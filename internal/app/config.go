package app

import (
	_ "embed"
	"errors"
	"fmt"
	"io/fs"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strings"

	"github.com/BurntSushi/toml"
)

const (
	AppName        = "com.cconroy.kukicha"
	ConfigFileName = "kukicha.toml"
	DbFileName     = "kukicha.db"
)

type AppConfig struct {
	AppDir    string
	Roots     []Root
	DbPath    string
	DbPragmas string
	Settings  struct {
		DatabasePath              string            `toml:"database_path"`
		Roots                     []string          `toml:"roots"`
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

func LoadAppConfig(catalog *Catalog) (appConfig *AppConfig, err error) {
	var appDir, path string
	var userTomlData []byte

	if appDir, path, err = ensureConfigFile(); err != nil {
		err = GiveUp(err)
		return
	}

	if userTomlData, err = os.ReadFile(path); err != nil {
		err = GiveUp(err)
		return
	}

	appConfig = &AppConfig{AppDir: appDir}
	appConfig.DbPath = filepath.Join(appDir, DbFileName)
	sqlitePragmas := []string{
		"_foreign_keys=on",
		"_synchronous=NORMAL",
		"_busy_timeout=5000",
		"_cache_size=-32768", // '-' means use kilobytes, thus 32 MB page store
	}
	appConfig.DbPragmas = strings.Join(sqlitePragmas, "&")

	// First load defaults config, and then "merge" with user's config
	if _, err = toml.Decode(string(defaultsTomlData), &appConfig.Settings); err != nil {
		return nil, GiveUp(err)
	}
	if _, err = toml.Decode(string(userTomlData), &appConfig.Settings); err != nil {
		return nil, GiveUp(err)
	}

	// Validations...

	// ...no empty tokens
	if slices.Contains(slices.Concat(
		slices.Collect(maps.Keys(appConfig.Settings.AlbumArtistSplitTokens)),
		slices.Collect(maps.Keys(appConfig.Settings.AlbumArtistSplitAuto)),
		slices.Collect(maps.Keys(appConfig.Settings.PrimaryGenreSplitTokens)),
		slices.Collect(maps.Keys(appConfig.Settings.SecondaryGenreSplitTokens)),
	), "") {
		return nil, HeresWhy(
			"I found a empty token in " +
				"album_artist_split_tokens, " +
				"album_artist_split_auto_tokens, " +
				"primary_genre_split_tokens, " +
				"or secondary_genre_split_tokens")
	}

	// ...auto tokens subset of artist split tokens
	for token, enabled := range appConfig.Settings.AlbumArtistSplitAuto {
		if !enabled {
			continue
		}

		if enabled := appConfig.Settings.AlbumArtistSplitTokens[token]; !enabled {
			m := fmt.Sprintf("The token %q should be present/set in config key album_artist_split_tokens", token)
			return nil, HeresWhy(m)
		}
	}

	for _, genreOrStyle := range appConfig.Settings.GenreStyleAliases {
		if !catalog.IsValidTerm(genreOrStyle) {
			m := fmt.Sprintf(
				"The genre or style %q was not accepted as a valid taxonomy term. "+
					"A print out of valid terms is available at %q",
				genreOrStyle,
				// TODO
				"/foo/printout.txt",
			)
			return nil, HeresWhy(m)
		}
	}

	// ...normalize each root to a actualy folder path
	for pos, root := range appConfig.Settings.Roots {
		var norm string

		if norm, err = normalizeRoot(appConfig.AppDir, root); err != nil {
			return nil, err
		}

		newRoot := Root{RootPos: pos, RootPath: norm}
		appConfig.Roots = append(appConfig.Roots, newRoot)
	}

	// ...normalize database path
	if appConfig.Settings.DatabasePath != "" {
		var norm string

		if norm, err = normalizeFolderPath(appConfig.AppDir, appConfig.Settings.DatabasePath); err != nil {
			return nil, err
		}

		appConfig.DbPath = filepath.Join(norm, DbFileName)
	}

	return
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

func normalizeRoot(baseDir, root string) (result string, err error) {
	if root == "" {
		err = HeresWhy("I cannot accept a empty root path")
		return
	}

	if result, err = normalizeFolderPath(baseDir, root); err != nil {
		return
	}

	return
}

func normalizeFolderPath(baseDir, path string) (result string, err error) {
	switch {
	case strings.HasPrefix(path, "~"):
		var home string

		if home, err = os.UserHomeDir(); err != nil {
			err = HeresWhyErr(fmt.Sprintf("I could not resolve user's home dir in path %q", path), err)
			return
		}

		result = filepath.Join(home, strings.TrimPrefix(path, "~"))

	case !filepath.IsAbs(path):
		result = filepath.Join(baseDir, path)

	default:
		result = path
	}

	result = filepath.Clean(result)

	var info os.FileInfo
	if info, err = os.Lstat(result); err != nil {
		switch {
		case errors.Is(err, fs.ErrNotExist):
			err = HeresWhy(fmt.Sprintf(
				"The path %q you gave me does not exists",
				path,
			))
		default:
			err = GiveUp(err)
		}

		return
	}

	mode := info.Mode()

	if mode&os.ModeSymlink != 0 {
		if result, err = filepath.EvalSymlinks(result); err != nil {
			err = GiveUp(err)
			return
		}

		if info, err = os.Lstat(result); err != nil {
			err = GiveUp(err)
			return
		}

		mode = info.Mode()
	}

	if !info.IsDir() {
		err = HeresWhy(fmt.Sprintf(
			"I found the path %q but it's not a folder, instead you gave me a file type %v",
			path, mode.Type(),
		))
		return
	}

	return
}
