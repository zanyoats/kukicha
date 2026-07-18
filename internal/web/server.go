package web

import (
	"bytes"
	"database/sql"
	"errors"
	"fmt"
	"log"
	"net/http"
	"net/url"
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
	AlbumArtworkURL     string
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

	albumArtworkPattern := pathAtAppBase(
		appBasePath,
		"/album/artwork",
	)
	mux.HandleFunc(
		"GET "+albumArtworkPattern,
		func(w http.ResponseWriter, r *http.Request) {
			albumArtwork(w, r, db)
		},
	)

	trackAudioPattern := pathAtAppBase(
		appBasePath,
		"/album/tracks/audio",
	)
	mux.HandleFunc(
		"GET "+trackAudioPattern,
		func(w http.ResponseWriter, r *http.Request) {
			trackAudio(w, r, db)
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
	albumID, err := parseAlbumID(r)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
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
	data.AlbumArtworkURL = albumArtworkURL(
		appBasePath,
		album.ID,
		query.AlbumArtworkFull,
	)

	renderPage(w, "layout.html", data)
}

func albumArtwork(
	w http.ResponseWriter,
	r *http.Request,
	db *sql.DB,
) {
	albumID, err := parseAlbumID(r)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	size, err := parseArtworkSize(r)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	artwork, err := query.AlbumArtworkByID(
		r.Context(),
		db,
		albumID,
		size,
	)

	switch {
	case errors.Is(err, query.ErrAlbumNotFound):
		http.Error(
			w,
			"Album not found",
			http.StatusNotFound,
		)
		return

	case errors.Is(err, query.ErrAlbumArtworkNotFound):
		switch size {
		case query.AlbumArtworkFull:
			writeArtwork(w, "image/svg+xml", albumFullFallbackArtwork)
		case query.AlbumArtworkThumbnail:
			writeArtwork(w, "image/svg+xml", albumThumbFallbackArtwork)
		}
		return

	case err != nil:
		log.Printf(
			"Unable to load artwork for album %d: %v",
			albumID,
			err,
		)
		http.Error(
			w,
			"Unable to load album artwork",
			http.StatusInternalServerError,
		)
		return
	}

	contentType := artwork.ContentType
	if contentType == "" {
		contentType = http.DetectContentType(artwork.Bytes)
	}

	invalidArtwork := len(artwork.Bytes) == 0 ||
		len(contentType) < len("image/") ||
		contentType[:len("image/")] != "image/"

	if invalidArtwork {
		log.Printf(
			"Album %d has invalid resolved artwork",
			albumID,
		)
		switch size {
		case query.AlbumArtworkFull:
			writeArtwork(w, "image/svg+xml", albumFullFallbackArtwork)
		case query.AlbumArtworkThumbnail:
			writeArtwork(w, "image/svg+xml", albumThumbFallbackArtwork)
		}
		return
	}

	writeArtwork(w, contentType, artwork.Bytes)
}

func writeArtwork(
	w http.ResponseWriter,
	contentType string,
	data []byte,
) {
	w.Header().Set("Content-Type", contentType)
	w.Header().Set(
		"Content-Length",
		strconv.Itoa(len(data)),
	)
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.WriteHeader(http.StatusOK)

	_, _ = w.Write(data)
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

func albumArtworkURL(
	appBasePath string,
	albumID int64,
	size query.AlbumArtworkSize,
) string {
	return pathAtAppBase(appBasePath, "/album/artwork") +
		"?album_id=" + strconv.FormatInt(albumID, 10) +
		"&size=" + string(size)
}

func parseAlbumID(r *http.Request) (int64, error) {
	return parsePositiveID(r, "album_id")
}

func parseTrackID(r *http.Request) (int64, error) {
	return parsePositiveID(r, "track_id")
}

func parseArtworkSize(r *http.Request) (query.AlbumArtworkSize, error) {
	value, err := requiredQueryValue(
		r.URL.Query(),
		"size",
	)
	if err != nil {
		return "", err
	}

	switch value {
	case string(query.AlbumArtworkThumbnail):
		return query.AlbumArtworkThumbnail, nil

	case string(query.AlbumArtworkFull):
		return query.AlbumArtworkFull, nil

	default:
		return "", errors.New("size must be thumb or full")
	}
}

func parsePositiveID(
	r *http.Request,
	name string,
) (int64, error) {
	value, err := requiredQueryValue(
		r.URL.Query(),
		name,
	)
	if err != nil {
		return 0, err
	}

	id, err := strconv.ParseInt(value, 10, 64)
	if err != nil || id < 1 {
		return 0, fmt.Errorf(
			"%s must be a positive integer",
			name,
		)
	}

	return id, nil
}

func requiredQueryValue(
	values url.Values,
	name string,
) (string, error) {
	found := values[name]

	if len(found) != 1 || found[0] == "" {
		return "", fmt.Errorf(
			"a single %s is required",
			name,
		)
	}

	return found[0], nil
}
