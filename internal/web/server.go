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

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

type pageData struct {
	HomeURL              string
	LibraryURL           string
	SearchURL            string
	UnpolyStylesheetURL  string
	StylesheetURL        string
	UnpolyJavaScriptURL  string
	PlayerJavaScriptURL  string
	InstanceIdenticonURL string
	PageTitle            string
	SearchQuery          string
	Albums               []albumCard
	Album                *query.Album
	AlbumArtworkURL      string
	TrackAudioURLs       map[int64]string
}

type albumCard struct {
	Title      string
	Artists    []string
	ArtworkURL string
	AlbumURL   string
}

func newPageData(appBasePath string) pageData {
	return pageData{
		HomeURL:              appBasePath,
		LibraryURL:           pathAtAppBase(appBasePath, "/library"),
		SearchURL:            pathAtAppBase(appBasePath, "/search"),
		UnpolyStylesheetURL:  pathAtAppBase(appBasePath, "/assets/unpoly.min.css"),
		StylesheetURL:        pathAtAppBase(appBasePath, "/assets/app.css"),
		UnpolyJavaScriptURL:  pathAtAppBase(appBasePath, "/assets/unpoly.min.js"),
		PlayerJavaScriptURL:  pathAtAppBase(appBasePath, "/assets/player.js"),
		InstanceIdenticonURL: pathAtAppBase(appBasePath, "/instance/identicon.svg"),
	}
}

func NewHandler(appBasePath string, db *sql.DB) http.Handler {
	mux := http.NewServeMux()

	mux.HandleFunc(
		"GET /favicon.ico",
		func(w http.ResponseWriter, r *http.Request) {
			instanceIdenticon(w, r, db)
		},
	)

	appRootPattern := appBasePath
	if appRootPattern == "/" {
		appRootPattern = "/{$}"
	}

	mux.HandleFunc(
		"GET "+appRootPattern,
		func(w http.ResponseWriter, r *http.Request) {
			homePage(w, r, appBasePath)
		},
	)

	libraryPattern := pathAtAppBase(appBasePath, "/library")
	mux.HandleFunc(
		"GET "+libraryPattern,
		func(w http.ResponseWriter, r *http.Request) {
			libraryPage(w, r, appBasePath, db)
		},
	)

	searchPattern := pathAtAppBase(appBasePath, "/search")
	mux.HandleFunc(
		"GET "+searchPattern,
		func(w http.ResponseWriter, r *http.Request) {
			searchPage(w, r, appBasePath)
		},
	)

	albumPattern := pathAtAppBase(appBasePath, "/library/album")
	mux.HandleFunc(
		"GET "+albumPattern,
		func(w http.ResponseWriter, r *http.Request) {
			albumPage(w, r, appBasePath, db)
		},
	)

	albumArtworkPattern := pathAtAppBase(
		appBasePath,
		"/library/album/artwork",
	)
	mux.HandleFunc(
		"GET "+albumArtworkPattern,
		func(w http.ResponseWriter, r *http.Request) {
			albumArtwork(w, r, db)
		},
	)

	trackAudioPattern := pathAtAppBase(
		appBasePath,
		"/library/album/tracks/audio",
	)
	mux.HandleFunc(
		"GET "+trackAudioPattern,
		func(w http.ResponseWriter, r *http.Request) {
			trackAudio(w, r, db)
		},
	)

	instanceIdenticonPattern := pathAtAppBase(
		appBasePath,
		"/instance/identicon.svg",
	)
	mux.HandleFunc(
		"GET "+instanceIdenticonPattern,
		func(w http.ResponseWriter, r *http.Request) {
			instanceIdenticon(w, r, db)
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

func instanceIdenticon(
	w http.ResponseWriter,
	r *http.Request,
	db *sql.DB,
) {
	tx, err := db.BeginTx(
		r.Context(),
		&sql.TxOptions{ReadOnly: true},
	)
	if err != nil {
		log.Printf("Unable to begin the instance identicon query: %v", err)
		http.Error(
			w,
			"Unable to load the instance identicon",
			http.StatusInternalServerError,
		)
		return
	}
	defer tx.Rollback()

	identicon, err := query.AppMetadata(
		r.Context(),
		tx,
		app.InstanceIdenticon,
	)
	if err != nil {
		log.Printf("Unable to load the instance identicon: %v", err)
		http.Error(
			w,
			"Unable to load the instance identicon",
			http.StatusInternalServerError,
		)
		return
	}

	if err := tx.Commit(); err != nil {
		log.Printf("Unable to commit the instance identicon query: %v", err)
		http.Error(
			w,
			"Unable to load the instance identicon",
			http.StatusInternalServerError,
		)
		return
	}

	writeArtwork(w, "image/svg+xml", []byte(identicon))
}

func libraryPage(
	w http.ResponseWriter,
	r *http.Request,
	appBasePath string,
	db *sql.DB,
) {
	albums, err := query.Albums(r.Context(), db)
	if err != nil {
		log.Printf("Unable to load albums: %v", err)
		http.Error(
			w,
			"Unable to load the albums",
			http.StatusInternalServerError,
		)
		return
	}

	data := newPageData(appBasePath)
	data.PageTitle = "Library"
	data.Albums = make([]albumCard, 0, len(albums))

	for _, album := range albums {
		data.Albums = append(data.Albums, albumCard{
			Title:   album.Title,
			Artists: album.Artists,
			ArtworkURL: albumArtworkURL(
				appBasePath,
				album.ID,
				query.AlbumArtwork64,
			),
			AlbumURL: albumURL(appBasePath, album.ID),
		})
	}

	renderPage(w, "library", data)
}

func homePage(
	w http.ResponseWriter,
	_ *http.Request,
	appBasePath string,
) {
	data := newPageData(appBasePath)
	data.PageTitle = "Home"

	renderPage(w, "home", data)
}

func searchPage(
	w http.ResponseWriter,
	r *http.Request,
	appBasePath string,
) {
	data := newPageData(appBasePath)
	data.PageTitle = "Search"
	data.SearchQuery = r.URL.Query().Get("q")

	renderPage(w, "search", data)
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
		query.AlbumArtwork256,
	)
	data.TrackAudioURLs = make(map[int64]string, len(album.Tracks))
	for _, track := range album.Tracks {
		data.TrackAudioURLs[track.ID] = trackAudioURL(
			appBasePath,
			track.ID,
		)
	}

	renderPage(w, "album", data)
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
		case query.AlbumArtwork256:
			writeArtwork(w, "image/svg+xml", AlbumArtwork256FallbackArtwork)
		case query.AlbumArtwork64:
			writeArtwork(w, "image/svg+xml", AlbumArtwork64FallbackArtwork)
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
		case query.AlbumArtwork256:
			writeArtwork(w, "image/svg+xml", AlbumArtwork256FallbackArtwork)
		case query.AlbumArtwork64:
			writeArtwork(w, "image/svg+xml", AlbumArtwork64FallbackArtwork)
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
	pageName string,
	data pageData,
) {
	var rendered bytes.Buffer

	pageTemplate, ok := pageTemplates[pageName]
	if !ok {
		log.Printf("Unable to find page template %q", pageName)
		http.Error(
			w,
			"Unable to render the application",
			http.StatusInternalServerError,
		)
		return
	}

	if err := pageTemplate.ExecuteTemplate(
		&rendered,
		"layout.html",
		data,
	); err != nil {
		log.Printf(
			"Unable to render template %q: %v",
			pageName,
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
	return pathAtAppBase(appBasePath, "/library/album/artwork") +
		"?album_id=" + strconv.FormatInt(albumID, 10) +
		"&size=" + string(size)
}

func albumURL(appBasePath string, albumID int64) string {
	return pathAtAppBase(appBasePath, "/library/album") +
		"?album_id=" + strconv.FormatInt(albumID, 10)
}

func trackAudioURL(
	appBasePath string,
	trackID int64,
) string {
	return pathAtAppBase(
		appBasePath,
		"/library/album/tracks/audio",
	) + "?track_id=" + strconv.FormatInt(trackID, 10)
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
	case string(query.AlbumArtwork64):
		return query.AlbumArtwork64, nil

	case string(query.AlbumArtwork256):
		return query.AlbumArtwork256, nil

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
