package web

import (
	"embed"
	"errors"
	"fmt"
	"html/template"
	"io"
	"io/fs"
	"path"
)

var (
	//go:embed templates
	templates embed.FS
)

type Renderer interface {
	Render(w io.Writer, p string, m any) error
}

type PageRenderer struct {
	pages map[string]*template.Template
}

func NewPageRenderer() (Renderer, error) {
	base, err := template.
		New("site").
		Funcs(template.FuncMap{
			"formatDate":     formatDate,
			"formatDuration": formatDuration,
			"yesNo":          yesNo,
		}).
		ParseFS(
			templates,
			"templates/layout.gohtml",
			"templates/partials/*.gohtml",
		)
	if err != nil {
		return nil, fmt.Errorf(
			"error in initializing templates: %w",
			err,
		)
	}

	pagePaths, err := fs.Glob(templates, "templates/pages/*.gohtml")
	if err != nil {
		return nil, fmt.Errorf(
			"error in getting page templates: %w",
			err,
		)
	}
	if len(pagePaths) == 0 {
		return nil, errors.New("expect to have found pages to render")
	}

	pages := make(map[string]*template.Template, len(pagePaths))

	for _, p := range pagePaths {
		clone, err := base.Clone()
		if err != nil {
			return nil, fmt.Errorf(
				"clone page error for %q: %w",
				p, err,
			)
		}

		page, err := clone.ParseFS(templates, p)
		if err != nil {
			return nil, fmt.Errorf(
				"page parse error for %q: %w",
				p, err,
			)
		}

		pageName := path.Base(p)
		pages[pageName] = page
	}

	return &PageRenderer{pages}, nil
}

func (r *PageRenderer) Render(w io.Writer, p string, m any) error {
	t, ok := r.pages[p]
	if !ok {
		return fmt.Errorf("page %q was not found", p)
	}

	if err := t.ExecuteTemplate(w, "layout", m); err != nil {
		return fmt.Errorf("error executing template for page %q: %w", p, err)
	}

	return nil
}
