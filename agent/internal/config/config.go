// Package config stores sensor on/off settings. It does not store secrets.
package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

const (
	SensorCPUTemp   = "cpu_temp"
	SensorDiskWrite = "disk_write_bps"
	SensorCPU       = "cpu_percent"
	SensorMemory    = "memory_percent"
	SensorLoad      = "load"
	SensorFans      = "fans"
)

const (
	MinInterval = 1
	MaxInterval = 86400
)

// Setting is one controllable sensor. Disabled sensors are not sampled.
type Setting struct {
	Enabled   bool `json:"enabled"`
	IntervalS int  `json:"interval_s"`
}

// DefaultRepo is the public release repository. Set GitHubRepo to "off" to disable checks.
const DefaultRepo = "pixelwave/ravn-agent"

// Update is where an agent upgrade looks for a release.
// The agent never accepts a URL from a request.
type Update struct {
	GitHubRepo string `json:"github_repo,omitempty"`
	APIBase    string `json:"api_base,omitempty"`
}

// Repo returns the repository to check. An empty value uses DefaultRepo. "off" disables updates.
func (u Update) Repo() string {
	switch u.GitHubRepo {
	case "off":
		return ""
	case "":
		return DefaultRepo
	default:
		return u.GitHubRepo
	}
}

// File is the on-disk configuration.
type File struct {
	Listen  string             `json:"listen"`
	Sensors map[string]Setting `json:"sensors"`
	Update  Update             `json:"update"`
}

// Defaults are the sensors a new node ships with. All of them are off.
func Defaults() map[string]Setting {
	return map[string]Setting{
		SensorCPUTemp:   {Enabled: false, IntervalS: 30},
		SensorDiskWrite: {Enabled: false, IntervalS: 10},
		SensorCPU:       {Enabled: false, IntervalS: 15},
		SensorMemory:    {Enabled: false, IntervalS: 30},
		SensorLoad:      {Enabled: false, IntervalS: 15},
		SensorFans:      {Enabled: false, IntervalS: 30},
	}
}

// Load reads config.json from dir, fills in missing sensors, and drops unknown ones
// so a removed sensor does not linger in the file.
func Load(dir string) (*File, error) {
	path := filepath.Join(dir, "config.json")
	cfg := &File{Listen: ":8770", Sensors: Defaults()}
	raw, err := os.ReadFile(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return cfg, nil
		}
		return nil, err
	}
	var parsed File
	if err := json.Unmarshal(raw, &parsed); err != nil {
		return nil, fmt.Errorf("config.json: %w", err)
	}
	if parsed.Listen != "" {
		cfg.Listen = parsed.Listen
	}
	cfg.Update = parsed.Update
	for id, setting := range parsed.Sensors {
		def, ok := cfg.Sensors[id]
		if !ok {
			continue
		}
		if setting.IntervalS == 0 {
			setting.IntervalS = def.IntervalS
		}
		if err := validate(setting); err != nil {
			return nil, fmt.Errorf("sensor %s: %w", id, err)
		}
		cfg.Sensors[id] = setting
	}
	return cfg, nil
}

// Save writes the file atomically.
func Save(dir string, cfg *File) error {
	if err := os.MkdirAll(dir, 0o750); err != nil {
		return err
	}
	raw, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	raw = append(raw, '\n')
	path := filepath.Join(dir, "config.json")
	tmp := path + ".partial"
	if err := os.WriteFile(tmp, raw, 0o640); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

// Set updates one sensor and rejects unknown ids and intervals outside 1–86400.
func (f *File) Set(id string, enabled bool, intervalS int) error {
	if _, ok := f.Sensors[id]; !ok {
		return fmt.Errorf("unknown sensor")
	}
	next := Setting{Enabled: enabled, IntervalS: intervalS}
	if err := validate(next); err != nil {
		return err
	}
	f.Sensors[id] = next
	return nil
}

func validate(s Setting) error {
	if s.IntervalS < MinInterval || s.IntervalS > MaxInterval {
		return fmt.Errorf("interval must be %d-%d seconds", MinInterval, MaxInterval)
	}
	return nil
}
