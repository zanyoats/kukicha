package web

import (
	"bytes"
	"database/sql"
	"errors"
	"fmt"
	"io/fs"
	"log"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"

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

func NewWebRouter(appBasePath string, db *sql.DB) http.Handler {
	mux := http.NewServeMux()

	mux.Handle(
		"/",
		newRootRoutes(db),
	)
	appHandler := mountApp(
		appBasePath,
		newAppRoutes(appBasePath, db),
	)
	mux.Handle(appBasePath, appHandler)
	mux.Handle(appBasePath+"/", appHandler)

	return mux
}

func newRootRoutes(db *sql.DB) http.Handler {
	mux := http.NewServeMux()

	mux.HandleFunc("GET /favicon.ico", func(w http.ResponseWriter, r *http.Request) {
		getIdenticon(db, w, r)
	})

	return mux
}

type appRoutes struct {
	appBasePath string
	db          *sql.DB
}

func newAppRoutes(appBasePath string, db *sql.DB) http.Handler {
	router := appRoutes{appBasePath, db}

	mux := http.NewServeMux()

	mux.HandleFunc("GET /{$}", router.showHome)
	mux.Handle(
		"GET /assets/",
		http.StripPrefix(
			"/assets/",
			http.FileServer(http.FS(staticFiles)),
		),
	)
	mux.HandleFunc("GET /library", router.showLibrary)
	mux.HandleFunc("GET /library/album", router.showAlbum)
	mux.HandleFunc("GET /library/album/artwork", router.getAlbumArtwork)
	mux.HandleFunc("GET /library/album/tracks/audio", router.streamTrackAudio)
	mux.HandleFunc("GET /search", router.showSearch)
	mux.HandleFunc("GET /instance/identicon.svg", func(w http.ResponseWriter, r *http.Request) {
		getIdenticon(db, w, r)
	})

	return mux
}

func (c *appRoutes) showHome(w http.ResponseWriter, r *http.Request) {
	data := newPageData(c.appBasePath)
	data.PageTitle = "Home"

	renderPage(w, "home", data)
}

func (c *appRoutes) showLibrary(w http.ResponseWriter, r *http.Request) {
	albums, err := query.Albums(r.Context(), c.db)
	if err != nil {
		log.Printf("Unable to load albums: %v", err)
		http.Error(
			w,
			"Unable to load the albums",
			http.StatusInternalServerError,
		)
		return
	}

	data := newPageData(c.appBasePath)
	data.PageTitle = "Library"
	data.Albums = make([]albumCard, 0, len(albums))

	for _, album := range albums {
		data.Albums = append(data.Albums, albumCard{
			Title:   album.Title,
			Artists: album.Artists,
			ArtworkURL: albumArtworkURL(
				c.appBasePath,
				album.ID,
				query.AlbumArtwork64,
			),
			AlbumURL: albumURL(c.appBasePath, album.ID),
		})
	}

	renderPage(w, "library", data)
}

func (c *appRoutes) showSearch(w http.ResponseWriter, r *http.Request) {
	data := newPageData(c.appBasePath)
	data.PageTitle = "Search"

	renderPage(w, "search", data)
}

func (c *appRoutes) showAlbum(w http.ResponseWriter, r *http.Request) {
	albumID, err := parseAlbumID(r)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	album, err := query.AlbumByID(r.Context(), c.db, albumID)
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

	data := newPageData(c.appBasePath)
	data.PageTitle = album.Title
	data.Album = &album
	data.AlbumArtworkURL = albumArtworkURL(
		c.appBasePath,
		album.ID,
		query.AlbumArtwork256,
	)
	data.TrackAudioURLs = make(map[int64]string, len(album.Tracks))
	for _, track := range album.Tracks {
		data.TrackAudioURLs[track.ID] = trackAudioURL(
			c.appBasePath,
			track.ID,
		)
	}

	renderPage(w, "album", data)
}

func (c *appRoutes) getAlbumArtwork(w http.ResponseWriter, r *http.Request) {
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
		c.db,
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

func (c *appRoutes) streamTrackAudio(w http.ResponseWriter, r *http.Request) {
	trackID, err := parseTrackID(r)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	audio, err := query.TrackAudioByID(
		r.Context(),
		c.db,
		trackID,
	)
	switch {
	case errors.Is(err, query.ErrTrackNotFound):
		http.Error(w, "Track not found", http.StatusNotFound)
		return

	case err != nil:
		log.Printf(
			"Unable to load audio information for track %d: %v",
			trackID,
			err,
		)
		http.Error(
			w,
			"Unable to load track audio",
			http.StatusInternalServerError,
		)
		return
	}

	audioPath, err := resolveTrackAudioPath(
		audio.RootPath,
		audio.RelativePath,
	)
	switch {
	case errors.Is(err, fs.ErrNotExist):
		http.Error(w, "Track not found", http.StatusNotFound)
		return

	case err != nil:
		log.Printf(
			"Unable to resolve audio path for track %d: %v",
			trackID,
			err,
		)
		http.Error(
			w,
			"Unable to load track audio",
			http.StatusInternalServerError,
		)
		return
	}

	file, err := os.Open(audioPath)
	switch {
	case errors.Is(err, fs.ErrNotExist):
		http.Error(w, "Track not found", http.StatusNotFound)
		return

	case err != nil:
		log.Printf(
			"Unable to open audio for track %d: %v",
			trackID,
			err,
		)
		http.Error(
			w,
			"Unable to load track audio",
			http.StatusInternalServerError,
		)
		return
	}
	defer file.Close()

	info, err := file.Stat()
	if err != nil {
		log.Printf(
			"Unable to inspect audio for track %d: %v",
			trackID,
			err,
		)
		http.Error(
			w,
			"Unable to load track audio",
			http.StatusInternalServerError,
		)
		return
	}

	if !info.Mode().IsRegular() {
		log.Printf(
			"Refusing non-regular audio file for track %d",
			trackID,
		)
		http.Error(w, "Track not found", http.StatusNotFound)
		return
	}

	w.Header().Set(
		"Content-Type",
		trackAudioContentType(audio.Extension),
	)
	w.Header().Set("X-Content-Type-Options", "nosniff")

	http.ServeContent(
		w,
		r,
		filepath.Base(audio.RelativePath),
		info.ModTime(),
		file,
	)
}

func getIdenticon(db *sql.DB, w http.ResponseWriter, r *http.Request) {
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

func mountApp(prefix string, next http.Handler) http.Handler {
	stripped := http.StripPrefix(prefix, next)

	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Redirect trailing-slash forms to the canonical URL.
		if strings.HasSuffix(r.URL.Path, "/") {
			target := strings.TrimRight(r.URL.EscapedPath(), "/")

			if r.URL.RawQuery != "" {
				target += "?" + r.URL.RawQuery
			}

			http.Redirect(
				w,
				r,
				target,
				http.StatusPermanentRedirect,
			)
			return
		}

		// Present the exact mount path to the inner router as "/".
		if r.URL.Path == prefix {
			req := r.Clone(r.Context())
			req.URL.Path = prefix + "/"
			req.URL.RawPath = ""
			stripped.ServeHTTP(w, req)
			return
		}

		// All other cases can be handled by the inner route with any logic
		stripped.ServeHTTP(w, r)
	})
}
