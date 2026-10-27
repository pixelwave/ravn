package collect

import (
	"bufio"
	"fmt"
	"strconv"
	"strings"
	"time"
	"unicode"
)

// rate tracks a monotonic counter and returns units per second.
type rate struct {
	prev  uint64
	at    time.Time
	ready bool
}

func (r *rate) advance(total uint64, now time.Time) (float64, bool) {
	defer func() {
		r.prev = total
		r.at = now
		r.ready = true
	}()
	if !r.ready || !now.After(r.at) || total < r.prev {
		return 0, false
	}
	dt := now.Sub(r.at).Seconds()
	if dt <= 0 {
		return 0, false
	}
	return float64(total-r.prev) / dt, true
}

// ParseLoadavg reads the 1, 5, and 15 minute averages from /proc/loadavg.
func ParseLoadavg(text string) (one, five, fifteen float64, err error) {
	fields := strings.Fields(text)
	if len(fields) < 3 {
		return 0, 0, 0, fmt.Errorf("loadavg")
	}
	nums := [3]float64{}
	for i := 0; i < 3; i++ {
		nums[i], err = strconv.ParseFloat(fields[i], 64)
		if err != nil {
			return 0, 0, 0, err
		}
	}
	return nums[0], nums[1], nums[2], nil
}

// CPUTimes is the aggregate CPU line from /proc/stat.
type CPUTimes struct {
	Busy  uint64
	Total uint64
}

// ParseProcStat reads the first "cpu " line. Busy excludes idle and iowait.
func ParseProcStat(text string) (CPUTimes, error) {
	for _, line := range strings.Split(text, "\n") {
		if !strings.HasPrefix(line, "cpu ") {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 5 {
			return CPUTimes{}, fmt.Errorf("short cpu line")
		}
		var nums []uint64
		for _, f := range fields[1:] {
			n, err := strconv.ParseUint(f, 10, 64)
			if err != nil {
				return CPUTimes{}, err
			}
			nums = append(nums, n)
		}
		var total uint64
		for i := 0; i < len(nums) && i < 8; i++ {
			total += nums[i]
		}
		idle := nums[3]
		var iowait uint64
		if len(nums) > 4 {
			iowait = nums[4]
		}
		return CPUTimes{Busy: total - idle - iowait, Total: total}, nil
	}
	return CPUTimes{}, fmt.Errorf("cpu line missing")
}

// CPUPercent is the busy share between two samples.
func CPUPercent(prev, cur CPUTimes) (float64, bool) {
	if cur.Total <= prev.Total {
		return 0, false
	}
	return 100 * float64(cur.Busy-prev.Busy) / float64(cur.Total-prev.Total), true
}

// ParseMeminfo returns used percent from MemTotal and MemAvailable.
func ParseMeminfo(text string) (float64, error) {
	var total, available uint64
	var haveTotal, haveAvail bool
	sc := bufio.NewScanner(strings.NewReader(text))
	for sc.Scan() {
		key, val, ok := strings.Cut(sc.Text(), ":")
		if !ok {
			continue
		}
		fields := strings.Fields(val)
		if len(fields) == 0 {
			continue
		}
		n, err := strconv.ParseUint(fields[0], 10, 64)
		if err != nil {
			continue
		}
		switch key {
		case "MemTotal":
			total, haveTotal = n, true
		case "MemAvailable":
			available, haveAvail = n, true
		}
	}
	if !haveTotal || !haveAvail || total == 0 {
		return 0, fmt.Errorf("meminfo missing MemTotal or MemAvailable")
	}
	if available > total {
		available = total
	}
	return 100 * float64(total-available) / float64(total), nil
}

// IsWholeDisk reports physical whole disks. Partitions, loop, and virtual devices are skipped.
func IsWholeDisk(name string) bool {
	switch {
	case strings.HasPrefix(name, "loop"),
		strings.HasPrefix(name, "ram"),
		strings.HasPrefix(name, "fd"),
		strings.HasPrefix(name, "sr"),
		strings.HasPrefix(name, "zd"),
		strings.HasPrefix(name, "dm-"),
		strings.HasPrefix(name, "zram"),
		strings.HasPrefix(name, "nbd"),
		name == "":
		return false
	}
	okPrefix := strings.HasPrefix(name, "sd") ||
		strings.HasPrefix(name, "vd") ||
		strings.HasPrefix(name, "hd") ||
		strings.HasPrefix(name, "xvd") ||
		strings.HasPrefix(name, "nvme") ||
		strings.HasPrefix(name, "mmcblk")
	if !okPrefix {
		return false
	}
	if strings.HasPrefix(name, "nvme") || strings.HasPrefix(name, "mmcblk") {
		return !strings.Contains(name, "p")
	}
	return !unicode.IsDigit(rune(name[len(name)-1]))
}

// ParseDiskstats sums sectors written on whole disks.
func ParseDiskstats(text string) (uint64, error) {
	var sectors uint64
	var found bool
	for _, line := range strings.Split(text, "\n") {
		fields := strings.Fields(line)
		if len(fields) < 10 {
			continue
		}
		name := fields[2]
		if !IsWholeDisk(name) {
			continue
		}
		n, err := strconv.ParseUint(fields[9], 10, 64)
		if err != nil {
			return 0, err
		}
		sectors += n
		found = true
	}
	if !found {
		return 0, fmt.Errorf("no whole disks in diskstats")
	}
	return sectors, nil
}

// ParseIOStat sums wbytes from a cgroup v2 io.stat file.
func ParseIOStat(text string) (uint64, error) {
	var total uint64
	var found bool
	for _, line := range strings.Split(text, "\n") {
		fields := strings.Fields(line)
		if len(fields) < 2 {
			continue
		}
		for _, f := range fields[1:] {
			key, val, ok := strings.Cut(f, "=")
			if !ok || key != "wbytes" {
				continue
			}
			n, err := strconv.ParseUint(val, 10, 64)
			if err != nil {
				return 0, err
			}
			total += n
			found = true
		}
	}
	if !found {
		return 0, fmt.Errorf("io.stat has no wbytes")
	}
	return total, nil
}

// ParseDefaultRoute returns the interface of the IPv4 default route.
func ParseDefaultRoute(text string) string {
	sc := bufio.NewScanner(strings.NewReader(text))
	first := true
	for sc.Scan() {
		fields := strings.Fields(sc.Text())
		if len(fields) < 2 {
			continue
		}
		if first {
			first = false
			continue
		}
		if fields[1] == "00000000" {
			return fields[0]
		}
	}
	return ""
}

// UseCgroupIO is true when the process is inside a container whose /proc/diskstats
// would describe the host instead of this machine's own writes.
func UseCgroupIO(containerMarker string) bool {
	switch strings.TrimSpace(containerMarker) {
	case "lxc", "docker", "podman", "containerd", "systemd-nspawn":
		return true
	default:
		return false
	}
}
