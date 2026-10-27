// Package version is the agent release stamped into the binary.
package version

// Version is the installed agent release: YY.MM.DD, or YY.MM.DD.N for a
// later build on the same day. Release builds may override it with -ldflags.
var Version = "26.10.27"

// API is the HTTP API generation advertised to Home Assistant.
const API = 1
