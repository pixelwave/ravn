package main

import (
	"strings"
	"testing"
	"time"
	"unicode/utf8"
)

func TestBarMatchesTemplateScale(t *testing.T) {
	if got := bar(0.09); got != "[#-------------------]" {
		t.Fatalf("memory bar: %s", got)
	}
	if got := bar(39.2 / 100); got != "[#######-------------]" {
		t.Fatalf("temp bar: %s", got)
	}
	if got := bar(0); got != "[--------------------]" {
		t.Fatalf("empty bar: %s", got)
	}
}

func TestFormatUptime(t *testing.T) {
	got := formatUptime((3*7*24*time.Hour + 5*24*time.Hour + 21*time.Hour + 29*time.Minute))
	want := "3 weeks, 5 days, 21 hours, 29 minutes"
	if got != want {
		t.Fatalf("got %q", got)
	}
	if formatUptime(time.Minute) != "1 minute" {
		t.Fatal(formatUptime(time.Minute))
	}
	if formatUptime(0) != "0 minutes" {
		t.Fatal(formatUptime(0))
	}
}

func TestTokenBoxMatchesSample(t *testing.T) {
	token := "ravn_1234567890abcdef1234567890abcdef1234567890"
	if len(token) != 47 {
		t.Fatalf("sample token length: %d", len(token))
	}
	got := tokenBox(token)
	want := " ┌─ API Token " + strings.Repeat("─", 41) + "┐\n" +
		" │  " + token + "    │\n" +
		" └" + strings.Repeat("─", 53) + "┘\n"
	if got != want {
		t.Fatalf("got:\n%s\nwant:\n%s", got, want)
	}
}

func TestTokenBoxGrowsAndAligns(t *testing.T) {
	token := strings.Repeat("ab", 32)
	lines := strings.Split(strings.TrimSuffix(tokenBox(token), "\n"), "\n")
	if len(lines) != 3 {
		t.Fatalf("lines: %d", len(lines))
	}
	width := utf8.RuneCountInString(lines[0])
	for _, line := range lines[1:] {
		if utf8.RuneCountInString(line) != width {
			t.Fatalf("width %d:\n%s", width, strings.Join(lines, "\n"))
		}
	}
	if !strings.Contains(lines[1], "  "+token+"    ") {
		t.Fatalf("padding: %s", lines[1])
	}
}

func TestTokenBoxSkipsEmpty(t *testing.T) {
	if tokenBox("  \n") != "" {
		t.Fatal(tokenBox("  \n"))
	}
}

func TestShortID(t *testing.T) {
	if got := shortID("7f3a9c1e-0000-0000-0000-00000000c921"); got != "7f3a…c921" {
		t.Fatalf("got %q", got)
	}
	if shortID("") != "unknown" {
		t.Fatal(shortID(""))
	}
}
