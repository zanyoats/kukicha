package app

import (
	"log/slog"
	"maps"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/BurntSushi/toml"
)

func TestDefaultsTomlTopLevelTables(t *testing.T) {
	var decoded map[string]any
	if _, err := toml.Decode(string(defaultsTomlData), &decoded); err != nil {
		t.Fatalf("decode defaults: %v", err)
	}

	got := slices.Sorted(maps.Keys(decoded))
	want := []string{"logging", "scan", "server", "ui"}
	if !slices.Equal(got, want) {
		t.Fatalf("top-level keys = %v, want %v", got, want)
	}
}

func TestDefaultsTomlOperationalSettings(t *testing.T) {
	var config AppConfig
	if err := decodeSettings(defaultsTomlData, &config.Settings); err != nil {
		t.Fatalf("decode defaults: %v", err)
	}

	checks := []struct {
		name string
		got  any
		want any
	}{
		{"logging.level", config.Settings.Logging.Level.Level, slog.LevelInfo},
		{"scan.progress_log_interval", config.Settings.Scan.ProgressLogInterval.Duration, 15 * time.Second},
		{"scan.local_artwork_max_bytes", config.Settings.Scan.LocalArtworkMaxBytes, int64(25 * 1024 * 1024)},
		{"scan.itunes.enabled", config.Settings.Scan.ITunes.Enabled, true},
		{"scan.itunes.request_timeout", config.Settings.Scan.ITunes.RequestTimeout.Duration, 20 * time.Second},
		{"scan.itunes.lookup_interval", config.Settings.Scan.ITunes.LookupInterval.Duration, 3250 * time.Millisecond},
		{"scan.itunes.retry_attempts", config.Settings.Scan.ITunes.RetryAttempts, 2},
		{"server.read_header_timeout", config.Settings.Server.ReadHeaderTimeout.Duration, 5 * time.Second},
		{"server.database.busy_timeout", config.Settings.Server.Database.BusyTimeout.Duration, 5 * time.Second},
		{"server.database.cache_size_kib", config.Settings.Server.Database.CacheSizeKiB, int64(32 * 1024)},
	}

	for _, check := range checks {
		if check.got != check.want {
			t.Errorf("%s = %v, want %v", check.name, check.got, check.want)
		}
	}
}

func TestDecodeSettingsRejectsInvalidLogLevel(t *testing.T) {
	var config AppConfig
	err := decodeSettings(
		[]byte("[logging]\nlevel = \"verbose\"\n"),
		&config.Settings,
	)
	if err == nil || !strings.Contains(err.Error(), "debug, info, warn, or error") {
		t.Fatalf("error = %v, want invalid log level rejection", err)
	}
}

func TestDecodeSettingsRejectsLegacyITunesLookupKey(t *testing.T) {
	var config AppConfig
	err := decodeSettings(
		[]byte("[scan]\nitunes_lookup = true\n"),
		&config.Settings,
	)
	if err == nil || !strings.Contains(err.Error(), "scan.itunes_lookup") {
		t.Fatalf("error = %v, want legacy key rejection", err)
	}
}
