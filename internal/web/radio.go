package web

import (
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"strconv"
	"strings"

	"github.com/zanyoats/kukicha/internal/usecase/command"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

func (c *webRouter) selectRadioTrack(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	var input command.RadioRequest
	if !decodeJSONRequest(w, r, &input) {
		return
	}
	selection, err := command.SelectRadioTrack(r.Context(), c.db, input, c.radioMinTracks)
	if err != nil {
		switch {
		case errors.Is(err, command.ErrInvalidRadioRequest):
			apiError(w, http.StatusBadRequest, "Invalid radio request.")
		case errors.Is(err, command.ErrRadioMembershipChanged):
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusConflict)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "Radio membership changed.", "code": "radio_membership_changed"})
		case errors.Is(err, command.ErrRadioBelowMinimum):
			apiError(w, http.StatusNotFound, err.Error())
		case errors.Is(err, command.ErrRadioEmpty):
			apiError(w, http.StatusNotFound, "This radio has no tracks.")
		case errors.Is(err, command.ErrRadioNotFound):
			apiError(w, http.StatusNotFound, "This radio no longer exists.")
		default:
			slog.ErrorContext(r.Context(), "unable to select radio track", "error", err)
			apiError(w, http.StatusInternalServerError, "Unable to load this radio. Please try again.")
		}
		return
	}
	track := selection.Track
	response := struct {
		Name  string               `json:"name"`
		State command.RadioState   `json:"state"`
		Track playerAlbumPageTrack `json:"track"`
	}{
		Name:  selection.Name,
		State: selection.State,
		Track: playerAlbumPageTrack{
			ID:              strconv.FormatInt(track.ID, 10),
			AudioURL:        c.urls.TrackAudioURL(track.ID),
			Title:           track.Title,
			Artists:         strings.Join(track.Artists, " "),
			Album:           selection.AlbumTitle,
			AlbumURL:        c.urls.AlbumURL(selection.AlbumID),
			ArtworkURL:      c.urls.AlbumArtworkURL(selection.AlbumID, query.AlbumArtwork64),
			DurationSeconds: track.DurationSeconds,
		},
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(response)
}
