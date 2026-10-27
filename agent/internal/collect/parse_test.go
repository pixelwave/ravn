package collect

import (
	"testing"
	"time"
)

func TestParseLoadavg(t *testing.T) {
	one, five, fifteen, err := ParseLoadavg("1.29 0.72 0.55 1/201 12345\n")
	if err != nil {
		t.Fatal(err)
	}
	if one != 1.29 || five != 0.72 || fifteen != 0.55 {
		t.Fatalf("got %v %v %v", one, five, fifteen)
	}
	if _, _, _, err := ParseLoadavg("nope"); err == nil {
		t.Fatal("expected error")
	}
}

func TestParseProcStatAndPercent(t *testing.T) {
	prev, err := ParseProcStat("cpu  100 0 50 800 50 0 0 0 0 0\ncpu0 1 0 0 1 0 0 0 0\n")
	if err != nil {
		t.Fatal(err)
	}
	cur, err := ParseProcStat("cpu  180 0 70 840 60 0 0 0 0 0\n")
	if err != nil {
		t.Fatal(err)
	}
	pct, ok := CPUPercent(prev, cur)
	if !ok {
		t.Fatal("expected a rate")
	}
	// deltas: busy 100, total 150 -> 66.666%
	if pct < 66 || pct > 67 {
		t.Fatalf("percent = %v", pct)
	}
	if _, ok := CPUPercent(cur, cur); ok {
		t.Fatal("same sample must not produce a rate")
	}
}

func TestParseMeminfo(t *testing.T) {
	pct, err := ParseMeminfo("MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 250 kB\n")
	if err != nil {
		t.Fatal(err)
	}
	if pct != 75 {
		t.Fatalf("percent = %v", pct)
	}
}

func TestDiskParsers(t *testing.T) {
	if IsWholeDisk("sda1") || IsWholeDisk("nvme0n1p1") || IsWholeDisk("loop0") || IsWholeDisk("zd0") {
		t.Fatal("partitions and virtual devices must be excluded")
	}
	if !IsWholeDisk("sda") || !IsWholeDisk("nvme0n1") || !IsWholeDisk("vda") || !IsWholeDisk("mmcblk0") {
		t.Fatal("whole disks must be included")
	}
	sectors, err := ParseDiskstats("   8 0 sda 1 0 2 0 3 0 10 0 0 0 0 0 0 0 0\n   8 1 sda1 9 0 9 0 9 0 99 0 0 0 0 0 0 0 0\n   7 0 loop0 0 0 0 0 1 0 50 0 0 0 0 0 0 0 0\n")
	if err != nil {
		t.Fatal(err)
	}
	if sectors != 10 {
		t.Fatalf("sectors = %d", sectors)
	}
	wbytes, err := ParseIOStat("259:7 rbytes=10 wbytes=40 rios=1 wios=1\n259:9 rbytes=1 wbytes=2 rios=1 wios=1\n")
	if err != nil {
		t.Fatal(err)
	}
	if wbytes != 42 {
		t.Fatalf("wbytes = %d", wbytes)
	}
	if !UseCgroupIO("lxc") || UseCgroupIO("") || UseCgroupIO("none") {
		t.Fatal("cgroup selection")
	}
}

func TestRate(t *testing.T) {
	var r rate
	if _, ok := r.advance(100, time.Unix(1, 0)); ok {
		t.Fatal("first sample has no rate")
	}
	got, ok := r.advance(150, time.Unix(6, 0))
	if !ok || got != 10 {
		t.Fatalf("rate = %v ok=%v", got, ok)
	}
}

func TestParseDefaultRoute(t *testing.T) {
	text := "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\neth0 00000000 0101A8C0 0003 0 0 100 00000000 0 0 0\n"
	if got := ParseDefaultRoute(text); got != "eth0" {
		t.Fatalf("iface = %q", got)
	}
}
