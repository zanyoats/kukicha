package app

import (
	_ "embed"
	"errors"
	"fmt"
	"io/fs"
	"maps"
	"net"
	"os"
	"path"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"unicode"

	"github.com/BurntSushi/toml"
)

const (
	AppName        = "com.cconroy.kukicha"
	ConfigFileName = "kukicha.toml"
	DbFileName     = "kukicha.db"
)

type AppConfig struct {
	AppDir         string
	Roots          []Root
	DataSourceName string
	Settings       struct {
		DatabasePath string   `toml:"database_path"`
		Roots        []string `toml:"roots"`
		HTTP         struct {
			ServerAddress string `toml:"server_address"`
			AppBasePath   string `toml:"app_base_path"`
		} `toml:"http"`
		AlbumArtistSplitTokens    map[string]bool   `toml:"album_artist_split_tokens"`
		AlbumArtistSplitAuto      map[string]bool   `toml:"album_artist_split_auto_tokens"`
		RecognizedSidecarArtwork  map[string]bool   `toml:"recognized_sidecar_artwork"`
		PrimaryGenreSplitTokens   map[string]bool   `toml:"primary_genre_split_tokens"`
		SecondaryGenreSplitTokens map[string]bool   `toml:"secondary_genre_split_tokens"`
		GenreStyleAliases         map[string]string `toml:"genre_style_aliases"`
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
	if _, err = toml.Decode(string(defaultsTomlData), &result.Settings); err != nil {
		return nil, fmt.Errorf(
			"error decoding defaults configuration file: %w %s",
			err, "this is a bug",
		)
	}
	if _, err = toml.Decode(string(userTomlData), &result.Settings); err != nil {
		return nil, fmt.Errorf(
			"error decoding user's toml config file %q: %w",
			path, err,
		)
	}

	// Validations...

	// ...no empty tokens
	if slices.Contains(slices.Concat(
		slices.Collect(maps.Keys(result.Settings.AlbumArtistSplitTokens)),
		slices.Collect(maps.Keys(result.Settings.AlbumArtistSplitAuto)),
		slices.Collect(maps.Keys(result.Settings.PrimaryGenreSplitTokens)),
		slices.Collect(maps.Keys(result.Settings.SecondaryGenreSplitTokens)),
	), "") {
		return nil, fmt.Errorf(
			"I found a empty token in one of: %s",
			strings.Join([]string{
				"album_artist_split_tokens",
				"album_artist_split_auto_tokens",
				"primary_genre_split_tokens",
				"secondary_genre_split_tokens",
			}, ", "))
	}

	// ...auto tokens are valid and a subset of artist split tokens
	for token, enabled := range result.Settings.AlbumArtistSplitAuto {
		if !enabled {
			continue
		}

		if !isSymbolToken(token) {
			return nil, fmt.Errorf(
				"%s key %q must contain only punctuation/symbol characters",
				"album_artist_split_auto_tokens", token,
			)
		}

		if enabled := result.Settings.AlbumArtistSplitTokens[token]; !enabled {
			return nil, fmt.Errorf(
				"The token %q should be present/set in config key %s",
				"album_artist_split_tokens", token,
			)
		}
	}

	// ... user provided genres/styles are valid terms
	for _, genreOrStyle := range result.Settings.GenreStyleAliases {
		if !catalog.IsValidTerm(genreOrStyle) {
			format := strings.Join([]string{
				"The genre or style %q was not accepted as a valid taxonomy term.",
				"A print out of valid terms is available at %q",
			}, "")
			return nil, fmt.Errorf(format, genreOrStyle, "/foo/printout.txt")
		}
	}

	// ...normalize each root to a actualy folder path
	for pos, root := range result.Settings.Roots {
		var norm string

		if norm, err = normalizeFolderPath(result.AppDir, root); err != nil {
			return nil, err
		}

		newRoot := Root{RootPos: pos, RootPath: norm}
		result.Roots = append(result.Roots, newRoot)
	}

	// ...normalize database path
	dbPath = filepath.Join(result.AppDir, DbFileName)
	if result.Settings.DatabasePath != "" {
		var norm string

		if norm, err = normalizeFolderPath(result.AppDir, result.Settings.DatabasePath); err != nil {
			return nil, err
		}

		dbPath = filepath.Join(norm, DbFileName)
	}

	// valid http settings...
	if err := validateServerAddress(result.Settings.HTTP.ServerAddress); err != nil {
		return nil, fmt.Errorf(
			"%q setting is not valid: %w",
			"http.server_address", err,
		)
	}
	if err := validateAppBasePath(result.Settings.HTTP.AppBasePath); err != nil {
		return nil, fmt.Errorf(
			"%q setting is not valid: %w",
			"http.app_base_path", err,
		)
	}

	sqlitePragmas := []string{
		"_foreign_keys=on",
		"_synchronous=NORMAL",
		"_busy_timeout=5000",
		"_cache_size=-32768", // '-' means use kilobytes, thus 32 MB page store
	}
	dbPragmas := strings.Join(sqlitePragmas, "&")
	result.DataSourceName = fmt.Sprintf("%s?%s", dbPath, dbPragmas)

	return result, nil
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

	case path.Clean(basePath) != basePath:
		return errors.New("HTTP app base path must be clean")

	case strings.ContainsAny(basePath, "{}?#"):
		return errors.New("HTTP app base path cannot contain route wildcards, a query, or a fragment")
	}

	return nil
}

func isSymbolToken(token string) bool {
	if token == "" {
		return false
	}

	for _, r := range token {
		if !unicode.IsPunct(r) && !unicode.IsSymbol(r) {
			return false
		}
	}

	return true
}
