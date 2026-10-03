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
	notifications, unsubscribe := c.notifications.Subscribe()
	defer unsubscribe()

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

	writeEvent := func(event string, value any) error {
		// Bound writes so a stalled client cannot retain the handler indefinitely.
		if err := controller.SetWriteDeadline(
			time.Now().Add(10 * time.Second),
		); err != nil && !errors.Is(err, http.ErrNotSupported) {
			return err
		}
		data, err := json.Marshal(value)
		if err != nil {
			return err
		}
		if _, err := fmt.Fprintf(w, "event: %s\ndata: %s\n\n", event, data); err != nil {
			return err
		}
		return controller.Flush()
	}
	writeStatus := func() error {
		return writeEvent("job-status", struct {
			Jobs []query.PendingJob `json:"jobs"`
		}{Jobs: jobs})
	}
	if err := writeStatus(); err != nil {
		return
	}

	for {
		select {
		case <-r.Context().Done():
			return
		case notification := <-notifications:
			if err := writeEvent("job-notification", notification); err != nil {
				return
			}
		case <-ticker.C:
			jobs, err = query.PendingJobs(r.Context(), c.db)
			if err != nil {
				if r.Context().Err() == nil {
					slog.ErrorContext(r.Context(), "unable to read pending jobs", "error", err)
				}
				return
			}
			if err := writeStatus(); err != nil {
				return
			}
		}
	}
}
