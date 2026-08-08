package web

import (
	"embed"
	"html/template"
	"io/fs"
)

//go:embed templates/*.html static/*
var embeddedFiles embed.FS

var pageTemplates = map[string]*template.Template{
	"album":   mustParsePageTemplate("album"),
	"home":    mustParsePageTemplate("home"),
	"library": mustParsePageTemplate("library"),
	"search":  mustParsePageTemplate("search"),
}

var staticFiles = mustSub(embeddedFiles, "static")

var AlbumArtwork64FallbackArtwork = mustReadFile(staticFiles, "album-fallback-64.svg")
var AlbumArtwork256FallbackArtwork = mustReadFile(staticFiles, "album-fallback-256.svg")

func mustParsePageTemplate(pageName string) *template.Template {
	return template.Must(template.New(pageName).Funcs(template.FuncMap{
		"yesNo": yesNo,
	}).ParseFS(
		embeddedFiles,
		"templates/layout.html",
		"templates/"+pageName+".html",
	))
}

func yesNo(value *bool) string {
	if value != nil && *value {
		return "Yes"
	}

	return "No"
}

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
