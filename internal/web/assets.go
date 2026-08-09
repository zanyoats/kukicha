package web

import (
	"embed"
	"io/fs"
)

//go:embed static/*
var static embed.FS

var staticFiles = must(fs.Sub(static, "static"))

var AlbumArtwork64FallbackArtwork = must(fs.ReadFile(staticFiles, "album-fallback-64.svg"))
var AlbumArtwork256FallbackArtwork = must(fs.ReadFile(staticFiles, "album-fallback-256.svg"))

func must[T any](value T, err error) T {
	if err != nil {
		panic(err)
	}
	return value
}
