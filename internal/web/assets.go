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

var albumFullFallbackArtwork = mustReadFile(staticFiles, "album-fallback-full.svg")
var albumThumbFallbackArtwork = mustReadFile(staticFiles, "album-fallback-thumb.svg")

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
