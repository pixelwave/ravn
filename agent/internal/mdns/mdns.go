// Package mdns advertises the agent as _ravn._tcp. The TXT record has no token.
package mdns

import (
	"log/slog"
	"net"
	"os"
	"strings"

	"github.com/grandcat/zeroconf"
)

// Server is a running advertisement.
type Server struct {
	zc *zeroconf.Server
}

// Announce publishes the node. A failure is logged and returned; HTTP still works.
func Announce(name, nodeID, fingerprint, agentVersion string, port int) (*Server, error) {
	if name == "" {
		name, _ = os.Hostname()
	}
	txt := []string{
		"id=" + nodeID,
		"ver=" + agentVersion,
		"fp=" + fingerprint,
		"path=/v1",
	}
	zc, err := zeroconf.Register(name, "_ravn._tcp", "local.", port, txt, advertiseIfaces())
	if err != nil {
		slog.Warn("mdns advertisement failed", "err", err)
		return nil, err
	}
	return &Server{zc: zc}, nil
}

// advertiseIfaces skips docker and bridge interfaces so discovery does not publish those addresses.
func advertiseIfaces() []net.Interface {
	ifaces, err := net.Interfaces()
	if err != nil {
		return nil
	}
	var out []net.Interface
	for _, iface := range ifaces {
		if iface.Flags&net.FlagUp == 0 || iface.Flags&net.FlagMulticast == 0 || iface.Flags&net.FlagLoopback != 0 {
			continue
		}
		name := iface.Name
		if strings.HasPrefix(name, "docker") || strings.HasPrefix(name, "br-") || strings.HasPrefix(name, "veth") || strings.HasPrefix(name, "virbr") {
			continue
		}
		out = append(out, iface)
	}
	return out
}

// Close stops advertising.
func (s *Server) Close() {
	if s != nil && s.zc != nil {
		s.zc.Shutdown()
	}
}
