package web

import (
	"net/url"
	"strconv"
	"strings"

	"github.com/zanyoats/kukicha/internal/usecase/query"
)

// URLs contains paths shared by router registration and page links.
// Directory paths include their canonical trailing slash.
type URLs struct {
	Login                   string
	Logout                  string
	Home                    string
	Library                 string
	AlbumArtwork            string
	TrackAudio              string
	Search                  string
	ArtistSplitRules        string
	Add                     string
	Roots                   string
	DropRoot                string
	MetadataOverrides       string
	ListeningData           string
	Jobs                    string
	JobLogs                 string
	Help                    string
	InstanceIdenticon       string
	UpdateArtistSplitRule   string
	SubmitJob               string
	CancelJob               string
	JobEvents               string
	AlbumTracks             string
	RadioTrack              string
	Assets                  string
	Favicon                 string
	UnpolyStylesheet        string
	Stylesheet              string
	AppJavaScript           string
	AlbumArtwork64Fallback  string
	AlbumArtwork256Fallback string
}

func NewURLs(basePath string) URLs {
	base := strings.TrimRight(basePath, "/")
	app := base + "/app"
	api := base + "/api"
	assets := "/assets/"
	return URLs{
		Login:                   base + "/login/",
		Logout:                  base + "/logout/",
		Home:                    app + "/",
		Library:                 app + "/library/",
		AlbumArtwork:            app + "/library/album/artwork/",
		TrackAudio:              app + "/library/album/tracks/audio/",
		Search:                  app + "/search/",
		ArtistSplitRules:        app + "/artistSplitRules/",
		Add:                     app + "/add/",
		Roots:                   app + "/roots/",
		DropRoot:                api + "/roots/drop/",
		MetadataOverrides:       app + "/metadataOverrides/",
		ListeningData:           app + "/listeningData/",
		Jobs:                    app + "/jobs/",
		JobLogs:                 app + "/jobs/logs/",
		Help:                    app + "/help/",
		InstanceIdenticon:       app + "/instance/identicon.svg",
		UpdateArtistSplitRule:   api + "/artistSplitRules/",
		SubmitJob:               api + "/jobs/",
		CancelJob:               api + "/jobs/cancel/",
		JobEvents:               api + "/jobs/events/",
		AlbumTracks:             api + "/albums/tracks/",
		RadioTrack:              api + "/radios/track/",
		Assets:                  assets,
		Favicon:                 "/favicon.ico",
		UnpolyStylesheet:        assets + "unpoly.min.css",
		Stylesheet:              assets + "app.css",
		AppJavaScript:           assets + "app.js",
		AlbumArtwork64Fallback:  assets + "album-fallback-64.svg",
		AlbumArtwork256Fallback: assets + "album-fallback-256.svg",
	}
}

// exactPattern matches a directory path without matching its descendants.
// Use ordinary method + path patterns for files and subtree handlers.
func exactPattern(method, path string) string {
	return method + " " + path + "{$}"
}

func (u URLs) AlbumArtworkURL(albumID int64, size query.AlbumArtworkSize) string {
	q := url.Values{}
	q.Set("album_id", strconv.FormatInt(albumID, 10))
	q.Set("size", string(size))
	return u.AlbumArtwork + "?" + q.Encode()
}

func (u URLs) AlbumTracksURL(id int64) string {
	q := url.Values{}
	q.Set("album_id", strconv.FormatInt(id, 10))
	return u.AlbumTracks + "?" + q.Encode()
}

func (u URLs) AlbumURL(id int64) string {
	q := url.Values{}
	q.Set("album_id", strconv.FormatInt(id, 10))
	return u.Library + "?" + q.Encode()
}

func (u URLs) ArtistURL(id int64) string {
	q := url.Values{}
	q.Set("artist_id", strconv.FormatInt(id, 10))
	return u.Library + "?" + q.Encode()
}

func (u URLs) GenreURL(id int64) string {
	q := url.Values{}
	q.Set("genre_id", strconv.FormatInt(id, 10))
	return u.Library + "?" + q.Encode()
}

func (u URLs) StyleURL(id int64) string {
	q := url.Values{}
	q.Set("style_id", strconv.FormatInt(id, 10))
	return u.Library + "?" + q.Encode()
}

func (u URLs) TrackAudioURL(id int64) string {
	q := url.Values{}
	q.Set("track_id", strconv.FormatInt(id, 10))
	return u.TrackAudio + "?" + q.Encode()
}

func (u URLs) JobLogsURL(jobID, before int64) string {
	q := url.Values{"job_id": {strconv.FormatInt(jobID, 10)}}
	if before > 0 {
		q.Set("before", strconv.FormatInt(before, 10))
	}
	return u.JobLogs + "?" + q.Encode()
}

func (u URLs) JobsURL(before int64) string {
	if before <= 0 {
		return u.Jobs
	}
	return u.Jobs + "?" + url.Values{"before": {strconv.FormatInt(before, 10)}}.Encode()
}
