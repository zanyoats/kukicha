package web

import (
	"database/sql"
	"log/slog"
	"net/http"

	"github.com/zanyoats/kukicha/internal/app"
)

type appRoutes struct {
	urls             URLs
	renderer         Renderer
	db               *sql.DB
	perPage          int
	defaultAlbumSort app.AlbumSort
}

func NewWebRouter(
	basePath string,
	renderer Renderer,
	db *sql.DB,
	perPage int,
	defaultAlbumSort app.AlbumSort,
) http.Handler {
	urls := NewURLs(basePath)
	router := appRoutes{
		urls:             urls,
		renderer:         renderer,
		db:               db,
		perPage:          perPage,
		defaultAlbumSort: defaultAlbumSort,
	}
	mux := http.NewServeMux()

	mux.Handle(
		"GET /{$}",
		http.RedirectHandler(urls.Home, http.StatusTemporaryRedirect),
	)
	mux.HandleFunc(
		http.MethodGet+" "+urls.Favicon,
		router.getIdenticon,
	)
	mux.Handle(
		http.MethodGet+" "+urls.Assets,
		http.StripPrefix(
			urls.Assets,
			http.FileServer(http.FS(staticFiles)),
		),
	)
	mux.HandleFunc(
		exactPattern(http.MethodGet, urls.Home),
		router.showHome,
	)
	mux.HandleFunc(
		exactPattern(http.MethodGet, urls.Library),
		router.showLibrary,
	)
	mux.HandleFunc(
		exactPattern(http.MethodGet, urls.AlbumArtwork),
		router.getAlbumArtwork,
	)
	mux.HandleFunc(
		exactPattern(http.MethodGet, urls.TrackAudio),
		router.streamTrackAudio,
	)
	mux.HandleFunc(
		exactPattern(http.MethodGet, urls.Search),
		router.showSearch,
	)
	mux.HandleFunc(
		exactPattern(http.MethodGet, urls.ArtistSplitRules),
		router.showArtistSplitRules,
	)
	mux.HandleFunc(
		exactPattern(http.MethodPatch, urls.UpdateArtistSplitRule),
		router.updateArtistSplitRule,
	)
	mux.HandleFunc(
		exactPattern(http.MethodGet, urls.Roots),
		router.showRoots,
	)
	for _, page := range configPages(urls) {
		mux.HandleFunc(
			exactPattern(http.MethodGet, page.URL),
			router.showConfig(page),
		)
	}
	mux.HandleFunc(
		http.MethodGet+" "+urls.InstanceIdenticon,
		router.getIdenticon,
	)

	return mux
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
