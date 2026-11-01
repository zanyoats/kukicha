package web

import (
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"mime"
	"net/http"

	"github.com/zanyoats/kukicha/internal/upload"
)

type addModeView struct {
	Value, Label string
}

func (c *webRouter) showAdd(w http.ResponseWriter, r *http.Request) {
	renderPage(w, c.renderer, "add.gohtml", struct {
		URLs             URLs
		EyeBrow, Heading string
		ConfiguredRoots  []configuredRootView
		Modes            []addModeView
	}{
		URLs: c.urls, EyeBrow: "Library", Heading: "Add to Kukicha",
		Modes: []addModeView{
			{Value: upload.ModeFiles, Label: "Upload files"},
			{Value: upload.ModeSync, Label: "Server folder or bucket"},
		},
		ConfiguredRoots: configuredRootViews(c.configuredRoots),
	})
}

func (c *webRouter) uploadFiles(w http.ResponseWriter, r *http.Request) {
	mediaType, _, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if err != nil {
		apiError(w, http.StatusBadRequest, "Invalid Content-Type.")
		return
	}
	var jobID int64
	switch mediaType {
	case "multipart/form-data":
		r.Body = http.MaxBytesReader(w, r.Body, upload.MaxRequestBytes)
		parts, parseErr := r.MultipartReader()
		if parseErr != nil {
			apiError(w, http.StatusBadRequest, "Expected multipart file upload.")
			return
		}
		jobID, err = c.uploads.AcceptFiles(r.Context(), r.URL.Query().Get("root_id"), parts)
	case "application/json":
		r.Body = http.MaxBytesReader(w, r.Body, 1<<20)
		decoder := json.NewDecoder(r.Body)
		decoder.DisallowUnknownFields()
		var request upload.SyncRequest
		if err = decoder.Decode(&request); err != nil {
			apiError(w, http.StatusBadRequest, "Invalid sync request: "+err.Error())
			return
		}
		if err = decoder.Decode(new(any)); !errors.Is(err, io.EOF) {
			apiError(w, http.StatusBadRequest, "Expected one JSON request.")
			return
		}
		jobID, err = c.uploads.AcceptSync(r.Context(), r.URL.Query().Get("root_id"), request)
	default:
		apiError(w, http.StatusUnsupportedMediaType, "Expected multipart/form-data or application/json.")
		return
	}
	if err != nil {
		slog.WarnContext(r.Context(), "add request not accepted", "error", err)
		status := http.StatusBadRequest
		if errors.Is(err, upload.ErrEnqueue) {
			status = http.StatusInternalServerError
		}
		apiError(w, status, err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	_ = json.NewEncoder(w).Encode(struct {
		JobID int64 `json:"job_id"`
	}{jobID})
}
