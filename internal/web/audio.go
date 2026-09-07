package web

import (
	"errors"
	"io/fs"
	"log/slog"
	"mime"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	"github.com/zanyoats/kukicha/internal/usecase/query"
)

var errUnsafeTrackAudioPath = errors.New("unsafe track audio path")

func (c *appRoutes) streamTrackAudio(w http.ResponseWriter, r *http.Request) {
	trackID, err := parseTrackID(r)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	audio, err := query.TrackAudioByID(
		r.Context(),
		c.db,
		trackID,
	)
	switch {
	case errors.Is(err, query.ErrTrackNotFound):
		http.Error(w, "Track not found", http.StatusNotFound)
		return

	case err != nil:
		internalServerError(
			w,
			"unable to load track audio information",
			"track_id", trackID,
			"error", err,
		)
		return
	}

	audioPath, err := resolveTrackAudioPath(
		audio.RootPath,
		audio.RelativePath,
	)
	switch {
	case errors.Is(err, fs.ErrNotExist):
		http.Error(w, "Track not found", http.StatusNotFound)
		return

	case err != nil:
		internalServerError(
			w,
			"unable to resolve track audio path",
			"track_id", trackID,
			"error", err,
		)
		return
	}

	file, err := os.Open(audioPath)
	switch {
	case errors.Is(err, fs.ErrNotExist):
		http.Error(w, "Track not found", http.StatusNotFound)
		return

	case err != nil:
		internalServerError(
			w,
			"unable to open track audio",
			"track_id", trackID,
			"error", err,
		)
		return
	}
	defer file.Close()

	info, err := file.Stat()
	if err != nil {
		internalServerError(
			w,
			"unable to inspect track audio",
			"track_id", trackID,
			"error", err,
		)
		return
	}

	if !info.Mode().IsRegular() {
		slog.WarnContext(
			r.Context(),
			"refusing non-regular track audio file",
			"track_id", trackID,
			"path", audioPath,
		)
		http.Error(w, "Track not found", http.StatusNotFound)
		return
	}

	w.Header().Set(
		"Content-Type",
		trackAudioContentType(audio.Extension),
	)
	w.Header().Set("X-Content-Type-Options", "nosniff")

	http.ServeContent(
		w,
		r,
		filepath.Base(audio.RelativePath),
		info.ModTime(),
		file,
	)
}

func resolveTrackAudioPath(
	rootPath string,
	relativePath string,
) (string, error) {
	if !filepath.IsAbs(rootPath) ||
		!filepath.IsLocal(relativePath) {
		return "", errUnsafeTrackAudioPath
	}

	resolvedRoot, err := filepath.EvalSymlinks(
		filepath.Clean(rootPath),
	)
	if err != nil {
		return "", err
	}

	candidate := filepath.Join(
		resolvedRoot,
		relativePath,
	)

	resolvedCandidate, err := filepath.EvalSymlinks(candidate)
	if err != nil {
		return "", err
	}

	pathWithinRoot, err := filepath.Rel(
		resolvedRoot,
		resolvedCandidate,
	)
	if err != nil {
		return "", errUnsafeTrackAudioPath
	}

	escapesRoot := pathWithinRoot == ".." ||
		strings.HasPrefix(
			pathWithinRoot,
			".."+string(filepath.Separator),
		)

	if escapesRoot {
		return "", errUnsafeTrackAudioPath
	}

	return resolvedCandidate, nil
}

func trackAudioContentType(extension string) string {
	switch strings.ToLower(extension) {
	case ".mp3":
		return "audio/mpeg"

	case ".m4a", ".m4p":
		return "audio/mp4"

	case ".m4v":
		return "video/mp4"

	case ".flac":
		return "audio/flac"

	case ".oga", ".ogg", ".opus":
		return "audio/ogg"
	}

	if contentType := mime.TypeByExtension(extension); contentType != "" {
		return contentType
	}

	return "application/octet-stream"
}
