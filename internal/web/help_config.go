package web

import (
	"fmt"
	"maps"
	"slices"
	"strconv"

	"github.com/zanyoats/kukicha/internal/app"
)

type helpConfigEntry struct{ Key, Value string }
type helpConfigField struct {
	Label, Value string
	Items        []string
	Entries      []helpConfigEntry
	Kind         string
}
type helpConfigSection struct {
	Title  string
	Fields []helpConfigField
}

// Explicitly select displayed fields so future configuration secrets are not
// automatically exposed. These values describe the loaded configuration.
func runtimeConfiguration(conf *app.AppConfig) []helpConfigSection {
	if conf == nil {
		return nil
	}
	field := func(label string, value any) helpConfigField {
		text := fmt.Sprint(value)
		if text == "" {
			text = "(empty)"
		}
		return helpConfigField{Label: label, Value: text}
	}
	list := func(label string, values []string) helpConfigField {
		items := make([]string, len(values))
		for i, value := range values {
			items[i] = strconv.Quote(value)
		}
		return helpConfigField{Label: label, Items: items, Kind: "list"}
	}
	mapping := func(label string, values map[string]string) helpConfigField {
		entries := make([]helpConfigEntry, 0, len(values))
		for _, key := range slices.Sorted(maps.Keys(values)) {
			entries = append(entries, helpConfigEntry{key, values[key]})
		}
		return helpConfigField{Label: label, Entries: entries, Kind: "map"}
	}
	size := func(value int64) string {
		for _, unit := range []struct {
			name  string
			bytes int64
		}{{"GiB", 1 << 30}, {"MiB", 1 << 20}, {"KiB", 1 << 10}} {
			if value >= unit.bytes {
				return fmt.Sprintf("%.2f %s (%d bytes)", float64(value)/float64(unit.bytes), unit.name, value)
			}
		}
		return fmt.Sprintf("%d bytes", value)
	}
	s := &conf.Settings
	workers := strconv.Itoa(s.Jobs.Workers.Resolve())
	if s.Jobs.Workers.IsAuto() {
		workers = "auto (currently " + workers + ")"
	}
	artwork := make(map[string]string, len(s.Scan.RecognizedSidecarArtwork))
	for key, enabled := range s.Scan.RecognizedSidecarArtwork {
		if enabled {
			artwork[key] = "Enabled"
		} else {
			artwork[key] = "Disabled"
		}
	}
	sections := []helpConfigSection{
		{"Application", []helpConfigField{
			field("Application directory", conf.AppDir), field("Configuration file", conf.ConfigFilePath), field("Database connection", conf.DataSourceName),
		}},
		{"Server", []helpConfigField{
			field("Database path", s.Server.DatabasePath), field("Listen address", s.Server.ServerAddress), field("Base path", s.Server.AppBasePath),
			field("Read header timeout", s.Server.ReadHeaderTimeout), field("Shutdown timeout", s.Server.ShutdownTimeout), field("Session lifetime", s.Server.SessionLifetime),
			field("Database busy timeout", s.Server.Database.BusyTimeout), field("Database cache size", fmt.Sprintf("%d KiB", s.Server.Database.CacheSizeKiB)),
		}},
		{"Scanning", []helpConfigField{
			list("Ignore prefixes", s.Scan.IgnorePrefixes), list("Ignore files", s.Scan.IgnoreFiles), field("Progress log interval", s.Scan.ProgressLogInterval),
			field("Maximum local artwork size", size(s.Scan.LocalArtworkMaxBytes)), mapping("Recognized sidecar artwork", artwork),
			list("Primary genre split tokens", s.Scan.PrimaryGenreSplitTokens), list("Secondary genre split tokens", s.Scan.SecondaryGenreSplitTokens), mapping("Genre / style aliases", s.Scan.GenreStyleAliases),
		}},
	}
	for _, provider := range []struct {
		name   string
		config app.ProviderLookupConfig
	}{{"iTunes", s.Scan.ITunes}, {"Discogs", s.Scan.Discogs}, {"MusicBrainz", s.Scan.MusicBrainz}} {
		enabled := "Disabled"
		if provider.config.Enabled {
			enabled = "Enabled"
		}
		sections = append(sections, helpConfigSection{provider.name + " lookup", []helpConfigField{
			field("Status", enabled), field("Request timeout", provider.config.RequestTimeout), field("Lookup interval", provider.config.LookupInterval), field("Maximum attempts", provider.config.MaxAttempts),
		}})
	}
	optional := func(value *string) string {
		if value == nil {
			return "(not set)"
		}
		if *value == "" {
			return "(empty)"
		}
		return *value
	}
	configured := helpConfigSection{Title: "Configured roots"}
	for _, root := range s.Scan.Roots {
		configured.Fields = append(configured.Fields, helpConfigField{Label: root.ID, Kind: "map", Entries: []helpConfigEntry{
			{"Type", root.Type}, {"Path", optional(root.Path)}, {"Endpoint URL", optional(root.EndpointURL)}, {"Bucket", optional(root.Bucket)}, {"Prefix", optional(root.Prefix)}, {"Profile", optional(root.Profile)}, {"Region", optional(root.Region)}, {"Addressing style", optional(root.AddressingStyle)},
		}})
	}
	return append(sections, configured,
		helpConfigSection{"Jobs", []helpConfigField{field("Workers", workers), field("Poll interval", s.Jobs.PollInterval), field("Finalize timeout", s.Jobs.FinalizeTimeout)}},
		helpConfigSection{"Uploads", []helpConfigField{field("Maximum request size", size(s.Upload.MaxRequestBytes))}},
		helpConfigSection{"Logging", []helpConfigField{field("Level", s.Logging.Level)}},
		helpConfigSection{"Radio", []helpConfigField{field("Minimum tracks", s.Radio.MinTracks)}},
		helpConfigSection{"User interface", []helpConfigField{field("Items per page", s.UI.PerPage), field("Default album sort", s.UI.DefaultAlbumSort), field("Minimum toast duration", s.UI.ToastMinDuration)}},
	)
}
