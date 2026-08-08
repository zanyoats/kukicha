package web

import (
	"errors"
	"mime"
	"path/filepath"
	"strings"
)

var errUnsafeTrackAudioPath = errors.New("unsafe track audio path")

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
