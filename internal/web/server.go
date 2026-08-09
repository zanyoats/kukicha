package web

import (
	"database/sql"
	"errors"
	"fmt"
	"io/fs"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

func NewWebRouter(appBasePath string, renderer Renderer, db *sql.DB) http.Handler {
	mux := http.NewServeMux()

	mux.Handle(
		"/",
		newRootRoutes(db),
	)

	appHandler := mountApp(
		appBasePath,
		newAppRoutes(appBasePath, renderer, db),
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
	renderer    Renderer
	db          *sql.DB
}

func newAppRoutes(appBasePath string, renderer Renderer, db *sql.DB) http.Handler {
	router := appRoutes{appBasePath, renderer, db}

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

type homeViewModel struct {
	sharedURLs
}

func (c *appRoutes) showHome(w http.ResponseWriter, r *http.Request) {
	renderPage(w, c.renderer, "home.gohtml", homeViewModel{
		sharedURLs: getSharedURLs(c.appBasePath),
	})
}

type libraryViewModel struct {
	sharedURLs
	Albums []albumCard
}

type albumCard struct {
	Title      string
	Artists    []string
	ArtworkURL string
	AlbumURL   string
}

func (c *appRoutes) showLibrary(w http.ResponseWriter, r *http.Request) {
	albums, err := query.Albums(r.Context(), c.db)
	if err != nil {
		internalServerError(w, fmt.Sprintf(
			"Unable to load albums: %v", err,
		))
		return
	}

	albumCards := make([]albumCard, 0, len(albums))

	for _, album := range albums {
		albumCards = append(albumCards, albumCard{
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

	renderPage(w, c.renderer, "library.gohtml", libraryViewModel{
		sharedURLs: getSharedURLs(c.appBasePath),
		Albums:     albumCards,
	})
}

type searchViewModel struct {
	sharedURLs
}

func (c *appRoutes) showSearch(w http.ResponseWriter, r *http.Request) {
	renderPage(w, c.renderer, "search.gohtml", searchViewModel{
		sharedURLs: getSharedURLs(c.appBasePath),
	})
}

type albumViewModel struct {
	sharedURLs
	Album           *query.Album
	AlbumArtworkURL string
	TrackAudioURLs  map[int64]string
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
		internalServerError(w, fmt.Sprintf(
			"Unable to load album %d: %v", albumID, err,
		))
		return
	}

	trackAudioURLs := make(map[int64]string, len(album.Tracks))
	for _, track := range album.Tracks {
		trackAudioURLs[track.ID] = trackAudioURL(
			c.appBasePath,
			track.ID,
		)
	}

	renderPage(w, c.renderer, "album.gohtml", albumViewModel{
		sharedURLs: getSharedURLs(c.appBasePath),
		Album:      &album,
		AlbumArtworkURL: albumArtworkURL(
			c.appBasePath,
			album.ID,
			query.AlbumArtwork256,
		),
		TrackAudioURLs: trackAudioURLs,
	})
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
			renderImage(w, "image/svg+xml", AlbumArtwork256FallbackArtwork)
		case query.AlbumArtwork64:
			renderImage(w, "image/svg+xml", AlbumArtwork64FallbackArtwork)
		}
		return

	case err != nil:
		internalServerError(w, fmt.Sprintf(
			"Unable to load artwork for album %d: %v",
			albumID, err,
		))
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
			renderImage(w, "image/svg+xml", AlbumArtwork256FallbackArtwork)
		case query.AlbumArtwork64:
			renderImage(w, "image/svg+xml", AlbumArtwork64FallbackArtwork)
		}
		return
	}

	renderImage(w, contentType, artwork.Bytes)
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
		internalServerError(w, fmt.Sprintf(
			"Unable to load audio information for track %d: %v",
			trackID, err,
		))
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
		internalServerError(w, fmt.Sprintf(
			"Unable to resolve audio path for track %d: %v",
			trackID, err,
		))
		return
	}

	file, err := os.Open(audioPath)
	switch {
	case errors.Is(err, fs.ErrNotExist):
		http.Error(w, "Track not found", http.StatusNotFound)
		return

	case err != nil:
		internalServerError(w, fmt.Sprintf(
			"Unable to open audio for track %d: %v",
			trackID, err,
		))
		return
	}
	defer file.Close()

	info, err := file.Stat()
	if err != nil {
		internalServerError(w, fmt.Sprintf(
			"Unable to inspect audio for track %d: %v",
			trackID, err,
		))
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
		internalServerError(w, fmt.Sprintf(
			"Unable to begin the instance identicon query: %v", err,
		))
		return
	}
	defer tx.Rollback()

	identicon, err := query.AppMetadata(
		r.Context(),
		tx,
		app.InstanceIdenticon,
	)
	if err != nil {
		internalServerError(w, fmt.Sprintf(
			"Unable to load the instance identicon: %v", err,
		))
		return
	}

	if err := tx.Commit(); err != nil {
		internalServerError(w, fmt.Sprintf(
			"Unable to commit the instance identicon query: %v", err,
		))
		return
	}

	renderImage(w, "image/svg+xml", []byte(identicon))
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

func internalServerError(w http.ResponseWriter, logmsg string) {
	log.Print(logmsg)
	http.Error(
		w,
		"An unexpected error occured. "+
			"If you are a web admin see the server logs for more details.",
		http.StatusInternalServerError,
	)
}
