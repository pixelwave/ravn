package api

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"ravn/internal/collect"
	"ravn/internal/config"
	"ravn/internal/update"
)

func TestAuthCatalogAndToggle(t *testing.T) {
	dir := t.TempDir()
	cfg, err := config.Load(dir)
	if err != nil {
		t.Fatal(err)
	}
	hw := t.TempDir()
	proc := filepath.Join(dir, "stat")
	os.WriteFile(proc, []byte("cpu  1 0 1 10 0 0 0 0\n"), 0o644)
	mem := filepath.Join(dir, "mem")
	os.WriteFile(mem, []byte("MemTotal: 1000 kB\nMemAvailable: 400 kB\n"), 0o644)
	mgr := collect.NewManager(dir, cfg, collect.Paths{Hwmon: hw, ProcStat: proc, Meminfo: mem, Hostname: filepath.Join(dir, "missing-host")})
	h := New(Deps{Token: "secret", NodeID: "node-1", CertFP: "abc", Collect: mgr, Updates: update.New("", "", dir)})

	unauth := httptest.NewRecorder()
	h.ServeHTTP(unauth, httptest.NewRequest(http.MethodGet, "/v1/catalog", nil))
	if unauth.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d", unauth.Code)
	}

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/v1/catalog", nil)
	req.Header.Set("Authorization", "Bearer secret")
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("catalog status = %d body=%s", rec.Code, rec.Body.String())
	}
	if rec.Header().Get("X-Ravn-Api") != "1" {
		t.Fatal("missing api header")
	}
	var catalog struct {
		Sensors []struct {
			ID      string `json:"id"`
			Enabled bool   `json:"enabled"`
		} `json:"sensors"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &catalog); err != nil {
		t.Fatal(err)
	}
	enabled := map[string]bool{}
	for _, s := range catalog.Sensors {
		enabled[s.ID] = s.Enabled
	}
	if !enabled["name"] || !enabled["ip"] || enabled["cpu_temp"] || enabled["disk_write_bps"] || enabled["load"] {
		t.Fatalf("defaults = %#v", enabled)
	}

	body := bytes.NewBufferString(`{"enabled":true,"interval_s":12}`)
	put := httptest.NewRecorder()
	preq := httptest.NewRequest(http.MethodPut, "/v1/sensors/cpu_percent", body)
	preq.Header.Set("Authorization", "Bearer secret")
	h.ServeHTTP(put, preq)
	if put.Code != http.StatusOK {
		t.Fatalf("put status = %d %s", put.Code, put.Body.String())
	}
	raw, err := os.ReadFile(filepath.Join(dir, "config.json"))
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(raw, []byte(`"interval_s": 12`)) {
		t.Fatalf("config = %s", raw)
	}

	bad := httptest.NewRecorder()
	breq := httptest.NewRequest(http.MethodPut, "/v1/sensors/cpu_percent", bytes.NewBufferString(`{"enabled":true,"interval_s":0}`))
	breq.Header.Set("Authorization", "Bearer secret")
	h.ServeHTTP(bad, breq)
	if bad.Code != http.StatusBadRequest {
		t.Fatalf("bad interval status = %d", bad.Code)
	}
}
