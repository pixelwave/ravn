// Package version is the agent release stamped into the binary.
package version

// Version is the public agent release. Release builds may override it with -ldflags.
var Version = "2026.48.1"

// API is the HTTP API generation advertised to Home Assistant.
const API = 1
