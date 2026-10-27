// Package api is the local HTTPS API. Every route requires the bearer token.
// Handlers only read cached metrics or write RAVN's own sensor settings.
package api

import (
	"crypto/subtle"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"time"

	"ravn/internal/collect"
	"ravn/internal/update"
	"ravn/internal/version"
)

// Deps is what the HTTP handlers need from the running agent.
type Deps struct {
	Token   string
	NodeID  string
	CertFP  string
	Collect *collect.Manager
	Updates *update.Updater
}

// New returns the authenticated API handler.
func New(d Deps) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /v1/identity", d.identity)
	mux.HandleFunc("GET /v1/catalog", d.catalog)
	mux.HandleFunc("GET /v1/readings", d.readings)
	mux.HandleFunc("PUT /v1/sensors/{id}", d.putSensor)
	mux.HandleFunc("GET /v1/update", d.getUpdate)
	mux.HandleFunc("POST /v1/update/check", d.checkUpdate)
	mux.HandleFunc("POST /v1/update", d.applyUpdate)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Ravn-Api", "1")
		if !authorized(r, d.Token) {
			writeErr(w, http.StatusUnauthorized, "unauthorized")
			return
		}
		mux.ServeHTTP(w, r)
	})
}

func authorized(r *http.Request, token string) bool {
	if token == "" {
		return false
	}
	got := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	if len(got) != len(token) {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(got), []byte(token)) == 1
}

func (d Deps) identity(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"node_id":     d.NodeID,
		"name":        d.Collect.Hostname(),
		"version":     version.Version,
		"api":         version.API,
		"fingerprint": d.CertFP,
		"ip":          d.Collect.IP(),
	})
}

func (d Deps) catalog(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"sensors": metaList(d.Collect.Catalog())})
}

func (d Deps) readings(w http.ResponseWriter, _ *http.Request) {
	cached := d.Updates.Cached()
	latest := cached.Latest
	writeJSON(w, http.StatusOK, map[string]any{
		"node_id":  d.NodeID,
		"name":     d.Collect.Hostname(),
		"version":  version.Version,
		"ip":       d.Collect.IP(),
		"catalog":  metaList(d.Collect.Catalog()),
		"readings": readingsJSON(d.Collect.Readings()),
		"update": map[string]any{
			"latest":     latest,
			"notes":      cached.Notes,
			"configured": cached.Configured,
		},
	})
}

func (d Deps) putSensor(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Enabled   bool `json:"enabled"`
		IntervalS int  `json:"interval_s"`
	}
	r.Body = http.MaxBytesReader(w, r.Body, 4096)
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeErr(w, http.StatusBadRequest, "invalid_json")
		return
	}
	id := r.PathValue("id")
	if err := d.Collect.SetSensor(id, body.Enabled, body.IntervalS); err != nil {
		writeErr(w, http.StatusBadRequest, "invalid_sensor")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"sensors": metaList(d.Collect.Catalog())})
}

func (d Deps) getUpdate(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, d.Updates.Cached())
}

func (d Deps) checkUpdate(w http.ResponseWriter, r *http.Request) {
	info, err := d.Updates.Check(r.Context())
	if err != nil {
		writeErr(w, http.StatusBadGateway, "update_check_failed")
		return
	}
	writeJSON(w, http.StatusOK, info)
}

func (d Deps) applyUpdate(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Force bool `json:"force"`
	}
	r.Body = http.MaxBytesReader(w, r.Body, 4096)
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil && !errors.Is(err, http.ErrBodyNotAllowed) {
		// An empty body is valid and means force=false.
		if err.Error() != "EOF" {
			writeErr(w, http.StatusBadRequest, "invalid_json")
			return
		}
	}
	if err := d.Updates.Apply(r.Context(), body.Force); err != nil {
		code := http.StatusConflict
		if err.Error() == "checksum mismatch" {
			code = http.StatusBadGateway
		}
		writeErr(w, code, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"status": "installed"})
}

func metaList(items []collect.Meta) []map[string]any {
	out := make([]map[string]any, 0, len(items))
	for _, item := range items {
		out = append(out, metaJSON(item))
	}
	return out
}

func metaJSON(item collect.Meta) map[string]any {
	obj := map[string]any{
		"id":           item.ID,
		"name":         item.Name,
		"controllable": item.Controllable,
		"enabled":      item.Enabled,
		"available":    item.Available,
		"precision":    item.Precision,
	}
	if item.Unit != "" {
		obj["unit"] = item.Unit
	}
	if item.DeviceClass != "" {
		obj["device_class"] = item.DeviceClass
	}
	if item.StateClass != "" {
		obj["state_class"] = item.StateClass
	}
	if item.Icon != "" {
		obj["icon"] = item.Icon
	}
	if item.Controllable {
		obj["interval_s"] = item.IntervalS
	}
	if len(item.Members) > 0 {
		members := make([]map[string]any, 0, len(item.Members))
		for _, member := range item.Members {
			members = append(members, metaJSON(member))
		}
		obj["members"] = members
	}
	return obj
}

func readingsJSON(readings map[string]collect.Reading) map[string]any {
	out := make(map[string]any, len(readings))
	for id, reading := range readings {
		item := map[string]any{"available": reading.Available}
		if reading.Value != nil {
			item["value"] = *reading.Value
		}
		if reading.Text != "" {
			item["text"] = reading.Text
		}
		if len(reading.Attrs) > 0 {
			item["attributes"] = reading.Attrs
		}
		if !reading.At.IsZero() {
			item["ts"] = reading.At.Format(time.RFC3339)
		}
		out[id] = item
	}
	return out
}

func writeJSON(w http.ResponseWriter, code int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	enc := json.NewEncoder(w)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(body)
}

func writeErr(w http.ResponseWriter, code int, msg string) {
	writeJSON(w, code, map[string]string{"error": msg})
}
