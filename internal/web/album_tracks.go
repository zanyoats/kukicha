package web

import (
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"

	"github.com/zanyoats/kukicha/internal/usecase/query"
)

func (c *webRouter) getAlbumTracks(w http.ResponseWriter, r *http.Request) {
	albumID, err := parseAlbumID(r)
	if err != nil {
		apiError(w, http.StatusBadRequest, err.Error())
		return
	}

	album, err := query.AlbumByFilters(r.Context(), c.db, query.AlbumFilters{AlbumID: albumID})
	if errors.Is(err, query.ErrAlbumNotFound) {
		apiError(w, http.StatusNotFound, "Album not found.")
		return
	} else if err != nil {
		slog.Error("unable to load album tracks", "album_id", albumID, "error", err)
		apiError(w, http.StatusInternalServerError, "Unable to load album tracks.")
		return
	}

	artwork := newArtworkViewModel(c.urls, album.ID, query.AlbumArtwork64, album.SmallArtwork)
	data, err := json.Marshal(newAlbumPageData(album, c.urls, artwork))
	if err != nil {
		slog.Error("unable to encode album tracks", "album_id", albumID, "error", err)
		apiError(w, http.StatusInternalServerError, "Unable to encode album tracks.")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_, _ = w.Write(data)
}
