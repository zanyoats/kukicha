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

func mustSub(source fs.FS, directory string) fs.FS {
	sub, err := fs.Sub(source, directory)
	if err != nil {
		panic(err)
	}

	return sub
}
