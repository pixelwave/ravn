package collect

import (
	"fmt"
	"os"
	"time"

	"ravn/internal/config"
)

// Paths overrides Linux interfaces in tests. Empty fields use the real system.
type Paths struct {
	Hwmon     string
	Diskstats string
	IOStat    string
	Virt      string
	ProcStat  string
	Meminfo   string
	Route     string
	Hostname  string
	Loadavg   string
}

func (p Paths) withDefaults() Paths {
	if p.Hwmon == "" {
		p.Hwmon = "/sys/class/hwmon"
	}
	if p.Diskstats == "" {
		p.Diskstats = "/proc/diskstats"
	}
	if p.IOStat == "" {
		p.IOStat = "/sys/fs/cgroup/io.stat"
	}
	if p.Virt == "" {
		p.Virt = "/run/systemd/container"
	}
	if p.ProcStat == "" {
		p.ProcStat = "/proc/stat"
	}
	if p.Meminfo == "" {
		p.Meminfo = "/proc/meminfo"
	}
	if p.Route == "" {
		p.Route = "/proc/net/route"
	}
	if p.Hostname == "" {
		p.Hostname = "/etc/hostname"
	}
	if p.Loadavg == "" {
		p.Loadavg = "/proc/loadavg"
	}
	return p
}

// Meta describes a catalog entry.
type Meta struct {
	ID           string
	Name         string
	Unit         string
	DeviceClass  string
	StateClass   string
	Icon         string
	Controllable bool
	Precision    int
	Available    bool
	Enabled      bool
	IntervalS    int
	Members      []Meta
}

// Reading is a cached sample. Text is set for values that are not a single number.
type Reading struct {
	Value     *float64
	Text      string
	Attrs     map[string]float64
	Available bool
	At        time.Time
}

func fptr(v float64) *float64 { return &v }

type cpuTemp struct {
	root string
	path string
}

func (c *cpuTemp) meta(available bool) Meta {
	return Meta{
		ID: c.id(), Name: "CPU temperature", Unit: "°C", DeviceClass: "temperature",
		StateClass: "measurement", Icon: "mdi:thermometer", Controllable: true, Precision: 1, Available: available,
	}
}

func (c *cpuTemp) id() string { return config.SensorCPUTemp }

func (c *cpuTemp) prepare() {
	if c.path != "" {
		return
	}
	path, err := FindCPUTemp(c.root)
	if err == nil {
		c.path = path
	}
}

func (c *cpuTemp) sample() (map[string]Reading, error) {
	c.prepare()
	if c.path == "" {
		return map[string]Reading{c.id(): {Available: false}}, errString("cpu temperature sensor not found")
	}
	v, err := ReadMilliC(c.path)
	if err != nil {
		c.path = ""
		return map[string]Reading{c.id(): {Available: false}}, err
	}
	return map[string]Reading{c.id(): {Value: fptr(v), Available: true, At: time.Now().UTC()}}, nil
}

type diskWrite struct {
	paths Paths
	r     rate
}

func (d *diskWrite) id() string { return config.SensorDiskWrite }

func (d *diskWrite) meta() Meta {
	return Meta{
		ID: d.id(), Name: "Disk write rate", Unit: "B/s", DeviceClass: "data_rate",
		StateClass: "measurement", Icon: "mdi:harddisk", Controllable: true, Precision: 0, Available: true,
	}
}

func (d *diskWrite) sample() (map[string]Reading, error) {
	var total uint64
	var err error
	marker, _ := os.ReadFile(d.paths.Virt)
	if UseCgroupIO(string(marker)) {
		raw, readErr := os.ReadFile(d.paths.IOStat)
		if readErr != nil {
			return map[string]Reading{d.id(): {Available: false}}, readErr
		}
		total, err = ParseIOStat(string(raw))
	} else {
		raw, readErr := os.ReadFile(d.paths.Diskstats)
		if readErr != nil {
			return map[string]Reading{d.id(): {Available: false}}, readErr
		}
		sectors, parseErr := ParseDiskstats(string(raw))
		if parseErr != nil {
			return map[string]Reading{d.id(): {Available: false}}, parseErr
		}
		total = sectors * 512
		err = nil
	}
	if err != nil {
		return map[string]Reading{d.id(): {Available: false}}, err
	}
	per, ok := d.r.advance(total, time.Now())
	if !ok {
		return map[string]Reading{d.id(): {Available: false}}, nil
	}
	return map[string]Reading{d.id(): {Value: fptr(per), Available: true, At: time.Now().UTC()}}, nil
}

type cpuPercent struct {
	path string
	prev CPUTimes
	have bool
}

func (c *cpuPercent) id() string { return config.SensorCPU }

func (c *cpuPercent) meta() Meta {
	return Meta{
		ID: c.id(), Name: "CPU utilization", Unit: "%", StateClass: "measurement",
		Icon: "mdi:cpu-64-bit", Controllable: true, Precision: 1, Available: true,
	}
}

func (c *cpuPercent) sample() (map[string]Reading, error) {
	raw, err := os.ReadFile(c.path)
	if err != nil {
		return map[string]Reading{c.id(): {Available: false}}, err
	}
	cur, err := ParseProcStat(string(raw))
	if err != nil {
		return map[string]Reading{c.id(): {Available: false}}, err
	}
	if !c.have {
		c.prev = cur
		c.have = true
		return map[string]Reading{c.id(): {Available: false}}, nil
	}
	pct, ok := CPUPercent(c.prev, cur)
	c.prev = cur
	if !ok {
		return map[string]Reading{c.id(): {Available: false}}, nil
	}
	return map[string]Reading{c.id(): {Value: fptr(pct), Available: true, At: time.Now().UTC()}}, nil
}

type memoryPercent struct{ path string }

func (m *memoryPercent) id() string { return config.SensorMemory }

func (m *memoryPercent) meta() Meta {
	return Meta{
		ID: m.id(), Name: "Memory used", Unit: "%", StateClass: "measurement",
		Icon: "mdi:memory", Controllable: true, Precision: 1, Available: true,
	}
}

func (m *memoryPercent) sample() (map[string]Reading, error) {
	raw, err := os.ReadFile(m.path)
	if err != nil {
		return map[string]Reading{m.id(): {Available: false}}, err
	}
	pct, err := ParseMeminfo(string(raw))
	if err != nil {
		return map[string]Reading{m.id(): {Available: false}}, err
	}
	return map[string]Reading{m.id(): {Value: fptr(pct), Available: true, At: time.Now().UTC()}}, nil
}

type loadAvg struct{ path string }

func (l *loadAvg) id() string { return config.SensorLoad }

func (l *loadAvg) meta() Meta {
	return Meta{
		ID: l.id(), Name: "Load", Icon: "mdi:speedometer",
		Controllable: true, Available: true,
	}
}

func (l *loadAvg) sample() (map[string]Reading, error) {
	raw, err := os.ReadFile(l.path)
	if err != nil {
		return map[string]Reading{l.id(): {Available: false}}, err
	}
	one, five, fifteen, err := ParseLoadavg(string(raw))
	if err != nil {
		return map[string]Reading{l.id(): {Available: false}}, err
	}
	return map[string]Reading{l.id(): {
		Text:      fmt.Sprintf("%.2f  %.2f  %.2f", one, five, fifteen),
		Attrs:     map[string]float64{"load_1m": one, "load_5m": five, "load_15m": fifteen},
		Available: true,
		At:        time.Now().UTC(),
	}}, nil
}

type fans struct {
	root    string
	members []FanInput
}

func (f *fans) id() string { return config.SensorFans }

func (f *fans) rescan() {
	found, err := FindFans(f.root)
	if err != nil {
		return
	}
	f.members = found
}

func (f *fans) meta() Meta {
	m := Meta{
		ID: f.id(), Name: "Fans", Icon: "mdi:fan", Controllable: true, Precision: 0,
		Available: len(f.members) > 0, Unit: "rpm", StateClass: "measurement",
	}
	for _, fan := range f.members {
		m.Members = append(m.Members, Meta{
			ID: fan.ID, Name: fan.Name, Unit: "rpm", StateClass: "measurement",
			Icon: "mdi:fan", Precision: 0, Available: true,
		})
	}
	return m
}

func (f *fans) sample() (map[string]Reading, error) {
	f.rescan()
	out := map[string]Reading{}
	if len(f.members) == 0 {
		return out, errString("no fan sensors found")
	}
	var first error
	for _, fan := range f.members {
		rpm, err := ReadRPM(fan.Path)
		if err != nil {
			out[fan.ID] = Reading{Available: false}
			if first == nil {
				first = err
			}
			continue
		}
		out[fan.ID] = Reading{Value: fptr(rpm), Available: true, At: time.Now().UTC()}
	}
	return out, first
}

type errString string

func (e errString) Error() string { return string(e) }
