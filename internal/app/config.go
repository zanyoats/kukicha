package app

import (
	_ "embed"
	"errors"
	"fmt"
	"io/fs"
	"log/slog"
	"maps"
	"net"
	"os"
	"path"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/BurntSushi/toml"
)

const (
	AppName        = "com.cconroy.kukicha"
	ConfigFileName = "kukicha.toml"
	DbFileName     = "kukicha.db"
	UserAgent      = "kukicha/0.1"
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
	AlbumSortRecentlyAdded AlbumSort = "recentlyAdded"
	AlbumSortAlbum         AlbumSort = "album"
	AlbumSortArtistAlbum   AlbumSort = "artistAlbum"
)

// UnmarshalText validates the album sort while decoding configuration.
func (sort *AlbumSort) UnmarshalText(text []byte) error {
	value := AlbumSort(text)
	switch value {
	case AlbumSortRecentlyAdded, AlbumSortAlbum, AlbumSortArtistAlbum:
		*sort = value
		return nil
	default:
		return fmt.Errorf("album sort %q must be one of recentlyAdded, album, or artistAlbum", text)
	}
}

type AppConfig struct {
	AppDir         string
	Roots          []Root
	DataSourceName string
	Settings       struct {
		Logging struct {
			Level LogLevel `toml:"level"`
		} `toml:"logging"`
		Scan struct {
			Roots                []string `toml:"roots"`
			ProgressLogInterval  Duration `toml:"progress_log_interval"`
			LocalArtworkMaxBytes int64    `toml:"local_artwork_max_bytes"`
			ITunes               struct {
				Enabled        bool     `toml:"enabled"`
				RequestTimeout Duration `toml:"request_timeout"`
				LookupInterval Duration `toml:"lookup_interval"`
				RetryAttempts  int      `toml:"retry_attempts"`
			} `toml:"itunes"`
			AlbumArtistSplitTokens    map[string]bool   `toml:"album_artist_split_tokens"`
			RecognizedSidecarArtwork  map[string]bool   `toml:"recognized_sidecar_artwork"`
			PrimaryGenreSplitTokens   map[string]bool   `toml:"primary_genre_split_tokens"`
			SecondaryGenreSplitTokens map[string]bool   `toml:"secondary_genre_split_tokens"`
			GenreStyleAliases         map[string]string `toml:"genre_style_aliases"`
		} `toml:"scan"`
		Server struct {
			DatabasePath      string   `toml:"database_path"`
			ServerAddress     string   `toml:"server_address"`
			AppBasePath       string   `toml:"app_base_path"`
			ReadHeaderTimeout Duration `toml:"read_header_timeout"`
			Database          struct {
				BusyTimeout  Duration `toml:"busy_timeout"`
				CacheSizeKiB int64    `toml:"cache_size_kib"`
			} `toml:"database"`
		} `toml:"server"`
		UI struct {
			PerPage          int       `toml:"per_page"`
			DefaultAlbumSort AlbumSort `toml:"default_album_sort"`
		} `toml:"ui"`
	}
}

//go:embed defaults.toml
var defaultsTomlData []byte

func LoadAppConfig(catalog *Catalog) (*AppConfig, error) {
	var (
		path         string
		dbPath       string
		userTomlData []byte
		err          error
		result       = new(AppConfig)
	)

	if result.AppDir, path, err = ensureConfigFile(); err != nil {
		return nil, fmt.Errorf(
			"error setting up config file name %q: %w",
			ConfigFileName, err,
		)
	}

	if userTomlData, err = os.ReadFile(path); err != nil {
		return nil, fmt.Errorf(
			"error reading config file path %q: %w",
			path, err,
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
			path, err,
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
	if result.Settings.Scan.ITunes.RequestTimeout.Duration <= 0 {
		return nil, errors.New(
			`"scan.itunes.request_timeout" setting must be greater than zero`,
		)
	}
	if result.Settings.Scan.ITunes.LookupInterval.Duration < 0 {
		return nil, errors.New(
			`"scan.itunes.lookup_interval" setting cannot be negative`,
		)
	}
	if result.Settings.Scan.ITunes.RetryAttempts < 1 {
		return nil, errors.New(
			`"scan.itunes.retry_attempts" setting must be a positive integer`,
		)
	}

	// ...no empty tokens
	if slices.Contains(slices.Concat(
		slices.Collect(maps.Keys(result.Settings.Scan.AlbumArtistSplitTokens)),
		slices.Collect(maps.Keys(result.Settings.Scan.PrimaryGenreSplitTokens)),
		slices.Collect(maps.Keys(result.Settings.Scan.SecondaryGenreSplitTokens)),
	), "") {
		return nil, fmt.Errorf(
			"I found a empty token in one of: %s",
			strings.Join([]string{
				"scan.album_artist_split_tokens",
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

	// ...normalize each root to a actualy folder path
	for pos, root := range result.Settings.Scan.Roots {
		var norm string

		if norm, err = normalizeFolderPath(result.AppDir, root); err != nil {
			return nil, err
		}

		newRoot := Root{RootPos: pos, RootPath: norm}
		result.Roots = append(result.Roots, newRoot)
	}

	// ...normalize database path
	dbPath = filepath.Join(result.AppDir, DbFileName)
	if result.Settings.Server.DatabasePath != "" {
		var norm string

		if norm, err = normalizeFolderPath(result.AppDir, result.Settings.Server.DatabasePath); err != nil {
			return nil, err
		}

		dbPath = filepath.Join(norm, DbFileName)
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
	if result.Settings.Server.ReadHeaderTimeout.Duration <= 0 {
		return nil, errors.New(
			`"server.read_header_timeout" setting must be greater than zero`,
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

	// ...valid UI settings
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

func normalizeFolderPath(baseDir, path string) (string, error) {
	var result string

	switch {
	case path == "":
		return "", fmt.Errorf(
			"empty folder path %q was found",
			path,
		)
	case strings.HasPrefix(path, "~"):
		home, err := os.UserHomeDir()
		if err != nil {
			return "", fmt.Errorf(
				"error resolving user's home dir in path %q: %w",
				path, err,
			)
		}

		result = filepath.Join(home, strings.TrimPrefix(path, "~"))

	case !filepath.IsAbs(path):
		result = filepath.Join(baseDir, path)

	default:
		result = path
	}

	result = filepath.Clean(result)

	info, err := os.Lstat(result)
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return "", fmt.Errorf(
				"The path %q given does not exist",
				path,
			)
		}

		return "", fmt.Errorf(
			"error processing path %q: %w",
			result, err,
		)
	}

	mode := info.Mode()
	if mode&os.ModeSymlink != 0 {
		if result, err = filepath.EvalSymlinks(result); err != nil {
			return "", fmt.Errorf(
				"error resolving symbolic link %q: %w",
				result, err,
			)
		}

		info, err := os.Lstat(result)
		if err != nil {
			return "", fmt.Errorf(
				"error processing resolved link %q from original path %q: %w",
				result, path, err,
			)
		}

		mode = info.Mode()
	}

	if !info.IsDir() {
		return "", fmt.Errorf(
			"path %q is not a folder; file type found to be %v",
			path, mode.Type(),
		)
	}

	return result, nil
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
