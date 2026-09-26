package web

import (
	"context"
	"database/sql"
	"encoding/json"
	"log/slog"
	"maps"
	"net/http"
	"slices"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/processor"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

// JobProcessor provides job submission and cancellation for the web handlers.
type JobProcessor interface {
	Submit(context.Context, processor.JobSubmission) (int64, error)
	Cancel(context.Context, int64) (query.Job, error)
}

type appRoutes struct {
	urls                      URLs
	renderer                  Renderer
	db                        *sql.DB
	processor                 JobProcessor
	perPage                   int
	defaultAlbumSort          app.AlbumSort
	configuredRoots           []app.Root
	configFilePath            string
	recognizedAudioExtensions []string
}

func NewWebRouter(
	basePath string,
	renderer Renderer,
	db *sql.DB,
	perPage int,
	defaultAlbumSort app.AlbumSort,
	jobProcessor JobProcessor,
	configuredRoots []app.Root,
	configFilePath string,
	recognizedAudioExtensions map[string]bool,
) http.Handler {
	urls := NewURLs(basePath)
	router := appRoutes{
		urls:                      urls,
		renderer:                  renderer,
		db:                        db,
		processor:                 jobProcessor,
		perPage:                   perPage,
		defaultAlbumSort:          defaultAlbumSort,
		configuredRoots:           configuredRoots,
		configFilePath:            configFilePath,
		recognizedAudioExtensions: slices.Sorted(maps.Keys(recognizedAudioExtensions)),
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
		exactPattern(http.MethodPost, urls.SubmitJob),
		router.submitJob,
	)
	mux.HandleFunc(
		exactPattern(http.MethodPost, urls.CancelJob),
		router.cancelJob,
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

func apiError(w http.ResponseWriter, status int, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": message})
}
