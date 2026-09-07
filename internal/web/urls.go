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
	Home                  string
	Library               string
	Album                 string
	AlbumArtwork          string
	TrackAudio            string
	Artist                string
	Genre                 string
	Style                 string
	Search                string
	ArtistSplitRules      string
	Roots                 string
	MetadataOverrides     string
	ListeningData         string
	Cache                 string
	Jobs                  string
	Help                  string
	InstanceIdenticon     string
	UpdateArtistSplitRule string
	Assets                string
	Favicon               string
	UnpolyStylesheet      string
	Stylesheet            string
	AppJavaScript         string
}

func NewURLs(basePath string) URLs {
	base := strings.TrimRight(basePath, "/")
	app := base + "/app"
	api := base + "/api"
	assets := "/assets/"
	return URLs{
		Home:                  app + "/",
		Library:               app + "/library/",
		Album:                 app + "/library/album/",
		AlbumArtwork:          app + "/library/album/artwork/",
		TrackAudio:            app + "/library/album/tracks/audio/",
		Artist:                app + "/library/artist/",
		Genre:                 app + "/library/genre/",
		Style:                 app + "/library/style/",
		Search:                app + "/search/",
		ArtistSplitRules:      app + "/artistSplitRules/",
		Roots:                 app + "/roots/",
		MetadataOverrides:     app + "/metadataOverrides/",
		ListeningData:         app + "/listeningData/",
		Cache:                 app + "/cache/",
		Jobs:                  app + "/jobs/",
		Help:                  app + "/help/",
		InstanceIdenticon:     app + "/instance/identicon.svg",
		UpdateArtistSplitRule: api + "/artistSplitRules/",
		Assets:                assets,
		Favicon:               "/favicon.ico",
		UnpolyStylesheet:      assets + "unpoly.min.css",
		Stylesheet:            assets + "app.css",
		AppJavaScript:         assets + "app.js",
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

func (u URLs) AlbumURL(id int64) string {
	q := url.Values{}
	q.Set("album_id", strconv.FormatInt(id, 10))
	return u.Album + "?" + q.Encode()
}

func (u URLs) ArtistURL(id int64) string {
	q := url.Values{}
	q.Set("artist_id", strconv.FormatInt(id, 10))
	return u.Artist + "?" + q.Encode()
}

func (u URLs) GenreURL(id int64) string {
	q := url.Values{}
	q.Set("genre_id", strconv.FormatInt(id, 10))
	return u.Genre + "?" + q.Encode()
}

func (u URLs) StyleURL(id int64) string {
	q := url.Values{}
	q.Set("style_id", strconv.FormatInt(id, 10))
	return u.Style + "?" + q.Encode()
}

func (u URLs) TrackAudioURL(id int64) string {
	q := url.Values{}
	q.Set("track_id", strconv.FormatInt(id, 10))
	return u.TrackAudio + "?" + q.Encode()
}
