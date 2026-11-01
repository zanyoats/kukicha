package web

import (
	"bytes"
	"crypto/sha256"
	"embed"
	"fmt"
	"io/fs"
	"net/http"
	"strings"
	"time"
)

//go:embed static/*
var static embed.FS

var staticFiles = must(fs.Sub(static, "static"))

const assetsRoot = "/assets/"

var assetsPrefix = assetsRoot + must(hashAssets(staticFiles)) + "/"

// Hash the whole tree so relative module imports share the same version.
// WalkDir visits paths in lexical order; lengths delimit names and contents.
func hashAssets(files fs.FS) (string, error) {
	h := sha256.New()
	err := fs.WalkDir(files, ".", func(name string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if entry.IsDir() {
			return nil
		}
		data, err := fs.ReadFile(files, name)
		if err != nil {
			return err
		}
		fmt.Fprintf(h, "%d:%s%d:", len(name), name, len(data))
		h.Write(data)
		return nil
	})
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("%x", h.Sum(nil)), nil
}

// The router strips the current hashed prefix before calling serveAsset.
func serveAsset(w http.ResponseWriter, r *http.Request) {
	name := strings.TrimPrefix(r.URL.Path, "/")
	data, err := fs.ReadFile(staticFiles, name)
	if err != nil {
		assetNotFound(w, r)
		return
	}
	w.Header().Set("Cache-Control", "public, max-age=31536000, immutable")
	http.ServeContent(w, r, name, time.Time{}, bytes.NewReader(data))
}

func assetNotFound(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	http.NotFound(w, r)
}

var AlbumArtwork64FallbackArtwork = must(fs.ReadFile(staticFiles, "album-fallback-64.svg"))
var AlbumArtwork256FallbackArtwork = must(fs.ReadFile(staticFiles, "album-fallback-256.svg"))

func must[T any](value T, err error) T {
	if err != nil {
		panic(err)
	}
	return value
}
