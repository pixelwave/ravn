// Package update downloads a checksum-verified binary into the state directory
// and asks the root helper to install it. The helper removes the staged file.
package update

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"sync"
	"time"

	"ravn/internal/version"
)

const maxBytes = 32 << 20

var repoPattern = regexp.MustCompile(`^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$`)

// Info is the cached release lookup.
type Info struct {
	Installed  string    `json:"installed"`
	Latest     string    `json:"latest"`
	Notes      string    `json:"notes"`
	CheckedAt  time.Time `json:"checked_at,omitempty"`
	Configured bool      `json:"configured"`
}

// Updater talks to a GitHub release, or a test server with the same API shape.
type Updater struct {
	mu         sync.Mutex
	repo       string
	apiBase    string
	stateDir   string
	http       *http.Client
	info       Info
	inProgress bool
	// now and lookPath are swapped in tests.
	now      func() time.Time
	runApply func(ctx context.Context) error
}

// New builds an updater. An empty repo leaves updates disabled.
func New(repo, apiBase, stateDir string) *Updater {
	if apiBase == "" {
		apiBase = "https://api.github.com"
	}
	u := &Updater{
		repo:     repo,
		apiBase:  strings.TrimRight(apiBase, "/"),
		stateDir: stateDir,
		now:      time.Now,
	}
	u.http = &http.Client{Timeout: 2 * time.Minute, CheckRedirect: u.checkRedirect}
	u.runApply = u.applyWithSystemd
	u.info.Installed = version.Version
	u.info.Configured = repo != ""
	return u
}

// Clean removes staged binaries and partial downloads.
func Clean(stateDir string) {
	removeMatching(stateDir, true)
}

// CleanPartials removes incomplete downloads and leaves a finished staged binary for the installer.
func CleanPartials(stateDir string) {
	removeMatching(stateDir, false)
}

func removeMatching(stateDir string, includeStaged bool) {
	entries, err := os.ReadDir(stateDir)
	if err != nil {
		return
	}
	for _, entry := range entries {
		name := entry.Name()
		partial := strings.HasSuffix(name, ".partial") || strings.HasSuffix(name, ".download")
		if partial || (includeStaged && name == "staged") {
			os.Remove(filepath.Join(stateDir, name))
		}
	}
}

// Cached returns the last lookup without contacting the network.
func (u *Updater) Cached() Info {
	u.mu.Lock()
	defer u.mu.Unlock()
	u.info.Installed = version.Version
	u.info.Configured = u.repo != ""
	return u.info
}

// Check refreshes the latest release. It does not download the binary.
func (u *Updater) Check(ctx context.Context) (Info, error) {
	u.mu.Lock()
	defer u.mu.Unlock()
	u.info.Installed = version.Version
	u.info.Configured = u.repo != ""
	if u.repo == "" {
		return u.info, nil
	}
	if !repoPattern.MatchString(u.repo) {
		return u.info, errors.New("invalid release repository")
	}
	rel, err := u.fetchLatest(ctx)
	if err != nil {
		return u.info, err
	}
	u.info.CheckedAt = u.now()
	if !rel.hasAgent() {
		// An integration release has no agent binary. It is not an agent update.
		u.info.Latest = u.info.Installed
		u.info.Notes = ""
		return u.info, nil
	}
	u.info.Latest = strings.TrimPrefix(rel.TagName, "v")
	u.info.Notes = rel.Body
	return u.info, nil
}

func (rel release) hasAgent() bool {
	asset := assetName()
	hasBin, hasSum := false, false
	for _, item := range rel.Assets {
		switch item.Name {
		case asset:
			hasBin = true
		case "SHA256SUMS":
			hasSum = true
		}
	}
	return hasBin && hasSum
}

// Apply downloads the pinned asset, checks SHA256SUMS, stages it, and starts the installer.
// A downgrade is refused unless force is set. Staging files are removed on failure.
func (u *Updater) Apply(ctx context.Context, force bool) error {
	u.mu.Lock()
	if u.inProgress {
		u.mu.Unlock()
		return errors.New("update already running")
	}
	if u.repo == "" {
		u.mu.Unlock()
		return errors.New("updates not configured")
	}
	u.inProgress = true
	u.mu.Unlock()
	defer func() {
		u.mu.Lock()
		u.inProgress = false
		u.mu.Unlock()
	}()

	if _, err := u.Check(ctx); err != nil {
		return err
	}
	info := u.Cached()
	if info.Latest == "" {
		return errors.New("no release found")
	}
	if !force && !newer(info.Latest, info.Installed) {
		return errors.New("already up to date")
	}
	if err := os.MkdirAll(u.stateDir, 0o750); err != nil {
		return err
	}
	// Drop a previous abandoned stage and any partial download before writing a new one.
	Clean(u.stateDir)
	os.Remove(filepath.Join(u.stateDir, "staged"))

	rel, err := u.fetchLatest(ctx)
	if err != nil {
		return err
	}
	asset := assetName()
	binURL, sumURL := "", ""
	for _, item := range rel.Assets {
		switch item.Name {
		case asset:
			binURL = item.BrowserDownloadURL
		case "SHA256SUMS":
			sumURL = item.BrowserDownloadURL
		}
	}
	if binURL == "" || sumURL == "" {
		return fmt.Errorf("release is missing %s or SHA256SUMS", asset)
	}
	sums, err := u.downloadLimited(ctx, sumURL)
	if err != nil {
		return err
	}
	want, err := sumFor(string(sums), asset)
	if err != nil {
		return err
	}
	partial := filepath.Join(u.stateDir, "binary.partial")
	if err := u.downloadFile(ctx, binURL, partial); err != nil {
		os.Remove(partial)
		return err
	}
	got, err := fileSHA256(partial)
	if err != nil || got != want {
		os.Remove(partial)
		if err != nil {
			return err
		}
		return errors.New("checksum mismatch")
	}
	staged := filepath.Join(u.stateDir, "staged")
	if err := os.Rename(partial, staged); err != nil {
		os.Remove(partial)
		return err
	}
	if err := u.runApply(ctx); err != nil {
		os.Remove(staged)
		Clean(u.stateDir)
		return err
	}
	return nil
}

// applyWithSystemd waits for ravn-update.path to consume the staged file.
// Cancelling the request usually means the helper restarted this process, so the file is left alone.
func (u *Updater) applyWithSystemd(ctx context.Context) error {
	staged := filepath.Join(u.stateDir, "staged")
	deadline := time.NewTimer(20 * time.Second)
	defer deadline.Stop()
	tick := time.NewTicker(200 * time.Millisecond)
	defer tick.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-deadline.C:
			return errors.New("update helper did not install the staged binary")
		case <-tick.C:
			if _, err := os.Stat(staged); errors.Is(err, os.ErrNotExist) {
				return nil
			}
		}
	}
}

type release struct {
	TagName string `json:"tag_name"`
	Body    string `json:"body"`
	Assets  []struct {
		Name               string `json:"name"`
		BrowserDownloadURL string `json:"browser_download_url"`
	} `json:"assets"`
}

func (u *Updater) fetchLatest(ctx context.Context) (release, error) {
	endpoint := u.apiBase + "/repos/" + u.repo + "/releases/latest"
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return release{}, err
	}
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("User-Agent", "ravn-agent")
	res, err := u.http.Do(req)
	if err != nil {
		return release{}, err
	}
	defer res.Body.Close()
	body, err := io.ReadAll(io.LimitReader(res.Body, 1<<20))
	if err != nil {
		return release{}, err
	}
	if res.StatusCode != http.StatusOK {
		return release{}, fmt.Errorf("release lookup returned %d", res.StatusCode)
	}
	var rel release
	if err := json.Unmarshal(body, &rel); err != nil {
		return release{}, err
	}
	return rel, nil
}

func (u *Updater) downloadLimited(ctx context.Context, rawURL string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "ravn-agent")
	res, err := u.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer res.Body.Close()
	if res.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("download returned %d", res.StatusCode)
	}
	return io.ReadAll(io.LimitReader(res.Body, 1<<20))
}

func (u *Updater) downloadFile(ctx context.Context, rawURL, dest string) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return err
	}
	req.Header.Set("User-Agent", "ravn-agent")
	res, err := u.http.Do(req)
	if err != nil {
		return err
	}
	defer res.Body.Close()
	if res.StatusCode != http.StatusOK {
		return fmt.Errorf("download returned %d", res.StatusCode)
	}
	f, err := os.OpenFile(dest, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o640)
	if err != nil {
		return err
	}
	defer f.Close()
	n, err := io.Copy(f, io.LimitReader(res.Body, maxBytes+1))
	if err != nil {
		return err
	}
	if n > maxBytes {
		return errors.New("release asset is too large")
	}
	return nil
}

func (u *Updater) checkRedirect(req *http.Request, via []*http.Request) error {
	if len(via) >= 5 {
		return errors.New("too many redirects")
	}
	if !u.hostAllowed(req.URL) {
		return errors.New("redirect host refused")
	}
	return nil
}

func (u *Updater) hostAllowed(target *url.URL) bool {
	host := target.Hostname()
	base, err := url.Parse(u.apiBase)
	if err == nil && host == base.Hostname() {
		return true
	}
	switch host {
	case "api.github.com", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com", "github-releases.githubusercontent.com":
		return true
	default:
		return false
	}
}

func assetName() string {
	arch := runtime.GOARCH
	if arch == "arm" {
		arch = "armv7"
	}
	return "ravn_" + runtime.GOOS + "_" + arch
}

func sumFor(sums, name string) (string, error) {
	for _, line := range strings.Split(sums, "\n") {
		fields := strings.Fields(line)
		if len(fields) != 2 {
			continue
		}
		gotName := strings.TrimPrefix(fields[1], "*")
		if gotName == name {
			if len(fields[0]) != 64 {
				return "", errors.New("checksum has the wrong length")
			}
			return strings.ToLower(fields[0]), nil
		}
	}
	return "", errors.New("checksum not found for this binary")
}

func fileSHA256(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

func newer(latest, installed string) bool {
	return compare(latest, installed) > 0
}

func compare(a, b string) int {
	as := splitVer(a)
	bs := splitVer(b)
	for i := 0; i < len(as); i++ {
		if as[i] > bs[i] {
			return 1
		}
		if as[i] < bs[i] {
			return -1
		}
	}
	return 0
}

// splitVer reads YY.MM.DD or YY.MM.DD.N. The first release of a day has three
// parts and sorts as revision 1, so YY.MM.DD.2 is newer the same day.
func splitVer(v string) [4]int {
	v = strings.TrimPrefix(strings.TrimSpace(v), "v")
	var out [4]int
	out[3] = 1
	parts := strings.Split(v, ".")
	for i := 0; i < len(parts) && i < 3; i++ {
		out[i] = leadingInt(parts[i])
	}
	if len(parts) >= 4 {
		if n, ok := revision(parts[3]); ok {
			out[3] = n
		}
	}
	return out
}

func leadingInt(part string) int {
	n := 0
	for _, c := range part {
		if c < '0' || c > '9' {
			break
		}
		n = n*10 + int(c-'0')
	}
	return n
}

func revision(suffix string) (int, bool) {
	if suffix == "" {
		return 0, false
	}
	n := 0
	for _, c := range suffix {
		if c < '0' || c > '9' {
			return 0, false
		}
		n = n*10 + int(c-'0')
	}
	if n < 2 {
		return 0, false
	}
	return n, true
}
