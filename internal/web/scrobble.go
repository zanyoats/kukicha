package web

import (
	"errors"
	"log/slog"
	"net/http"

	"github.com/zanyoats/kukicha/internal/usecase/command"
)

func (c *webRouter) scrobble(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	var input command.ScrobbleRequest
	if !decodeJSONRequest(w, r, &input) {
		return
	}
	if err := command.RecordScrobble(r.Context(), c.db, input); err != nil {
		switch {
		case errors.Is(err, command.ErrInvalidScrobble):
			apiError(w, http.StatusBadRequest, "id, time and submission are required and must be valid.")
		case errors.Is(err, command.ErrScrobbleTrackNotFound):
			apiError(w, http.StatusNotFound, "Track not found.")
		default:
			slog.ErrorContext(r.Context(), "unable to record playback", "error", err)
			apiError(w, http.StatusInternalServerError, "Unable to record playback.")
		}
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
