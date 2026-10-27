package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/pem"
	"fmt"
	"net"
	"os"
	"os/exec"
	"runtime"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"ravn/internal/collect"
	"ravn/internal/config"
	"ravn/internal/version"
)

const banner = `
 ██████╗  █████╗ ██╗   ██╗███╗   ██╗
 ██╔══██╗██╔══██╗██║   ██║████╗  ██║
 ██████╔╝███████║██║   ██║██╔██╗ ██║
 ██╔══██╗██╔══██║╚██╗ ██╔╝██║╚██╗██║
 ██║  ██║██║  ██║ ╚████╔╝ ██║ ╚████║
 ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝  ╚═══╝
`

func printInstalled(configDir string) int {
	port := listenPortFrom(configDir)
	nodeID := strings.TrimSpace(readText(configDir + "/node_id"))
	token := strings.TrimSpace(readText(configDir + "/token"))
	fp := certificateFingerprint(configDir + "/tls.crt")
	ip := collect.ReadIP("/proc/net/route").Primary
	url := "https://<this-host>:" + port
	if ip != "" {
		url = "https://" + ip + ":" + port
	}
	fmt.Print(banner)
	fmt.Printf("\n RAVN Node Agent\n Lightweight monitoring · v%s\n\n", version.Version)
	fmt.Printf(" %s  Binary installed\n", mark(fileExists("/usr/local/bin/ravn")))
	fmt.Printf(" %s  Configuration initialized\n", mark(fileExists(configDir+"/config.json") && nodeID != ""))
	fmt.Printf(" %s  TLS certificate ready\n", mark(fileExists(configDir+"/tls.crt") && fileExists(configDir+"/tls.key")))
	fmt.Printf(" %s  Systemd service active\n", mark(serviceActive()))
	fmt.Printf(" %s  HTTPS API listening on port %s\n", mark(portOpen(port)), port)
	fmt.Print("\n ─────────────────────────────────────\n")
	fmt.Print(" INSTALLATION COMPLETE\n\n")
	fmt.Printf(" Node ID : %s\n", shortID(nodeID))
	fmt.Printf(" URL     : %s\n", url)
	if token != "" {
		fmt.Printf(" Token   : %s\n", token)
	}
	if fp != "" {
		fmt.Printf(" Fingerprint : %s\n", fp)
	}
	fmt.Print("\n Run 'ravn status' to inspect this node.\n")
	return 0
}

func printStatus(args []string) int {
	dir := configDir(args)
	host, _ := os.Hostname()
	if i := strings.IndexByte(host, '.'); i > 0 {
		host = host[:i]
	}
	ip := collect.ReadIP("/proc/net/route").Primary
	if ip == "" {
		ip = "n/a"
	}
	uptime := formatUptime(readUptime())
	loads := readLoad()
	used, total := readMemory()
	temp, tempOK := readTemp()

	memBar := bar(0)
	memText := "n/a"
	if total > 0 {
		memBar = bar(float64(used) / float64(total))
		pct := int(float64(used) * 100 / float64(total))
		memText = fmt.Sprintf("%dMB / %dMB (%d%%)", used/1024/1024, total/1024/1024, pct)
	}
	loadBar := bar(0)
	loadText := "n/a"
	if len(loads) == 3 {
		cpus := runtime.NumCPU()
		if cpus < 1 {
			cpus = 1
		}
		loadBar = bar(loads[0] / float64(cpus))
		loadText = fmt.Sprintf("%.2f  %.2f  %.2f", loads[0], loads[1], loads[2])
	}
	tempBar := bar(0)
	tempText := "n/a"
	if tempOK {
		tempBar = bar(temp / 100)
		tempText = fmt.Sprintf("%.1f°C", temp)
	}

	fmt.Print(banner)
	fmt.Printf("\n RAVN Node Agent\n Lightweight monitoring · v%s\n\n", version.Version)
	fmt.Printf(" %-10s: %s\n", "Host", host)
	fmt.Printf(" %-10s: %s\n", "IP", ip)
	fmt.Printf(" %-10s: %s\n", "Uptime", uptime)
	fmt.Printf(" %-10s: %s  %s\n", "Load", loadBar, loadText)
	fmt.Printf(" %-10s: %s  %s\n", "Memory", memBar, memText)
	fmt.Printf(" %-10s: %s  %s\n", "Temp", tempBar, tempText)
	if box := tokenBox(readText(dir + "/token")); box != "" {
		fmt.Print("\n")
		fmt.Print(box)
	}
	fmt.Print("\n * Documentation:  https://github.com/pixelwave/ravn\n")
	return 0
}

// tokenBox draws the API token between the readings and the documentation line.
// The frame grows with the token. An empty token prints nothing.
func tokenBox(token string) string {
	token = strings.TrimSpace(token)
	if token == "" {
		return ""
	}
	const leftPad, rightPad = 2, 4
	inner := strings.Repeat(" ", leftPad) + token + strings.Repeat(" ", rightPad)
	width := utf8.RuneCountInString(inner)
	label := "─ API Token "
	labelWidth := utf8.RuneCountInString(label)
	if width < labelWidth+1 {
		inner += strings.Repeat(" ", labelWidth+1-width)
		width = labelWidth + 1
	}
	return " ┌" + label + strings.Repeat("─", width-labelWidth) + "┐\n" +
		" │" + inner + "│\n" +
		" └" + strings.Repeat("─", width) + "┘\n"
}

func mark(ok bool) string {
	if ok {
		return "✓"
	}
	return "✗"
}

func bar(frac float64) string {
	if frac < 0 {
		frac = 0
	}
	if frac > 1 {
		frac = 1
	}
	n := int(frac * 20)
	return "[" + strings.Repeat("#", n) + strings.Repeat("-", 20-n) + "]"
}

func shortID(id string) string {
	compact := strings.ReplaceAll(strings.TrimSpace(id), "-", "")
	if len(compact) < 8 {
		if compact == "" {
			return "unknown"
		}
		return compact
	}
	return compact[:4] + "…" + compact[len(compact)-4:]
}

func formatUptime(d time.Duration) string {
	if d < 0 {
		d = 0
	}
	mins := int(d.Round(time.Minute) / time.Minute)
	units := []struct {
		name string
		size int
	}{
		{"week", 7 * 24 * 60},
		{"day", 24 * 60},
		{"hour", 60},
		{"minute", 1},
	}
	var parts []string
	for _, unit := range units {
		n := mins / unit.size
		mins %= unit.size
		if n == 0 {
			continue
		}
		label := unit.name
		if n != 1 {
			label += "s"
		}
		parts = append(parts, fmt.Sprintf("%d %s", n, label))
	}
	if len(parts) == 0 {
		return "0 minutes"
	}
	return strings.Join(parts, ", ")
}

func readUptime() time.Duration {
	text := strings.Fields(readText("/proc/uptime"))
	if len(text) == 0 {
		return 0
	}
	secs, err := strconv.ParseFloat(text[0], 64)
	if err != nil {
		return 0
	}
	return time.Duration(secs * float64(time.Second))
}

func readLoad() []float64 {
	fields := strings.Fields(readText("/proc/loadavg"))
	if len(fields) < 3 {
		return nil
	}
	out := make([]float64, 3)
	for i := 0; i < 3; i++ {
		n, err := strconv.ParseFloat(fields[i], 64)
		if err != nil {
			return nil
		}
		out[i] = n
	}
	return out
}

func readMemory() (used, total uint64) {
	var memTotal, memAvail uint64
	for _, line := range strings.Split(readText("/proc/meminfo"), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 2 {
			continue
		}
		n, err := strconv.ParseUint(fields[1], 10, 64)
		if err != nil {
			continue
		}
		switch fields[0] {
		case "MemTotal:":
			memTotal = n * 1024
		case "MemAvailable:":
			memAvail = n * 1024
		}
	}
	if memTotal == 0 || memAvail > memTotal {
		return 0, memTotal
	}
	return memTotal - memAvail, memTotal
}

func readTemp() (float64, bool) {
	path, err := collect.FindCPUTemp("/sys/class/hwmon")
	if err != nil {
		return 0, false
	}
	v, err := collect.ReadMilliC(path)
	if err != nil {
		return 0, false
	}
	return v, true
}

func serviceActive() bool {
	return exec.Command("systemctl", "is-active", "--quiet", "ravn.service").Run() == nil
}

func portOpen(port string) bool {
	conn, err := net.DialTimeout("tcp", "127.0.0.1:"+port, 500*time.Millisecond)
	if err != nil {
		return false
	}
	conn.Close()
	return true
}

func listenPortFrom(dir string) string {
	cfg, err := config.Load(dir)
	if err != nil || cfg.Listen == "" {
		return "8770"
	}
	listen := cfg.Listen
	if strings.HasPrefix(listen, ":") {
		return strings.TrimPrefix(listen, ":")
	}
	_, port, err := net.SplitHostPort(listen)
	if err != nil || port == "" {
		return "8770"
	}
	return port
}

func certificateFingerprint(path string) string {
	raw, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	block, _ := pem.Decode(raw)
	if block == nil {
		return ""
	}
	sum := sha256.Sum256(block.Bytes)
	return hex.EncodeToString(sum[:])
}

func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}

func readText(path string) string {
	raw, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return string(raw)
}
