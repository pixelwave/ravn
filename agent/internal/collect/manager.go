// Package collect samples Linux metrics. Disabled sensors do not run a timer.
// The machine name is read at start and when /etc/hostname changes. Addresses are read on demand.
package collect

import (
	"context"
	"log/slog"
	"os"
	"sync"
	"time"

	"github.com/fsnotify/fsnotify"

	"ravn/internal/config"
)

// Manager owns sensor loops and the cached snapshot served by HTTP.
type Manager struct {
	mu       sync.Mutex
	cond     *sync.Cond
	gen      int
	cfg      *config.File
	dir      string
	paths    Paths
	readings map[string]Reading
	lastErr  map[string]string
	name     string
	cpu      *cpuTemp
	disk     *diskWrite
	cpuUse   *cpuPercent
	mem      *memoryPercent
	load     *loadAvg
	fan      *fans
}

// NewManager builds collectors. cfg is kept and updated in place.
func NewManager(dir string, cfg *config.File, paths Paths) *Manager {
	paths = paths.withDefaults()
	m := &Manager{
		cfg:      cfg,
		dir:      dir,
		paths:    paths,
		readings: map[string]Reading{},
		lastErr:  map[string]string{},
		cpu:      &cpuTemp{root: paths.Hwmon},
		disk:     &diskWrite{paths: paths},
		cpuUse:   &cpuPercent{path: paths.ProcStat},
		mem:      &memoryPercent{path: paths.Meminfo},
		load:     &loadAvg{path: paths.Loadavg},
		fan:      &fans{root: paths.Hwmon},
	}
	m.cond = sync.NewCond(&m.mu)
	m.cpu.prepare()
	m.fan.rescan()
	m.name = readHostname()
	return m
}

// Start launches one loop per controllable sensor and the hostname watch.
func (m *Manager) Start(ctx context.Context) {
	go m.watchHostname(ctx)
	go m.run(ctx, m.cpu.id(), m.cpu.sample)
	go m.run(ctx, m.disk.id(), m.disk.sample)
	go m.run(ctx, m.cpuUse.id(), m.cpuUse.sample)
	go m.run(ctx, m.mem.id(), m.mem.sample)
	go m.run(ctx, m.load.id(), m.load.sample)
	go m.run(ctx, m.fan.id(), m.fan.sample)
}

// Hostname returns the cached machine name.
func (m *Manager) Hostname() string {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.name
}

// IP reads current addresses. It does not start a timer.
func (m *Manager) IP() IPInfo {
	return ReadIP(m.paths.Route)
}

// Catalog lists every sensor. Name and IP are always on and have no interval control.
func (m *Manager) Catalog() []Meta {
	m.mu.Lock()
	defer m.mu.Unlock()
	name := Meta{ID: "name", Name: "Machine name", Icon: "mdi:desktop-classic", Enabled: true, Available: m.name != "", Precision: 0}
	ip := Meta{ID: "ip", Name: "IP address", Icon: "mdi:ip-network", Enabled: true, Available: true, Precision: 0}
	cpu := m.cpu.meta(m.cpu.path != "")
	disk := m.disk.meta()
	cpuUse := m.cpuUse.meta()
	mem := m.mem.meta()
	load := m.load.meta()
	fans := m.fan.meta()
	out := []Meta{name, ip, cpu, disk, cpuUse, mem, load, fans}
	for i := range out {
		if !out[i].Controllable {
			continue
		}
		setting := m.cfg.Sensors[out[i].ID]
		out[i].Enabled = setting.Enabled
		out[i].IntervalS = setting.IntervalS
	}
	return out
}

// Readings returns cached samples for enabled sensors, plus the machine name.
func (m *Manager) Readings() map[string]Reading {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make(map[string]Reading, len(m.readings)+1)
	for id, reading := range m.readings {
		out[id] = reading
	}
	out["name"] = Reading{Text: m.name, Available: m.name != "", At: time.Now().UTC()}
	return out
}

// SetSensor persists a toggle or interval and wakes the matching loop.
func (m *Manager) SetSensor(id string, enabled bool, intervalS int) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.cfg.Set(id, enabled, intervalS); err != nil {
		return err
	}
	if err := config.Save(m.dir, m.cfg); err != nil {
		return err
	}
	if !enabled {
		delete(m.readings, id)
		if id == config.SensorFans {
			for _, fan := range m.fan.members {
				delete(m.readings, fan.ID)
			}
		}
	}
	m.kick()
	return nil
}

func (m *Manager) run(ctx context.Context, id string, sample func() (map[string]Reading, error)) {
	for {
		if ctx.Err() != nil {
			return
		}
		enabled, interval := m.setting(id)
		if !enabled {
			if !m.sleep(ctx, 0) {
				return
			}
			continue
		}
		func() {
			defer func() {
				if rec := recover(); rec != nil {
					slog.Error("sensor panic", "sensor", id, "err", rec)
				}
			}()
			values, err := sample()
			m.store(id, values, err)
		}()
		if !m.sleep(ctx, time.Duration(interval)*time.Second) {
			return
		}
	}
}

func (m *Manager) store(id string, values map[string]Reading, err error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	msg := ""
	if err != nil {
		msg = err.Error()
	}
	if msg != m.lastErr[id] {
		if msg != "" {
			slog.Warn("sensor sample failed", "sensor", id, "err", msg)
		}
		m.lastErr[id] = msg
	}
	if values == nil {
		return
	}
	if id == config.SensorFans {
		keep := map[string]bool{}
		for fid := range values {
			keep[fid] = true
		}
		for fid := range m.readings {
			if len(fid) > 4 && fid[:4] == "fan_" && !keep[fid] {
				delete(m.readings, fid)
			}
		}
	}
	for fid, reading := range values {
		m.readings[fid] = reading
	}
}

func (m *Manager) setting(id string) (bool, int) {
	m.mu.Lock()
	defer m.mu.Unlock()
	s := m.cfg.Sensors[id]
	return s.Enabled, s.IntervalS
}

// sleep returns false when ctx is cancelled. A zero duration waits until settings change.
func (m *Manager) sleep(ctx context.Context, d time.Duration) bool {
	sleepCtx, cancel := context.WithCancel(ctx)
	defer func() {
		cancel()
		m.mu.Lock()
		m.cond.Broadcast()
		m.mu.Unlock()
	}()

	m.mu.Lock()
	start := m.gen
	m.mu.Unlock()

	woken := make(chan struct{}, 1)
	go func() {
		m.mu.Lock()
		defer m.mu.Unlock()
		for m.gen == start && sleepCtx.Err() == nil {
			m.cond.Wait()
		}
		if m.gen != start {
			select {
			case woken <- struct{}{}:
			default:
			}
		}
	}()

	var timer <-chan time.Time
	if d > 0 {
		t := time.NewTimer(d)
		defer t.Stop()
		timer = t.C
	}
	select {
	case <-ctx.Done():
		return false
	case <-woken:
		return true
	case <-timer:
		return true
	}
}

func (m *Manager) kick() {
	m.gen++
	m.cond.Broadcast()
}

func (m *Manager) watchHostname(ctx context.Context) {
	w, err := fsnotify.NewWatcher()
	if err != nil {
		slog.Warn("hostname watch unavailable", "err", err)
		return
	}
	defer w.Close()
	if err := w.Add(m.paths.Hostname); err != nil {
		slog.Warn("hostname watch unavailable", "err", err, "path", m.paths.Hostname)
		return
	}
	for {
		select {
		case <-ctx.Done():
			return
		case <-w.Events:
			m.mu.Lock()
			m.name = readHostname()
			m.mu.Unlock()
		case <-w.Errors:
			return
		}
	}
}

func readHostname() string {
	name, err := os.Hostname()
	if err != nil {
		return ""
	}
	return name
}
