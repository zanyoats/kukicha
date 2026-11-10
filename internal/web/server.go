package web

import (
	"context"
	"database/sql"
	"encoding/json"
	"log/slog"
	"maps"
	"net/http"
	"slices"
	"sync"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/auth"
	"github.com/zanyoats/kukicha/internal/processor"
	"github.com/zanyoats/kukicha/internal/upload"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

// JobProcessor provides job submission and cancellation for the web handlers.
type JobProcessor interface {
	Submit(context.Context, processor.JobSubmission) (int64, error)
	Cancel(context.Context, int64) (query.Job, error)
}

// NotificationSubscriber provides live job notifications and subscription cleanup.
type NotificationSubscriber interface {
	Subscribe() (<-chan app.Notification, func())
}

type webRouter struct {
	conf                      *app.AppConfig
	maxUploadRequestBytes     int64
	uploads                   *upload.Service
	auth                      *auth.Auth
	urls                      URLs
	renderer                  Renderer
	db                        *sql.DB
	processor                 JobProcessor
	notifications             NotificationSubscriber
	radioMinTracks            int64
	perPage                   int
	defaultAlbumSort          app.AlbumSort
	configuredRoots           []app.Root
	rootsByConfigID           map[string]app.Root
	remoteAudioClients        sync.Map // root config ID -> *s3.PresignClient
	configFilePath            string
	recognizedAudioExtensions []string
}

func NewWebRouter(
	conf *app.AppConfig,
	basePath string,
	renderer Renderer,
	db *sql.DB,
	perPage int,
	radioMinTracks int64,
	defaultAlbumSort app.AlbumSort,
	jobProcessor JobProcessor,
	notifications NotificationSubscriber,
	configuredRoots []app.Root,
	configFilePath string,
	recognizedAudioExtensions map[string]bool,
	credentials *auth.Auth,
	uploads *upload.Service,
	maxUploadRequestBytes int64,
) http.Handler {
	rootsByConfigID := make(map[string]app.Root, len(configuredRoots))
	for _, root := range configuredRoots {
		rootsByConfigID[root.ConfigID()] = root
	}
	urls := NewURLs(basePath)
	if credentials == nil {
		panic("authentication is required")
	}
	router := webRouter{
		conf:                      conf,
		maxUploadRequestBytes:     maxUploadRequestBytes,
		auth:                      credentials,
		uploads:                   uploads,
		urls:                      urls,
		renderer:                  renderer,
		db:                        db,
		processor:                 jobProcessor,
		notifications:             notifications,
		perPage:                   perPage,
		radioMinTracks:            radioMinTracks,
		defaultAlbumSort:          defaultAlbumSort,
		configuredRoots:           configuredRoots,
		rootsByConfigID:           rootsByConfigID,
		configFilePath:            configFilePath,
		recognizedAudioExtensions: slices.Sorted(maps.Keys(recognizedAudioExtensions)),
	}
	mux := http.NewServeMux()
	mux.HandleFunc(exactPattern(http.MethodPost, urls.Scrobble), router.scrobble)
	mux.HandleFunc(
		exactPattern(http.MethodGet, urls.Login),
		router.login,
	)
	mux.HandleFunc(
		exactPattern(http.MethodPost, urls.Login),
		router.login,
	)
	mux.HandleFunc(
		exactPattern(http.MethodPost, urls.Logout),
		router.logout,
	)
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
			http.HandlerFunc(serveAsset),
		),
	)
	mux.HandleFunc(
		http.MethodGet+" "+assetsRoot,
		assetNotFound,
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
	mux.HandleFunc(exactPattern(http.MethodGet, urls.AlbumSidecars), router.showAlbumSidecars)
	mux.HandleFunc(exactPattern(http.MethodGet, urls.SidecarFile), router.serveSidecarFile)
	mux.HandleFunc(
		exactPattern(http.MethodGet, urls.TrackAudio),
		router.streamTrackAudio,
	)
	mux.HandleFunc(
		exactPattern(http.MethodGet, urls.Search),
		router.showSearch,
	)
	mux.HandleFunc(exactPattern(http.MethodGet, urls.UnrecognizedTracks), router.showUnrecognizedTracks)
	mux.HandleFunc(exactPattern(http.MethodPatch, urls.UpdateUnrecognizedTrack), router.updateUnrecognizedTrack)
	mux.HandleFunc(
		exactPattern(http.MethodGet, urls.ArtistRules),
		router.showArtistRules,
	)
	mux.HandleFunc(
		exactPattern(http.MethodPatch, urls.UpdateArtistRule),
		router.updateArtistRule,
	)
	mux.HandleFunc(
		exactPattern(http.MethodDelete, urls.UpdateArtistRule),
		router.deleteArtistRule,
	)
	mux.HandleFunc(
		exactPattern(http.MethodGet, urls.MetadataOverrides),
		router.showMetadataOverrides,
	)
	mux.HandleFunc(
		exactPattern(http.MethodPut, urls.AlbumMetadataOverride),
		router.setAlbumMetadataOverride,
	)
	mux.HandleFunc(
		exactPattern(http.MethodDelete, urls.AlbumMetadataOverride),
		router.deleteAlbumMetadataOverride,
	)
	mux.HandleFunc(
		exactPattern(http.MethodPost, urls.SubmitJob),
		router.submitJob,
	)
	mux.HandleFunc(
		exactPattern(http.MethodGet, urls.JobEvents),
		router.jobEvents,
	)
	mux.HandleFunc(
		exactPattern(http.MethodGet, urls.AlbumTracks),
		router.getAlbumTracks,
	)
	mux.HandleFunc(
		exactPattern(http.MethodPost, urls.RadioTrack),
		router.selectRadioTrack,
	)
	mux.HandleFunc(exactPattern(http.MethodPost, urls.CleanupJob), router.cleanupJob)
	mux.HandleFunc(
		exactPattern(http.MethodPost, urls.CancelJob),
		router.cancelJob,
	)
	mux.HandleFunc(
		exactPattern(http.MethodGet, urls.Roots),
		router.showRoots,
	)
	mux.HandleFunc(
		exactPattern(http.MethodPost, urls.DropRoot),
		router.dropRoot,
	)
	mux.HandleFunc(
		exactPattern(http.MethodGet, urls.Jobs),
		router.showJobs,
	)
	mux.HandleFunc(
		exactPattern(http.MethodGet, urls.JobLogs),
		router.showJobLogs,
	)
	mux.HandleFunc(
		exactPattern(http.MethodGet, urls.Help),
		router.showHelp,
	)
	mux.HandleFunc(
		exactPattern(http.MethodGet, urls.Add),
		router.showAdd,
	)
	mux.HandleFunc(
		exactPattern(http.MethodPost, urls.Add),
		router.uploadFiles,
	)
	mux.HandleFunc(exactPattern(http.MethodGet, urls.ListeningData), router.showListeningData)
	mux.HandleFunc(exactPattern(http.MethodGet, urls.ListeningSummary), router.showListeningSummary)
	mux.HandleFunc(exactPattern(http.MethodGet, urls.ListeningChart), router.showListeningChart)
	mux.HandleFunc(exactPattern(http.MethodGet, urls.ListeningHours), router.showListeningHours)
	mux.HandleFunc(exactPattern(http.MethodGet, urls.ListeningGenres), router.showListeningGenres)

	mux.HandleFunc(
		http.MethodGet+" "+urls.InstanceIdenticon,
		router.getIdenticon,
	)

	return http.NewCrossOriginProtection().Handler(router.requireAuth(mux))
}

func internalServerError(w http.ResponseWriter, message string, args ...any) {
	slog.Error(message, args...)
	http.Error(
		w,
		"An unexpected error occurred. See the server logs for details.",
		http.StatusInternalServerError,
	)
}

func apiError(w http.ResponseWriter, status int, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": message})
}
