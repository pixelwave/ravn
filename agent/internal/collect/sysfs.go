package collect

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// FindCPUTemp returns the hwmon input that best represents CPU temperature.
// Preferred chips are coretemp, k10temp, and cpu_thermal. NVMe and other chips are ignored.
func FindCPUTemp(hwmonRoot string) (string, error) {
	chips, err := os.ReadDir(hwmonRoot)
	if err != nil {
		return "", err
	}
	bestPath := ""
	bestRank := 100
	for _, chip := range chips {
		dir := filepath.Join(hwmonRoot, chip.Name())
		name := strings.TrimSpace(readText(filepath.Join(dir, "name")))
		if name != "coretemp" && name != "k10temp" && name != "cpu_thermal" {
			continue
		}
		inputs, _ := filepath.Glob(filepath.Join(dir, "temp*_input"))
		for _, input := range inputs {
			label := strings.TrimSpace(readText(strings.TrimSuffix(input, "_input") + "_label"))
			rank := 2
			switch label {
			case "Tctl", "Package id 0":
				rank = 0
			default:
				if strings.Contains(label, "CPU") || label == "Tdie" {
					rank = 1
				}
			}
			if rank < bestRank {
				bestRank = rank
				bestPath = input
			}
		}
	}
	if bestPath == "" {
		return "", fmt.Errorf("cpu temperature sensor not found")
	}
	return bestPath, nil
}

// ReadMilliC converts a sysfs millidegree file to degrees Celsius.
func ReadMilliC(path string) (float64, error) {
	text := strings.TrimSpace(readText(path))
	if text == "" {
		return 0, fmt.Errorf("empty temperature")
	}
	n, err := strconv.ParseFloat(text, 64)
	if err != nil {
		return 0, err
	}
	return n / 1000, nil
}

// FanInput is one hwmon fan tachometer.
type FanInput struct {
	ID   string
	Name string
	Path string
}

// FindFans lists fan*_input files. The id stays stable for a chip name and fan number.
func FindFans(hwmonRoot string) ([]FanInput, error) {
	chips, err := os.ReadDir(hwmonRoot)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	var fans []FanInput
	for _, chip := range chips {
		dir := filepath.Join(hwmonRoot, chip.Name())
		name := strings.TrimSpace(readText(filepath.Join(dir, "name")))
		if name == "" {
			name = chip.Name()
		}
		inputs, _ := filepath.Glob(filepath.Join(dir, "fan*_input"))
		for _, input := range inputs {
			base := filepath.Base(input)
			num := strings.TrimSuffix(strings.TrimPrefix(base, "fan"), "_input")
			fans = append(fans, FanInput{
				ID:   "fan_" + name + "_" + chip.Name() + "_" + num,
				Name: fmt.Sprintf("Fan %s %s", name, num),
				Path: input,
			})
		}
	}
	return fans, nil
}

func ReadRPM(path string) (float64, error) {
	text := strings.TrimSpace(readText(path))
	n, err := strconv.ParseFloat(text, 64)
	if err != nil {
		return 0, err
	}
	return n, nil
}

func readText(path string) string {
	b, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return string(b)
}
