package web

import (
	"embed"
	"html/template"
	"io/fs"
)

//go:embed templates/*.html static/*
var embeddedFiles embed.FS

var pageTemplates = template.Must(
	template.ParseFS(embeddedFiles, "templates/*.html"),
)

var staticFiles = mustSub(embeddedFiles, "static")

var AlbumArtwork64FallbackArtwork = mustReadFile(staticFiles, "album-fallback-64.svg")
var AlbumArtwork256FallbackArtwork = mustReadFile(staticFiles, "album-fallback-256.svg")

func mustSub(source fs.FS, directory string) fs.FS {
	sub, err := fs.Sub(source, directory)
	if err != nil {
		panic(err)
	}

	return sub
}

func mustReadFile(source fs.FS, path string) []byte {
	data, err := fs.ReadFile(source, path)
	if err != nil {
		panic(err)
	}

	return data
}
