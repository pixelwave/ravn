package update

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"ravn/internal/version"
)

func TestApplyRejectsBadChecksumAndCleansUp(t *testing.T) {
	payload := []byte("not-the-agent")
	sum := sha256.Sum256([]byte("something-else"))
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/repos/acme/ravn/releases/latest":
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"tag_name":"v` + bump(version.Version) + `","body":"notes","assets":[` +
				`{"name":"` + assetName() + `","browser_download_url":"` + serverURL(r) + `/bin"},` +
				`{"name":"SHA256SUMS","browser_download_url":"` + serverURL(r) + `/sums"}]}`))
		case "/bin":
			_, _ = w.Write(payload)
		case "/sums":
			_, _ = w.Write([]byte(hex.EncodeToString(sum[:]) + "  " + assetName() + "\n"))
		default:
			http.NotFound(w, r)
		}
	}))
	defer srv.Close()

	dir := t.TempDir()
	u := New("acme/ravn", srv.URL, dir)
	u.runApply = func(context.Context) error { t.Fatal("apply must not run"); return nil }
	err := u.Apply(context.Background(), false)
	if err == nil || err.Error() != "checksum mismatch" {
		t.Fatalf("err = %v", err)
	}
	left, _ := os.ReadDir(dir)
	if len(left) != 0 {
		t.Fatalf("staging not cleaned: %v", names(left))
	}
}

func TestApplyStagesAndRemovesPartial(t *testing.T) {
	if testing.Short() {
	}
	payload := []byte("#!/bin/true\n")
	sum := sha256.Sum256(payload)
	var srv *httptest.Server
	srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/repos/acme/ravn/releases/latest":
			_, _ = w.Write([]byte(`{"tag_name":"v` + bump(version.Version) + `","body":"fix","assets":[` +
				`{"name":"` + assetName() + `","browser_download_url":"` + srv.URL + `/bin"},` +
				`{"name":"SHA256SUMS","browser_download_url":"` + srv.URL + `/sums"}]}`))
		case "/bin":
			_, _ = w.Write(payload)
		case "/sums":
			_, _ = w.Write([]byte(hex.EncodeToString(sum[:]) + "  " + assetName() + "\n"))
		default:
			http.NotFound(w, r)
		}
	}))
	defer srv.Close()

	dir := t.TempDir()
	u := New("acme/ravn", srv.URL, dir)
	applied := false
	u.runApply = func(context.Context) error {
		applied = true
		os.Remove(filepath.Join(dir, "staged"))
		Clean(dir)
		return nil
	}
	if err := u.Apply(context.Background(), false); err != nil {
		t.Fatal(err)
	}
	if !applied {
		t.Fatal("expected installer to run")
	}
	left, _ := os.ReadDir(dir)
	if len(left) != 0 {
		t.Fatalf("leftovers: %v", names(left))
	}
	info := u.Cached()
	if info.Notes != "fix" {
		t.Fatalf("notes = %q", info.Notes)
	}
}

func TestDisabledAndDowngrade(t *testing.T) {
	u := New("", "", t.TempDir())
	info, err := u.Check(context.Background())
	if err != nil || info.Configured {
		t.Fatalf("info=%+v err=%v", info, err)
	}
	if err := u.Apply(context.Background(), false); err == nil {
		t.Fatal("expected updates to be disabled")
	}
}

func serverURL(r *http.Request) string {
	return "http://" + r.Host
}

func names(entries []os.DirEntry) []string {
	out := make([]string, 0, len(entries))
	for _, e := range entries {
		out = append(out, e.Name())
	}
	return out
}

func TestNewerSameDayAndNextDay(t *testing.T) {
	if !newer("26.10.27.2", "26.10.27") {
		t.Fatal("same-day .2 should be newer than the first tag")
	}
	if !newer("26.10.27.3", "26.10.27.2") || !newer("26.10.27.10", "26.10.27.3") {
		t.Fatal("a higher same-day number should be newer")
	}
	if !newer("26.10.28", "26.10.27.10") {
		t.Fatal("the next day should be newer than a same-day number")
	}
	if newer("26.10.27", "26.10.27") || newer("26.10.27.2", "26.10.28") || newer("26.10.27-2", "26.10.27") {
		t.Fatal("equal, older, or hyphen tags should not count as newer")
	}
	if !newer("v26.10.27", "26.10.26") {
		t.Fatal("a later date should be newer")
	}
}

func bump(v string) string {
	parts := splitVer(v)
	parts[2]++
	return itoa(parts[0]) + "." + itoa(parts[1]) + "." + itoa(parts[2])
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b [8]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	return string(b[i:])
}
