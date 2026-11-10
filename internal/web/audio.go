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
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/usecase/query"
	"github.com/zanyoats/kukicha/internal/walk"
)

var errUnsafeTrackAudioPath = errors.New("unsafe track audio path")

func (c *webRouter) streamTrackAudio(w http.ResponseWriter, r *http.Request) {
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

	c.serveLibraryFile(w, r, audio)
}

// serveLibraryFile serves a database-resolved path using the configured storage root.
func (c *webRouter) serveLibraryFile(w http.ResponseWriter, r *http.Request, audio query.TrackAudio) {
	root := c.rootsByConfigID[audio.ConfigID]
	if root == nil {
		http.Error(w, "File root is no longer configured", http.StatusNotFound)
		return
	}

	if remoteRoot, ok := root.(*app.RemoteRoot); ok {
		c.redirectRemoteFile(w, r, remoteRoot, audio.RelativePath, audio.Extension)
		return
	}
	localRoot, ok := root.(*app.LocalRoot)
	if !ok {
		http.Error(w, "Unsupported file root", http.StatusInternalServerError)
		return
	}
	audioPath, err := resolveTrackAudioPath(
		localRoot.Path,
		audio.RelativePath,
	)
	switch {
	case errors.Is(err, fs.ErrNotExist):
		http.Error(w, "File not found", http.StatusNotFound)
		return

	case err != nil:
		internalServerError(
			w,
			"unable to resolve library file path",
			"path", audio.RelativePath,
			"error", err,
		)
		return
	}

	file, err := os.Open(audioPath)
	switch {
	case errors.Is(err, fs.ErrNotExist):
		http.Error(w, "File not found", http.StatusNotFound)
		return

	case err != nil:
		internalServerError(
			w,
			"unable to open library file",
			"path", audio.RelativePath,
			"error", err,
		)
		return
	}
	defer file.Close()

	info, err := file.Stat()
	if err != nil {
		internalServerError(
			w,
			"unable to inspect library file",
			"path", audio.RelativePath,
			"error", err,
		)
		return
	}

	if !info.Mode().IsRegular() {
		slog.WarnContext(
			r.Context(),
			"refusing non-regular library file file",
			"path", audio.RelativePath,
			"path", audioPath,
		)
		http.Error(w, "File not found", http.StatusNotFound)
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

func (c *webRouter) redirectRemoteFile(
	w http.ResponseWriter,
	r *http.Request,
	root *app.RemoteRoot,
	relativePath, extension string,
) {
	cached, ok := c.remoteAudioClients.Load(root.ConfigID())
	if !ok {
		client, err := walk.NewS3Client(r.Context(), root)
		if err != nil {
			internalServerError(w, "unable to initialize remote playback", "root", root.ConfigID(), "error", err)
			return
		}
		cached, _ = c.remoteAudioClients.LoadOrStore(root.ConfigID(), s3.NewPresignClient(client))
	}
	client := cached.(*s3.PresignClient)
	// Preserve exact S3 key bytes, including repeated slashes and dot segments.
	key := root.Prefix + relativePath
	options := func(o *s3.PresignOptions) { o.Expires = time.Hour }
	var location string
	var err error
	if r.Method == http.MethodHead {
		// GET routes also accept HEAD; signatures are specific to the HTTP method.
		signed, signErr := client.PresignHeadObject(r.Context(), &s3.HeadObjectInput{
			Bucket: aws.String(root.Bucket), Key: aws.String(key),
		}, options)
		err = signErr
		if err == nil {
			location = signed.URL
		}
	} else {
		signed, signErr := client.PresignGetObject(r.Context(), &s3.GetObjectInput{
			Bucket: aws.String(root.Bucket), Key: aws.String(key),
			ResponseContentType: aws.String(trackAudioContentType(extension)),
			// Leave Range unsigned so the browser can seek using the same URL.
		}, options)
		err = signErr
		if err == nil {
			location = signed.URL
		}
	}
	if err != nil {
		internalServerError(w, "unable to sign remote playback URL", "root", root.ConfigID(), "error", err)
		return
	}
	// Each reload must obtain a fresh signature rather than a cached redirect.
	w.Header().Set("Cache-Control", "no-store")
	http.Redirect(w, r, location, http.StatusTemporaryRedirect)
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
