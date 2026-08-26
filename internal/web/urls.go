package web

import (
	"strconv"

	"github.com/zanyoats/kukicha/internal/usecase/query"
)

type sharedURLs struct {
	HomeURL              string
	LibraryURL           string
	SearchURL            string
	UnpolyStylesheetURL  string
	StylesheetURL        string
	AppJavaScriptURL     string
	InstanceIdenticonURL string
}

func getSharedURLs(appBasePath string) sharedURLs {
	return sharedURLs{
		HomeURL:              appBasePath,
		LibraryURL:           appBasePath + "/library",
		SearchURL:            appBasePath + "/search",
		UnpolyStylesheetURL:  appBasePath + "/assets/unpoly.min.css",
		StylesheetURL:        appBasePath + "/assets/app.css",
		AppJavaScriptURL:     appBasePath + "/assets/app.js",
		InstanceIdenticonURL: appBasePath + "/instance/identicon.svg",
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

func trackAudioURL(
	appBasePath string,
	trackID int64,
) string {
	return appBasePath +
		"/library/album/tracks/audio" +
		"?track_id=" + strconv.FormatInt(trackID, 10)
}
