package web

import (
	"database/sql"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"

	"github.com/zanyoats/kukicha/internal/processor"
	"github.com/zanyoats/kukicha/internal/usecase/command"
)

func (c *appRoutes) submitJob(w http.ResponseWriter, r *http.Request) {
	var input struct {
		Type    string          `json:"type"`
		Payload json.RawMessage `json:"payload"`
	}
	if !decodeJSONRequest(w, r, &input) {
		return
	}
	submission := command.JobSubmission{Type: input.Type, Payload: input.Payload}
	if err := command.ValidateJobSubmission(submission); err != nil {
		apiError(w, http.StatusBadRequest, err.Error())
		return
	}
	if _, err := c.processor.Submit(r.Context(), processor.JobSubmission(submission)); err != nil {
		slog.ErrorContext(r.Context(), "unable to submit job", "error", err)
		apiError(w, http.StatusInternalServerError, "Unable to submit the job.")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (c *appRoutes) cancelJob(w http.ResponseWriter, r *http.Request) {
	var input struct {
		JobID int64 `json:"job_id"`
	}
	if !decodeJSONRequest(w, r, &input) {
		return
	}
	if input.JobID <= 0 {
		apiError(w, http.StatusBadRequest, "A positive job ID is required.")
		return
	}
	if _, err := c.processor.Cancel(r.Context(), input.JobID); err != nil {
		switch {
		case errors.Is(err, sql.ErrNoRows):
			apiError(w, http.StatusNotFound, "Job not found.")
		case errors.Is(err, command.ErrJobNotCancelable):
			apiError(w, http.StatusConflict, err.Error())
		default:
			slog.ErrorContext(r.Context(), "unable to cancel job", "error", err)
			apiError(w, http.StatusInternalServerError, "Unable to cancel the job.")
		}
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
