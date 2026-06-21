package app

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"

	"github.com/BurntSushi/toml"
)

const (
	AppName        = "com.cconroy.kukicha"
	ConfigFileName = "kukicha.toml"
	UnknownItem    = "__Uknown"
)

type FileType int

const (
	FileTypeAudio FileType = iota
	FileTypePlaylist
	FileTypeSidecar
)

var RecognizedAudioExtensions = map[string]bool{
	".flac": true,
	".m4a":  true,
	".m4p":  true,
	".mp3":  true,
	".oga":  true,
	".ogg":  true,
	".opus": true,
}

var RecognizedPlaylistExtensions = map[string]bool{
	".m3u":  true,
	".m3u8": true,
	".pls":  true,
}

var RecognizedSidecarArtwork = map[string]bool{
	"cover":    true,
	"folder":   true,
	"front":    true,
	"album":    true,
	"artwork":  true,
	"albumart": true,
}

var RecognizedSidecarExtensions = map[string]bool{
	".jpg":  true,
	".jpeg": true,
	".png":  true,
	".webp": true,
	".gif":  true,
}

type AppError struct {
	Msg string
	Err error
}

func GiveUp(err error) *AppError {
	return &AppError{
		Msg: "I gave up because of the following error.",
		Err: err,
	}
}

func HeresWhy(msg string) *AppError {
	return &AppError{
		Msg: msg,
	}
}

func HeresWhyErr(msg string, err error) *AppError {
	return &AppError{
		Msg: msg,
		Err: err,
	}
}

func (self *AppError) Error() string {
	if self.Err == nil {
		return self.Msg
	}

	return fmt.Sprintf("%s\n%v", self.Msg, self.Err)
}

func (self *AppError) Unwrap() error {
	return self.Err
}

type AppConfig struct {
	AppDir   string
	Roots    []Root
	Settings struct {
		Roots []string `toml:"roots"`
	}
}

type Root struct {
	Pos      int
	Path     string
	IsDir    bool
	FileType FileType
}

func LoadAppConfig() (*AppConfig, error) {
	appDir, path, err := ensureConfigFile()
	if err != nil {
		return nil, GiveUp(err)
	}

	data, err := os.ReadFile(path)
	if err != nil {
		return nil, GiveUp(err)
	}

	appConfig := &AppConfig{AppDir: appDir}

	if len(data) == 0 {
		return appConfig, nil
	}

	if _, err := toml.Decode(string(data), &appConfig.Settings); err != nil {
		return nil, GiveUp(err)
	}

	for pos, root := range appConfig.Settings.Roots {
		if isDir, norm, fileType, err := normalizeRoot(appConfig.AppDir, root); err != nil {
			return nil, err
		} else {
			newRoot := Root{Pos: pos, Path: norm, IsDir: isDir, FileType: fileType}
			appConfig.Roots = append(appConfig.Roots, newRoot)
		}
	}

	return appConfig, nil
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

func normalizeRoot(baseDir, root string) (isDir bool, result string, filetype FileType, err error) {
	switch {
	case root == "":
		err = HeresWhy("I cannot accept a empty root path")
		return
	case strings.HasPrefix(root, "~"):
		var home string

		if home, err = os.UserHomeDir(); err != nil {
			err = HeresWhyErr(fmt.Sprintf("I could not resolve user's home dir in root %q", root), err)
			return
		} else {
			result = filepath.Join(home, strings.TrimPrefix(root, "~"))
		}
	case !filepath.IsAbs(root):
		result = filepath.Join(baseDir, root)
	default:
		result = root
	}

	result = filepath.Clean(result)
	info, err := os.Lstat(result)

	if err != nil {
		switch {
		case errors.Is(err, fs.ErrNotExist):
			err = HeresWhy(fmt.Sprintf(
				"The root %q you gave me does not exists",
				root,
			))
		default:
			err = GiveUp(err)
		}
		return
	}

	mode := info.Mode()

	if mode&os.ModeSymlink != 0 {
		result, err = filepath.EvalSymlinks(result)
		if err != nil {
			err = GiveUp(err)
			return
		}

		info, err = os.Lstat(result)
		if err != nil {
			err = GiveUp(err)
			return
		}
		mode = info.Mode()
	}

	if !info.IsDir() && !mode.IsRegular() {
		err = HeresWhy(fmt.Sprintf(
			"I found the root %q is neither a file nor a folder, with file type %v",
			root, mode.Type(),
		))
		return
	}

	if mode.IsRegular() {
		var ok bool
		filetype, ok = IsRecognizable(root)

		switch {
		case !ok:
			err = HeresWhy(fmt.Sprintf(
				"The root %q is not a recognizable file",
				root,
			))
			return
		case filetype != FileTypeAudio && filetype != FileTypePlaylist:
			err = HeresWhy(fmt.Sprintf(
				"The root %q (as a file) can only be an audio or playlist file",
				root,
			))
			return
		}
	}

	return info.IsDir(), result, filetype, err
}

func IsRecognizable(name string) (filetype FileType, ok bool) {
	ok = false
	base := filepath.Base(name)
	ext := filepath.Ext(name)

	for ext_ := range RecognizedAudioExtensions {
		if strings.EqualFold(ext, ext_) {
			ok = true
			filetype = FileTypeAudio
			return
		}
	}

	for ext_ := range RecognizedPlaylistExtensions {
		if strings.EqualFold(ext, ext_) {
			ok = true
			filetype = FileTypePlaylist
			return
		}
	}

	sidecar := strings.TrimSuffix(base, ext)

	for ext_ := range RecognizedSidecarExtensions {
		if strings.EqualFold(ext, ext_) {
			for sidecar_ := range RecognizedSidecarArtwork {
				if strings.EqualFold(sidecar, sidecar_) {
					ok = true
					filetype = FileTypeSidecar
					return
				}
			}
		}
	}

	return
}
