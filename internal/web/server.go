package web

import (
	"bytes"
	"net/http"
)

type pageData struct {
	AppBasePath   string
	StylesheetURL string
}

func NewHandler(appBasePath string) http.Handler {
	mux := http.NewServeMux()

	appRootPattern := appBasePath
	if appRootPattern == "/" {
		appRootPattern = "/{$}"
	}

	mux.HandleFunc(
		"GET "+appRootPattern,
		func(w http.ResponseWriter, r *http.Request) {
			appRoot(w, r, appBasePath)
		},
	)

	assetsMountPath := pathAtAppBase(appBasePath, "/assets")

	mux.Handle(
		"GET "+assetsMountPath+"/",
		http.StripPrefix(
			assetsMountPath,
			http.FileServer(http.FS(staticFiles)),
		),
	)

	return mux
}

func appRoot(
	w http.ResponseWriter,
	_ *http.Request,
	appBasePath string,
) {
	data := pageData{
		AppBasePath:   appBasePath,
		StylesheetURL: pathAtAppBase(appBasePath, "/assets/app.css"),
	}

	var rendered bytes.Buffer

	if err := pageTemplates.ExecuteTemplate(
		&rendered,
		"layout.html",
		data,
	); err != nil {
		http.Error(
			w,
			"Unable to render the application",
			http.StatusInternalServerError,
		)
		return
	}

	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(http.StatusOK)

	_, _ = rendered.WriteTo(w)
}

func pathAtAppBase(appBasePath, suffix string) string {
	if appBasePath == "/" {
		return suffix
	}

	return appBasePath + suffix
}
