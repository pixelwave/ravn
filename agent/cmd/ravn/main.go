package main

import (
	"context"
	"crypto/tls"
	"flag"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"ravn/internal/api"
	"ravn/internal/collect"
	"ravn/internal/config"
	"ravn/internal/identity"
	"ravn/internal/mdns"
	"ravn/internal/update"
	"ravn/internal/version"
)

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	switch os.Args[1] {
	case "serve":
		os.Exit(serve(os.Args[2:]))
	case "token":
		os.Exit(printFile(os.Args[2:], "token"))
	case "fingerprint":
		os.Exit(printFingerprint(os.Args[2:]))
	case "status":
		os.Exit(printStatus(os.Args[2:]))
	case "installed":
		os.Exit(installed(os.Args[2:]))
	case "-h", "--help", "help":
		usage()
	default:
		usage()
		os.Exit(2)
	}
}

func usage() {
	fmt.Fprintf(os.Stderr, "ravn %s\n\ncommands: serve, token, fingerprint, status, installed\n", version.Version)
}

func serve(args []string) int {
	fs := flag.NewFlagSet("serve", flag.ContinueOnError)
	configDir := fs.String("config-dir", "/etc/ravn", "identity and sensor settings")
	stateDir := fs.String("state-dir", "/var/lib/ravn", "temporary update files")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	slog.SetDefault(slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelInfo})))

	id, err := identity.Open(*configDir)
	if err != nil {
		slog.Error("identity", "err", err)
		return 1
	}
	cfg, err := config.Load(*configDir)
	if err != nil {
		slog.Error("config", "err", err)
		return 1
	}
	if err := config.Save(*configDir, cfg); err != nil {
		slog.Error("config", "err", err)
		return 1
	}
	update.CleanPartials(*stateDir)

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	mgr := collect.NewManager(*configDir, cfg, collect.Paths{})
	mgr.Start(ctx)
	up := update.New(cfg.Update.Repo(), cfg.Update.APIBase, *stateDir)
	handler := api.New(api.Deps{
		Token: id.Token, NodeID: id.NodeID, CertFP: id.Fingerprint, Collect: mgr, Updates: up,
	})
	ln, err := tls.Listen("tcp", cfg.Listen, id.TLSConfig())
	if err != nil {
		slog.Error("listen", "err", err, "addr", cfg.Listen)
		return 1
	}
	srv := &http.Server{
		Handler:           handler,
		ReadHeaderTimeout: 5 * time.Second,
		IdleTimeout:       90 * time.Second,
	}
	go func() {
		if err := srv.Serve(ln); err != nil && err != http.ErrServerClosed {
			slog.Error("http", "err", err)
			stop()
		}
	}()

	port := listenPort(ln.Addr().String(), cfg.Listen)
	ad, err := mdns.Announce(mgr.Hostname(), id.NodeID, id.Fingerprint, version.Version, port)
	if err != nil {
		slog.Warn("discovery disabled", "err", err)
	}
	slog.Info("ravn listening", "addr", ln.Addr().String(), "version", version.Version)

	<-ctx.Done()
	ad.Close()
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = srv.Shutdown(shutdownCtx)
	update.CleanPartials(*stateDir)
	return 0
}

func listenPort(addr, fallback string) int {
	_, port, err := net.SplitHostPort(addr)
	if err != nil {
		_, port, err = net.SplitHostPort(fallback)
		if err != nil {
			port = strings.TrimPrefix(fallback, ":")
		}
	}
	n, _ := strconv.Atoi(port)
	if n == 0 {
		return 8770
	}
	return n
}

func printFile(args []string, name string) int {
	dir := configDir(args)
	raw, err := os.ReadFile(dir + "/" + name)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	fmt.Println(strings.TrimSpace(string(raw)))
	return 0
}

func printFingerprint(args []string) int {
	dir := configDir(args)
	id, err := identity.Open(dir)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	fmt.Println(id.Fingerprint)
	return 0
}

func installed(args []string) int {
	fs := flag.NewFlagSet("installed", flag.ContinueOnError)
	dir := fs.String("config-dir", "/etc/ravn", "identity and sensor settings")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	return printInstalled(*dir)
}

func configDir(args []string) string {
	fs := flag.NewFlagSet("cmd", flag.ContinueOnError)
	dir := fs.String("config-dir", "/etc/ravn", "identity and sensor settings")
	_ = fs.Parse(args)
	return *dir
}
