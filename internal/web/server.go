package web

import (
	"database/sql"
	"encoding/json"
	"errors"
	"io/fs"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
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
	Artists []homeLink
	Genres  []homeLink
	Styles  []homeLink
}

type homeLink struct {
	Name string
	URL  string
}

func (c *appRoutes) showHome(w http.ResponseWriter, r *http.Request) {
	home, err := query.Home(r.Context(), c.db)
	if err != nil {
		internalServerError(w, "unable to load home page library data", "error", err)
		return
	}

	renderPage(w, c.renderer, "home.gohtml", homeViewModel{
		sharedURLs: getSharedURLs(c.appBasePath),
		Artists: homeLinks(home.Artists, func(id int64) string {
			return artistURL(c.appBasePath, id)
		}),
		Genres: homeLinks(home.Genres, func(id int64) string {
			return genreURL(c.appBasePath, id)
		}),
		Styles: homeLinks(home.Styles, func(id int64) string {
			return styleURL(c.appBasePath, id)
		}),
	})
}

func homeLinks(items []query.HomeItem, itemURL func(int64) string) []homeLink {
	links := make([]homeLink, 0, len(items))
	for _, item := range items {
		links = append(links, homeLink{
			Name: item.Name,
			URL:  itemURL(item.ID),
		})
	}

	return links
}

type libraryViewModel struct {
	sharedURLs
	Albums []albumCard
}

type albumCard struct {
	Title        string
	Artists      []albumCategoryLink
	SmallArtwork artworkViewModel
	LargeArtwork artworkViewModel
	AlbumURL     string
}

func (c *appRoutes) showLibrary(w http.ResponseWriter, r *http.Request) {
	albums, err := query.Albums(r.Context(), c.db)
	if err != nil {
		internalServerError(w, "unable to load albums", "error", err)
		return
	}

	albumCards := make([]albumCard, 0, len(albums))

	for _, album := range albums {
		albumCards = append(albumCards, albumCard{
			Title:        album.Title,
			Artists:      albumArtistLinks(album.Artists, c.appBasePath),
			SmallArtwork: newArtworkViewModel(c.appBasePath, album.ID, query.AlbumArtwork64, album.SmallArtwork),
			LargeArtwork: newArtworkViewModel(c.appBasePath, album.ID, query.AlbumArtwork256, album.LargeArtwork),
			AlbumURL:     albumURL(c.appBasePath, album.ID),
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

type artworkViewModel struct {
	URL    string
	Width  int
	Height int
}

func newArtworkViewModel(appBasePath string, albumID int64, size query.AlbumArtworkSize, dimensions query.ArtworkDimensions) artworkViewModel {
	return artworkViewModel{
		URL:    albumArtworkURL(appBasePath, albumID, size),
		Width:  dimensions.Width,
		Height: dimensions.Height,
	}
}

type albumViewModel struct {
	sharedURLs
	Album         *query.Album
	SmallArtwork  artworkViewModel
	LargeArtwork  artworkViewModel
	AlbumPageData string
	Genres        []albumCategoryLink
	Styles        []albumCategoryLink
}

func albumArtistLinks(artists []query.AlbumArtist, appBasePath string) []albumCategoryLink {
	links := make([]albumCategoryLink, 0, len(artists))
	for _, artist := range artists {
		links = append(links, albumCategoryLink{Name: artist.Name, URL: artistURL(appBasePath, artist.ID)})
	}
	return links
}

type albumCategoryLink struct {
	Name string
	URL  string
}

func albumCategoryLinks(items []query.AlbumCategory, itemURL func(int64) string) []albumCategoryLink {
	links := make([]albumCategoryLink, 0, len(items))
	for _, item := range items {
		links = append(links, albumCategoryLink{Name: item.Name, URL: itemURL(item.ID)})
	}
	return links
}

type playerAlbumPageData struct {
	Tracks []playerAlbumPageTrack `json:"tracks"`
}

type playerAlbumPageTrack struct {
	ID              string   `json:"id"`
	AudioURL        string   `json:"audioURL"`
	Title           string   `json:"title"`
	Artists         string   `json:"artists"`
	Album           string   `json:"album"`
	AlbumURL        string   `json:"albumURL"`
	AlbumArtists    []string `json:"albumArtists"`
	ArtworkURL      string   `json:"artworkURL"`
	ArtworkWidth    int      `json:"artworkWidth"`
	ArtworkHeight   int      `json:"artworkHeight"`
	DurationSeconds int64    `json:"durationSeconds"`
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
		internalServerError(
			w,
			"unable to load album",
			"album_id", albumID,
			"error", err,
		)
		return
	}

	smallArtwork := newArtworkViewModel(c.appBasePath, album.ID, query.AlbumArtwork64, album.SmallArtwork)
	largeArtwork := newArtworkViewModel(c.appBasePath, album.ID, query.AlbumArtwork256, album.LargeArtwork)
	albumPageDataJSON, err := json.Marshal(
		newAlbumPageData(album, c.appBasePath, smallArtwork),
	)
	if err != nil {
		internalServerError(
			w,
			"unable to encode player queue",
			"album_id", albumID,
			"error", err,
		)
		return
	}

	renderPage(w, c.renderer, "album.gohtml", albumViewModel{
		sharedURLs:    getSharedURLs(c.appBasePath),
		Album:         &album,
		SmallArtwork:  smallArtwork,
		LargeArtwork:  largeArtwork,
		AlbumPageData: string(albumPageDataJSON),
		Genres: albumCategoryLinks(album.Genres, func(id int64) string {
			return genreURL(c.appBasePath, id)
		}),
		Styles: albumCategoryLinks(album.Styles, func(id int64) string {
			return styleURL(c.appBasePath, id)
		}),
	})
}

func newAlbumPageData(
	album query.Album,
	appBasePath string,
	artwork artworkViewModel,
) playerAlbumPageData {
	artistNames := make([]string, 0, len(album.Artists))
	for _, artist := range album.Artists {
		artistNames = append(artistNames, artist.Name)
	}
	tracks := make([]playerAlbumPageTrack, 0, len(album.Tracks))
	for _, track := range album.Tracks {
		tracks = append(tracks, playerAlbumPageTrack{
			ID:              strconv.FormatInt(track.ID, 10),
			AudioURL:        trackAudioURL(appBasePath, track.ID),
			Title:           track.Title,
			Artists:         strings.Join(track.Artists, " "),
			Album:           album.Title,
			AlbumURL:        albumURL(appBasePath, album.ID),
			AlbumArtists:    artistNames,
			ArtworkURL:      artwork.URL,
			ArtworkWidth:    artwork.Width,
			ArtworkHeight:   artwork.Height,
			DurationSeconds: track.DurationSeconds,
		})
	}

	return playerAlbumPageData{Tracks: tracks}
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
		internalServerError(
			w,
			"unable to load album artwork",
			"album_id", albumID,
			"error", err,
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
		slog.WarnContext(
			r.Context(),
			"album has invalid resolved artwork",
			"album_id", albumID,
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
		internalServerError(
			w,
			"unable to load track audio information",
			"track_id", trackID,
			"error", err,
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
		internalServerError(
			w,
			"unable to resolve track audio path",
			"track_id", trackID,
			"error", err,
		)
		return
	}

	file, err := os.Open(audioPath)
	switch {
	case errors.Is(err, fs.ErrNotExist):
		http.Error(w, "Track not found", http.StatusNotFound)
		return

	case err != nil:
		internalServerError(
			w,
			"unable to open track audio",
			"track_id", trackID,
			"error", err,
		)
		return
	}
	defer file.Close()

	info, err := file.Stat()
	if err != nil {
		internalServerError(
			w,
			"unable to inspect track audio",
			"track_id", trackID,
			"error", err,
		)
		return
	}

	if !info.Mode().IsRegular() {
		slog.WarnContext(
			r.Context(),
			"refusing non-regular track audio file",
			"track_id", trackID,
			"path", audioPath,
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
		internalServerError(
			w,
			"unable to begin instance identicon query",
			"error", err,
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
		internalServerError(
			w,
			"unable to load instance identicon",
			"error", err,
		)
		return
	}

	if err := tx.Commit(); err != nil {
		internalServerError(
			w,
			"unable to commit instance identicon query",
			"error", err,
		)
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

func internalServerError(w http.ResponseWriter, message string, args ...any) {
	slog.Error(message, args...)
	http.Error(
		w,
		"An unexpected error occured. "+
			"If you are a web admin see the server logs for more details.",
		http.StatusInternalServerError,
	)
}
