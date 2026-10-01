package web

import (
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"time"

	"github.com/zanyoats/kukicha/internal/usecase/query"
)

func (c *appRoutes) jobEvents(w http.ResponseWriter, r *http.Request) {
	jobs, err := query.PendingJobs(r.Context(), c.db)
	if err != nil {
		internalServerError(w, "unable to read pending jobs", "error", err)
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("X-Accel-Buffering", "no") // for Nginx compatibility, don't buffer SSE
	controller := http.NewResponseController(w)
	// 2 seconds seems like a good balance between load and responsiveness. There really
	// is no reason to surface this as a config key and keeping it hardcoded seems reasonable.
	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()

	for {
		// Bound writes so a stalled client cannot retain the handler indefinitely.
		if err := controller.SetWriteDeadline(
			time.Now().Add(10 * time.Second),
		); err != nil && !errors.Is(err, http.ErrNotSupported) {
			return
		}
		data, err := json.Marshal(struct {
			Jobs []query.PendingJob `json:"jobs"`
		}{Jobs: jobs})
		if err != nil {
			return
		}
		if _, err := fmt.Fprintf(w, "event: job-status\ndata: %s\n\n", data); err != nil {
			return
		}
		if err := controller.Flush(); err != nil {
			return
		}

		select {
		case <-r.Context().Done():
			return
		case <-ticker.C:
		}
		jobs, err = query.PendingJobs(r.Context(), c.db)
		if err != nil {
			if r.Context().Err() == nil {
				slog.ErrorContext(r.Context(), "unable to read pending jobs", "error", err)
			}
			return
		}
	}
}
