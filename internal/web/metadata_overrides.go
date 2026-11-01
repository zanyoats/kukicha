package web

import (
	"errors"
	"net/http"
	"strings"

	"github.com/zanyoats/kukicha/internal/metadata"
	"github.com/zanyoats/kukicha/internal/usecase/command"
)

func (c *webRouter) setAlbumMetadataOverride(w http.ResponseWriter, r *http.Request) {
	var input struct {
		AlbumKey string `json:"album_key"`
		URL      string `json:"url"`
	}
	if !decodeJSONRequest(w, r, &input) {
		return
	}
	if strings.TrimSpace(input.AlbumKey) == "" {
		apiError(w, http.StatusBadRequest, "An album key is required.")
		return
	}
	if _, err := metadata.ParseSource(input.URL); err != nil {
		apiError(w, http.StatusBadRequest, err.Error())
		return
	}
	if err := command.SetAlbumMetadataOverride(r.Context(), c.db, input.AlbumKey, input.URL); err != nil {
		if errors.Is(err, command.ErrOverrideAlbumNotFound) {
			apiError(w, http.StatusNotFound, err.Error())
			return
		}
		internalServerError(w, "unable to save album metadata override", "error", err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (c *webRouter) deleteAlbumMetadataOverride(w http.ResponseWriter, r *http.Request) {
	var input struct {
		AlbumKey string `json:"album_key"`
	}
	if !decodeJSONRequest(w, r, &input) {
		return
	}
	if strings.TrimSpace(input.AlbumKey) == "" {
		apiError(w, http.StatusBadRequest, "An album key is required.")
		return
	}
	if err := command.DeleteAlbumMetadataOverride(r.Context(), c.db, input.AlbumKey); err != nil {
		internalServerError(w, "unable to remove album metadata override", "error", err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
