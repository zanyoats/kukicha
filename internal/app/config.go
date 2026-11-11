package app

import (
	_ "embed"
	"errors"
	"fmt"
	"io/fs"
	"log/slog"
	"math"
	"net"
	"os"
	"path"
	"path/filepath"
	"runtime"
	"slices"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/BurntSushi/toml"
)

const (
	AppName              = "com.cconroy.kukicha"
	ConfigFileName       = "kukicha.toml"
	DbFileName           = "kukicha.db"
	UserAgent            = "kukicha/0.1.0 (https://cconroy.com/kukicha)"
	LookupMaxBytes       = 2 * 1024 * 1024
	ArtworkMinimumWidth  = 256
	JSONRequestMaxBytes  = 1 << 20  // 1 MiB
	LoginRequestMaxBytes = 16 << 10 // 16 KiB
)

type Duration struct {
	time.Duration
}

func (duration *Duration) UnmarshalText(text []byte) error {
	value, err := time.ParseDuration(string(text))
	if err != nil {
		return err
	}

	duration.Duration = value
	return nil
}

type LogLevel struct {
	slog.Level
}

func (level *LogLevel) UnmarshalText(text []byte) error {
	switch strings.ToLower(string(text)) {
	case "debug":
		level.Level = slog.LevelDebug
	case "info":
		level.Level = slog.LevelInfo
	case "warn":
		level.Level = slog.LevelWarn
	case "error":
		level.Level = slog.LevelError
	default:
		return fmt.Errorf(
			"log level %q must be one of debug, info, warn, or error",
			text,
		)
	}

	return nil
}

// AlbumSort selects the ordering of an album collection.
type AlbumSort string

const (
	AlbumSortRecentlyAdded  AlbumSort = "recentlyAdded"
	AlbumSortRecentlyPlayed AlbumSort = "recentlyPlayed"
	AlbumSortAlbum          AlbumSort = "album"
	AlbumSortArtistAlbum    AlbumSort = "artistAlbum"
)

// UnmarshalText validates the album sort while decoding configuration.
func (sort *AlbumSort) UnmarshalText(text []byte) error {
	value := AlbumSort(text)
	switch value {
	case AlbumSortRecentlyAdded, AlbumSortRecentlyPlayed, AlbumSortAlbum, AlbumSortArtistAlbum:
		*sort = value
		return nil
	default:
		return fmt.Errorf("album sort %q must be one of recentlyAdded, recentlyPlayed, album, or artistAlbum", text)
	}
}

// WorkerCount stores a fixed count, or zero for auto selection
type WorkerCount struct {
	count int
}

// UnmarshalTOML accepts only a positive int or "auto"
func (w *WorkerCount) UnmarshalTOML(value any) error {
	switch value := value.(type) {
	case string:
		if value != "auto" {
			return fmt.Errorf(
				"jobs.workers must be a positive integer or \"auto\"; got %q",
				value,
			)
		}
		w.count = 0

	case int64:
		if value < 1 || value > math.MaxInt {
			return fmt.Errorf(
				"jobs.workers must be between 1 and %d; got %d",
				math.MaxInt, value,
			)
		}
		w.count = int(value)

	default:
		return fmt.Errorf(
			"jobs.workers must be a positive integer or \"auto\"; got %T",
			value,
		)
	}

	return nil
}

func (w *WorkerCount) IsAuto() bool {
	return w.count == 0
}

// Resolve selects the worker count from runtime if equals 0
func (w *WorkerCount) Resolve() int {
	if w.IsAuto() {
		return runtime.GOMAXPROCS(0)
	}

	return w.count
}

type RootConfig struct {
	ID string `json:"id"`
	RootStorageConfig
}

// RootStorageConfig contains the editable fields, without the logical root ID.
type RootStorageConfig struct {
	Type            string  `toml:"type" json:"type"`
	Path            *string `toml:"path" json:"path"`
	EndpointURL     *string `toml:"endpoint_url" json:"endpoint_url"`
	Bucket          *string `toml:"bucket" json:"bucket"`
	Prefix          *string `toml:"prefix" json:"prefix"`
	Profile         *string `toml:"profile" json:"profile"`
	Region          *string `toml:"region" json:"region"`
	AddressingStyle *string `toml:"addressing_style" json:"addressing_style"`
}

// ProviderLookupConfig controls network requests; cached results remain usable when disabled.
type ProviderLookupConfig struct {
	Enabled        bool     `toml:"enabled"`
	RequestTimeout Duration `toml:"request_timeout"`
	LookupInterval Duration `toml:"lookup_interval"`
	MaxAttempts    int      `toml:"max_attempts"`
}

type AppConfig struct {
	AppDir         string
	ConfigFilePath string
	DataSourceName string
	Settings       struct {
		Logging struct {
			Level LogLevel `toml:"level"`
		} `toml:"logging"`
		Scan struct {
			IgnorePrefixes            []string             `toml:"ignore_prefixes"`
			IgnoreFiles               []string             `toml:"ignore_files"`
			ProgressLogInterval       Duration             `toml:"progress_log_interval"`
			LocalArtworkMaxBytes      int64                `toml:"local_artwork_max_bytes"`
			ITunes                    ProviderLookupConfig `toml:"itunes"`
			Discogs                   ProviderLookupConfig `toml:"discogs"`
			MusicBrainz               ProviderLookupConfig `toml:"musicbrainz"`
			RecognizedSidecarArtwork  map[string]bool      `toml:"recognized_sidecar_artwork"`
			PrimaryGenreSplitTokens   []string             `toml:"primary_genre_split_tokens"`
			SecondaryGenreSplitTokens []string             `toml:"secondary_genre_split_tokens"`
			GenreStyleAliases         map[string]string    `toml:"genre_style_aliases"`
		} `toml:"scan"`
		Upload struct {
			MaxRequestBytes int64 `toml:"max_request_bytes"`
		} `toml:"upload"`
		Server struct {
			DatabasePath      string   `toml:"database_path"`
			ServerAddress     string   `toml:"server_address"`
			AppBasePath       string   `toml:"app_base_path"`
			ReadHeaderTimeout Duration `toml:"read_header_timeout"`
			IdleTimeout       Duration `toml:"idle_timeout"`
			ShutdownTimeout   Duration `toml:"shutdown_timeout"`
			SessionLifetime   Duration `toml:"session_lifetime"`
			Database          struct {
				BusyTimeout  Duration `toml:"busy_timeout"`
				CacheSizeKiB int64    `toml:"cache_size_kib"`
			} `toml:"database"`
		} `toml:"server"`
		Jobs struct {
			Workers         WorkerCount `toml:"workers"`
			PollInterval    Duration    `toml:"poll_interval"`
			FinalizeTimeout Duration    `toml:"finalize_timeout"`
		} `toml:"jobs"`
		Radio struct {
			MinTracks int64 `toml:"min_tracks"`
		} `toml:"radio"`
		UI struct {
			PerPage          int       `toml:"per_page"`
			DefaultAlbumSort AlbumSort `toml:"default_album_sort"`
			ToastMinDuration Duration  `toml:"toast_min_duration"`
		} `toml:"ui"`
	}
}

//go:embed defaults.toml
var defaultsTomlData []byte

func LoadAppConfig(catalog *Catalog) (*AppConfig, error) {
	var (
		dbPath       string
		userTomlData []byte
		err          error
		result       = new(AppConfig)
	)

	if result.AppDir, result.ConfigFilePath, err = ensureConfigFile(); err != nil {
		return nil, fmt.Errorf(
			"error setting up config file name %q: %w",
			ConfigFileName, err,
		)
	}

	if userTomlData, err = os.ReadFile(result.ConfigFilePath); err != nil {
		return nil, fmt.Errorf(
			"error reading config file path %q: %w",
			result.ConfigFilePath, err,
		)
	}

	// First load defaults config, and then "merge" with user's config
	if err = decodeSettings(defaultsTomlData, &result.Settings); err != nil {
		return nil, fmt.Errorf(
			"error decoding defaults configuration file: %w %s",
			err, "this is a bug",
		)
	}
	if err = decodeSettings(userTomlData, &result.Settings); err != nil {
		return nil, fmt.Errorf(
			"error decoding user's toml config file %q: %w",
			result.ConfigFilePath, err,
		)
	}

	// Validations...
	if result.Settings.Scan.ProgressLogInterval.Duration < 0 {
		return nil, errors.New(
			`"scan.progress_log_interval" setting cannot be negative`,
		)
	}
	if result.Settings.Scan.LocalArtworkMaxBytes < 1 {
		return nil, errors.New(
			`"scan.local_artwork_max_bytes" setting must be a positive integer`,
		)
	}
	for _, provider := range []struct {
		name   string
		config ProviderLookupConfig
	}{
		{"itunes", result.Settings.Scan.ITunes},
		{"discogs", result.Settings.Scan.Discogs},
		{"musicbrainz", result.Settings.Scan.MusicBrainz},
	} {
		if provider.config.RequestTimeout.Duration <= 0 {
			return nil, fmt.Errorf("scan.%s.request_timeout must be greater than zero", provider.name)
		}
		if provider.config.LookupInterval.Duration < 0 {
			return nil, fmt.Errorf("scan.%s.lookup_interval cannot be negative", provider.name)
		}
		if provider.config.MaxAttempts < 1 {
			return nil, fmt.Errorf("scan.%s.max_attempts must be a positive integer", provider.name)
		}
	}

	if slices.Contains(result.Settings.Scan.IgnorePrefixes, "") {
		return nil, errors.New(`"scan.ignore_prefixes" cannot contain an empty prefix`)
	}

	// ...no empty tokens
	if slices.Contains(slices.Concat(
		result.Settings.Scan.PrimaryGenreSplitTokens,
		result.Settings.Scan.SecondaryGenreSplitTokens,
	), "") {
		return nil, fmt.Errorf(
			"empty genre split delimiters are not allowed in: %s",
			strings.Join([]string{
				"scan.primary_genre_split_tokens",
				"scan.secondary_genre_split_tokens",
			}, ", "))
	}

	// ... user provided genres/styles are valid terms
	for _, genreOrStyle := range result.Settings.Scan.GenreStyleAliases {
		if !catalog.IsValidTerm(genreOrStyle) {
			format := strings.Join([]string{
				"The genre or style %q was not accepted as a valid taxonomy term.",
				"A print out of valid terms is available at %q",
			}, "")
			return nil, fmt.Errorf(format, genreOrStyle, "/foo/printout.txt")
		}
	}

	// ...normalize database path
	dbPath, err = databasePath(result.AppDir, result.Settings.Server.DatabasePath)
	if err != nil {
		return nil, err
	}

	if result.Settings.Upload.MaxRequestBytes <= 0 {
		return nil, errors.New(`"upload.max_request_bytes" setting must be a positive integer`)
	}

	// ...valid server settings
	if err := validateServerAddress(result.Settings.Server.ServerAddress); err != nil {
		return nil, fmt.Errorf(
			"%q setting is not valid: %w",
			"server.server_address", err,
		)
	}
	if err := validateAppBasePath(result.Settings.Server.AppBasePath); err != nil {
		return nil, fmt.Errorf(
			"%q setting is not valid: %w",
			"server.app_base_path", err,
		)
	}
	if result.Settings.Server.SessionLifetime.Duration < time.Second {
		return nil, errors.New(
			`"server.session_lifetime" setting must be at least one second`,
		)
	}
	if result.Settings.Server.ReadHeaderTimeout.Duration <= 0 {
		return nil, errors.New(
			`"server.read_header_timeout" setting must be greater than zero`,
		)
	}
	if result.Settings.Server.IdleTimeout.Duration < 0 {
		return nil, errors.New(
			`"server.idle_timeout" setting cannot be negative`,
		)
	}
	if result.Settings.Server.Database.BusyTimeout.Duration < 0 {
		return nil, errors.New(
			`"server.database.busy_timeout" setting cannot be negative`,
		)
	}
	if result.Settings.Server.Database.CacheSizeKiB < 1 {
		return nil, errors.New(
			`"server.database.cache_size_kib" setting must be a positive integer`,
		)
	}

	if result.Settings.Radio.MinTracks < 1 {
		return nil, errors.New(`"radio.min_tracks" setting must be a positive integer`)
	}

	// ...valid UI settings
	if result.Settings.UI.ToastMinDuration.Duration <= 0 {
		return nil, errors.New(
			`"ui.toast_min_duration" setting must be greater than zero`,
		)
	}
	if result.Settings.UI.PerPage < 1 {
		return nil, errors.New(
			`"ui.per_page" setting must be a positive integer`,
		)
	}

	busyTimeout := result.Settings.Server.Database.BusyTimeout.Duration
	busyTimeoutMilliseconds := busyTimeout.Milliseconds()

	sqlitePragmas := []string{
		"_foreign_keys=on",
		"_synchronous=NORMAL",
		fmt.Sprintf("_busy_timeout=%d", busyTimeoutMilliseconds),
		fmt.Sprintf(
			"_cache_size=-%d",
			result.Settings.Server.Database.CacheSizeKiB,
		), // '-' means the value uses kibibytes rather than pages
	}
	dbPragmas := strings.Join(sqlitePragmas, "&")
	result.DataSourceName = fmt.Sprintf("%s?%s", dbPath, dbPragmas)

	return result, nil
}

// IsIgnoredScanPath matches a local filename or a complete remote object key.
// Prefixes are case-sensitive; exact matches are case-insensitive.
func IsIgnoredScanPath(name string, prefixes, names []string) bool {
	for _, prefix := range prefixes {
		if strings.HasPrefix(name, prefix) {
			return true
		}
	}
	for _, ignored := range names {
		if strings.EqualFold(name, ignored) {
			return true
		}
	}
	return false
}

func decodeSettings(data []byte, settings any) error {
	metadata, err := toml.Decode(string(data), settings)
	if err != nil {
		return err
	}

	undecoded := metadata.Undecoded()
	if len(undecoded) == 0 {
		return nil
	}

	keys := make([]string, len(undecoded))
	for index, key := range undecoded {
		keys[index] = key.String()
	}
	slices.Sort(keys)

	return fmt.Errorf(
		"unrecognized configuration keys: %s",
		strings.Join(keys, ", "),
	)
}

// ConfiguredDatabasePath reads the database location without initializing the app.
func ConfiguredDatabasePath(appDir, configPath string) (string, error) {
	var config struct {
		Server struct {
			DatabasePath string `toml:"database_path"`
		} `toml:"server"`
	}
	if _, err := toml.DecodeFile(configPath, &config); err != nil {
		return "", err
	}
	return databasePath(appDir, config.Server.DatabasePath)
}

func databasePath(appDir, folder string) (string, error) {
	if folder == "" {
		return filepath.Join(appDir, DbFileName), nil
	}
	dir, err := normalizeFolderPath(appDir, folder)
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, DbFileName), nil
}

func ensureConfigFile() (appDir string, path string, err error) {
	dir, err := os.UserConfigDir()
	if err != nil {
		return
	}

	appDir = filepath.Join(dir, AppName)
	path = filepath.Join(appDir, ConfigFileName)

	if err = os.MkdirAll(appDir, 0o755); err != nil {
		return
	}

	f, err := os.OpenFile(path, os.O_RDWR|os.O_CREATE|os.O_EXCL, 0o644)
	if err != nil {
		if errors.Is(err, fs.ErrExist) {
			err = nil // unset, not a error in this circumstance
		}

		return
	}
	defer f.Close()

	return
}

func normalizeFolderPath(baseDir, name string) (string, error) {
	if name == "" {
		return "", errors.New("folder path must not be empty")
	}
	resolved, err := normalizeRootPath(baseDir, name)
	if err != nil {
		return "", err
	}
	info, err := os.Stat(resolved)
	if err != nil {
		return "", fmt.Errorf("error accessing folder %q: %w", name, err)
	}
	if !info.IsDir() {
		return "", fmt.Errorf("path %q is not a folder", name)
	}
	return resolved, nil
}

func validateServerAddress(address string) error {
	if address == "" {
		return errors.New("HTTP server address cannot be empty")
	}

	if strings.IndexFunc(address, unicode.IsSpace) >= 0 {
		return errors.New("HTTP server address cannot contain whitespace")
	}

	_, portText, err := net.SplitHostPort(address)
	if err != nil {
		return fmt.Errorf(
			"HTTP server address must use host:port format: %w",
			err,
		)
	}

	port, err := strconv.Atoi(portText)
	if err != nil {
		return fmt.Errorf("HTTP server port %q must be numeric", portText)
	}

	if port < 1 || port > 65535 {
		return fmt.Errorf(
			"HTTP server port %d must be between 1 and 65535",
			port,
		)
	}

	return nil
}

func validateAppBasePath(basePath string) error {
	switch {
	case basePath == "":
		return errors.New("HTTP app base path cannot be empty")

	case !strings.HasPrefix(basePath, "/"):
		return errors.New("HTTP app base path must start with '/'")

	case strings.HasPrefix(basePath, "//"):
		return errors.New("HTTP app base path must start with exactly one '/'")

	case basePath == "/":
		return errors.New("HTTP app base path cannot be the root path")

	case strings.HasSuffix(basePath, "/"):
		return errors.New("HTTP app base path cannot end in a slash")

	case path.Clean(basePath) != basePath:
		return errors.New("HTTP app base path must be clean")

	case strings.IndexFunc(basePath, func(r rune) bool {
		return unicode.IsSpace(r) || unicode.IsControl(r)
	}) >= 0:
		return errors.New("HTTP app base path cannot contain whitespace or control characters")

	case strings.Contains(basePath, "%"):
		return errors.New("HTTP app base path cannot contain percent escapes")

	case strings.ContainsAny(basePath, `\{}?#`):
		return errors.New(
			"HTTP app base path contains unsupported routing characters",
		)
	}

	return nil
}

// NormalizeRoot validates one root and resolves its storage settings.
// Duplicate IDs and overlapping storage are checked against persisted roots by the caller.
func NormalizeRoot(baseDir string, entry RootConfig) (Root, error) {
	value := func(field *string) string {
		if field == nil {
			return ""
		}
		return *field
	}
	entry.ID = strings.TrimSpace(entry.ID)
	invalid := func(message string) error {
		return fmt.Errorf("storage location %q: %s", entry.ID, message)
	}
	if entry.ID == "" {
		return nil, invalid("id must be non-empty")
	}
	switch entry.Type {
	case "local":
		for _, field := range []struct {
			name  string
			value *string
		}{
			{"endpoint_url", entry.EndpointURL}, {"bucket", entry.Bucket},
			{"prefix", entry.Prefix}, {"profile", entry.Profile},
			{"region", entry.Region}, {"addressing_style", entry.AddressingStyle},
		} {
			if field.value != nil {
				return nil, invalid(fmt.Sprintf("%s is only allowed for type remote", field.name))
			}
		}
		if entry.Path == nil || *entry.Path == "" {
			return nil, invalid("path is required for type local")
		}
		path, err := normalizeRootPath(baseDir, *entry.Path)
		if err != nil {
			return nil, invalid(err.Error())
		}
		return &LocalRoot{ID: entry.ID, Path: path}, nil
	case "remote":
		if entry.Path != nil {
			return nil, invalid("path is only allowed for type local")
		}
		root := &RemoteRoot{
			ID: entry.ID, EndpointURL: value(entry.EndpointURL), Bucket: value(entry.Bucket),
			Prefix: value(entry.Prefix), Profile: value(entry.Profile),
			Region: value(entry.Region), AddressingStyle: value(entry.AddressingStyle),
		}
		if err := normalizeRemoteRoot(root); err != nil {
			return nil, invalid(err.Error())
		}
		return root, nil
	default:
		return nil, invalid("type must be local or remote")
	}
}

func normalizeRemoteRoot(r *RemoteRoot) error {
	location, err := normalizeRemoteLocation(remoteLocation{
		EndpointURL: r.EndpointURL, Bucket: r.Bucket, Prefix: r.Prefix,
		Region: r.Region, AddressingStyle: r.AddressingStyle,
	})
	if err != nil {
		return fmt.Errorf("remote storage location %q: %w", r.ID, err)
	}
	r.EndpointURL, r.Bucket, r.Region, r.AddressingStyle = location.EndpointURL, location.Bucket, location.Region, location.AddressingStyle
	r.Profile = strings.TrimSpace(r.Profile)
	return nil
}

// Resolve local destinations without requiring them to exist. Ownership and
// permission checks occur when an upload uses the root.
func normalizeRootPath(baseDir, name string) (string, error) {
	if strings.HasPrefix(name, "~") {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		name = filepath.Join(home, strings.TrimPrefix(name, "~"))
	} else if !filepath.IsAbs(name) {
		name = filepath.Join(baseDir, name)
	}
	name, err := filepath.Abs(name)
	if err != nil {
		return "", err
	}
	// Resolve the existing ancestor, including symlinks, for absent destinations.
	ancestor := name
	var missing []string
	for {
		resolved, err := filepath.EvalSymlinks(ancestor)
		if err == nil {
			info, err := os.Stat(resolved)
			if err != nil {
				return "", err
			}
			if !info.IsDir() {
				return "", fmt.Errorf("storage location ancestor %q is not a directory", ancestor)
			}
			for i := len(missing) - 1; i >= 0; i-- {
				resolved = filepath.Join(resolved, missing[i])
			}
			return resolved, nil
		}
		if !errors.Is(err, os.ErrNotExist) {
			return "", err
		}
		parent := filepath.Dir(ancestor)
		if parent == ancestor {
			return "", err
		}
		missing = append(missing, filepath.Base(ancestor))
		ancestor = parent
	}
}
