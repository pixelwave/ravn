package collect

import (
	"os"
	"path/filepath"
	"testing"
)

func TestFindCPUTempPrefersTctl(t *testing.T) {
	root := t.TempDir()
	nvme := filepath.Join(root, "hwmon0")
	k10 := filepath.Join(root, "hwmon1")
	os.MkdirAll(nvme, 0o755)
	os.MkdirAll(k10, 0o755)
	os.WriteFile(filepath.Join(nvme, "name"), []byte("nvme\n"), 0o644)
	os.WriteFile(filepath.Join(nvme, "temp1_input"), []byte("40000\n"), 0o644)
	os.WriteFile(filepath.Join(k10, "name"), []byte("k10temp\n"), 0o644)
	os.WriteFile(filepath.Join(k10, "temp1_input"), []byte("49125\n"), 0o644)
	os.WriteFile(filepath.Join(k10, "temp1_label"), []byte("Tctl\n"), 0o644)
	os.WriteFile(filepath.Join(k10, "temp3_input"), []byte("48000\n"), 0o644)
	os.WriteFile(filepath.Join(k10, "temp3_label"), []byte("Tccd1\n"), 0o644)

	path, err := FindCPUTemp(root)
	if err != nil {
		t.Fatal(err)
	}
	if filepath.Base(filepath.Dir(path)) != "hwmon1" || filepath.Base(path) != "temp1_input" {
		t.Fatalf("path = %s", path)
	}
	c, err := ReadMilliC(path)
	if err != nil || c != 49.125 {
		t.Fatalf("temp = %v err=%v", c, err)
	}
}

func TestFindFans(t *testing.T) {
	root := t.TempDir()
	chip := filepath.Join(root, "hwmon6")
	os.MkdirAll(chip, 0o755)
	os.WriteFile(filepath.Join(chip, "name"), []byte("nct6779\n"), 0o644)
	os.WriteFile(filepath.Join(chip, "fan1_input"), []byte("1200\n"), 0o644)
	os.WriteFile(filepath.Join(chip, "fan3_target"), []byte("0\n"), 0o644)
	fans, err := FindFans(root)
	if err != nil {
		t.Fatal(err)
	}
	if len(fans) != 1 || fans[0].ID != "fan_nct6779_hwmon6_1" {
		t.Fatalf("fans = %#v", fans)
	}
}
