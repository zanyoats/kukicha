package web

import (
	"strconv"

	"github.com/zanyoats/kukicha/internal/usecase/query"
)

type sharedURLs struct {
	HomeURL              string
	LibraryURL           string
	SearchURL            string
	ConfigLinks          []configLink
	UnpolyStylesheetURL  string
	StylesheetURL        string
	AppJavaScriptURL     string
	InstanceIdenticonURL string
	PlayerArtworkSize    query.AlbumArtworkSize
}

func getSharedURLs(appBasePath string) sharedURLs {
	return sharedURLs{
		HomeURL:              appBasePath,
		LibraryURL:           appBasePath + "/library",
		SearchURL:            appBasePath + "/search",
		ConfigLinks:          configLinks(appBasePath),
		UnpolyStylesheetURL:  appBasePath + "/assets/unpoly.min.css",
		StylesheetURL:        appBasePath + "/assets/app.css",
		AppJavaScriptURL:     appBasePath + "/assets/app.js",
		InstanceIdenticonURL: appBasePath + "/instance/identicon.svg",
		PlayerArtworkSize:    query.AlbumArtwork64,
	}
}

func albumArtworkURL(
	appBasePath string,
	albumID int64,
	size query.AlbumArtworkSize,
) string {
	return appBasePath + "/library/album/artwork" +
		"?album_id=" + strconv.FormatInt(albumID, 10) +
		"&size=" + string(size)
}

func albumURL(appBasePath string, albumID int64) string {
	return appBasePath + "/library/album" +
		"?album_id=" + strconv.FormatInt(albumID, 10)
}

func artistURL(appBasePath string, artistID int64) string {
	return appBasePath + "/library/artist" +
		"?artist_id=" + strconv.FormatInt(artistID, 10)
}

func genreURL(appBasePath string, genreID int64) string {
	return appBasePath + "/library/genre" +
		"?genre_id=" + strconv.FormatInt(genreID, 10)
}

func styleURL(appBasePath string, styleID int64) string {
	return appBasePath + "/library/style" +
		"?style_id=" + strconv.FormatInt(styleID, 10)
}

func trackAudioURL(
	appBasePath string,
	trackID int64,
) string {
	return appBasePath +
		"/library/album/tracks/audio" +
		"?track_id=" + strconv.FormatInt(trackID, 10)
}
