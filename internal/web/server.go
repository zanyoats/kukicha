package web

import (
	"bytes"
	"database/sql"
	"errors"
	"log"
	"net/http"
	"strconv"

	"github.com/zanyoats/kukicha/internal/usecase/query"
)

type pageData struct {
	AppBasePath         string
	UnpolyStylesheetURL string
	StylesheetURL       string
	UnpolyJavaScriptURL string
	PlayerJavaScriptURL string
	PageTitle           string
	Album               *query.Album
}

func newPageData(appBasePath string) pageData {
	return pageData{
		AppBasePath:         appBasePath,
		UnpolyStylesheetURL: pathAtAppBase(appBasePath, "/assets/unpoly.min.css"),
		StylesheetURL:       pathAtAppBase(appBasePath, "/assets/app.css"),
		UnpolyJavaScriptURL: pathAtAppBase(appBasePath, "/assets/unpoly.min.js"),
		PlayerJavaScriptURL: pathAtAppBase(appBasePath, "/assets/player.js"),
	}
}

func NewHandler(appBasePath string, db *sql.DB) http.Handler {
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

	albumPattern := pathAtAppBase(appBasePath, "/album")
	mux.HandleFunc(
		"GET "+albumPattern,
		func(w http.ResponseWriter, r *http.Request) {
			albumPage(w, r, appBasePath, db)
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
	data := newPageData(appBasePath)

	renderPage(w, "layout.html", data)
}

func albumPage(
	w http.ResponseWriter,
	r *http.Request,
	appBasePath string,
	db *sql.DB,
) {
	albumIDValues, found := r.URL.Query()["album_id"]
	if !found || len(albumIDValues) != 1 {
		http.Error(w, "A single album_id is required", http.StatusBadRequest)
		return
	}

	albumID, err := strconv.ParseInt(albumIDValues[0], 10, 64)
	if err != nil || albumID < 1 {
		http.Error(
			w,
			"album_id must be a positive integer",
			http.StatusBadRequest,
		)
		return
	}

	album, err := query.AlbumByID(r.Context(), db, albumID)
	if errors.Is(err, query.ErrAlbumNotFound) {
		http.Error(w, "Album not found", http.StatusNotFound)
		return
	} else if err != nil {
		log.Printf("Unable to load album %d: %v", albumID, err)
		http.Error(
			w,
			"Unable to load the album",
			http.StatusInternalServerError,
		)
		return
	}

	data := newPageData(appBasePath)
	data.PageTitle = album.Title
	data.Album = &album

	renderPage(w, "layout.html", data)
}

func renderPage(
	w http.ResponseWriter,
	templateName string,
	data pageData,
) {
	var rendered bytes.Buffer

	if err := pageTemplates.ExecuteTemplate(
		&rendered,
		templateName,
		data,
	); err != nil {
		log.Printf(
			"Unable to render template %q: %v",
			templateName,
			err,
		)
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
