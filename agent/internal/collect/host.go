package collect

import (
	"net"
	"os"
	"strings"
)

// IPInfo is the address set reported to Home Assistant.
type IPInfo struct {
	Primary string   `json:"primary"`
	IPv4    []string `json:"ipv4"`
	IPv6    []string `json:"ipv6"`
}

// ReadIP reads addresses at call time. Name changes are watched separately; addresses are cheap to read.
func ReadIP(routePath string) IPInfo {
	def := ""
	if raw, err := os.ReadFile(routePath); err == nil {
		def = ParseDefaultRoute(string(raw))
	}
	ifaces, err := net.Interfaces()
	if err != nil {
		return IPInfo{}
	}
	info := IPInfo{}
	var fallback string
	for _, iface := range ifaces {
		if iface.Flags&net.FlagUp == 0 || iface.Flags&net.FlagLoopback != 0 {
			continue
		}
		if skipIface(iface.Name) {
			continue
		}
		addrs, err := iface.Addrs()
		if err != nil {
			continue
		}
		var v4 string
		for _, addr := range addrs {
			ipnet, ok := addr.(*net.IPNet)
			if !ok || ipnet.IP == nil {
				continue
			}
			ip := ipnet.IP
			if ip.IsLoopback() || ip.IsLinkLocalUnicast() {
				continue
			}
			if v4ip := ip.To4(); v4ip != nil {
				s := v4ip.String()
				info.IPv4 = append(info.IPv4, s)
				if iface.Name == def && v4 == "" {
					v4 = s
				}
				if fallback == "" {
					fallback = s
				}
				continue
			}
			info.IPv6 = append(info.IPv6, ip.String())
		}
		if iface.Name == def && v4 != "" {
			info.Primary = v4
		}
	}
	if info.Primary == "" {
		info.Primary = fallback
	}
	if info.IPv4 == nil {
		info.IPv4 = []string{}
	}
	if info.IPv6 == nil {
		info.IPv6 = []string{}
	}
	return info
}

func skipIface(name string) bool {
	return strings.HasPrefix(name, "docker") ||
		strings.HasPrefix(name, "br-") ||
		strings.HasPrefix(name, "veth") ||
		strings.HasPrefix(name, "virbr")
}
