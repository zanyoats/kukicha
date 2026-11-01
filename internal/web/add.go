package web

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"mime"
	"net/http"

	"github.com/zanyoats/kukicha/internal/app"
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
		MaxRequestBytes  int64
		UploadLimit      string
		Modes            []addModeView
	}{
		URLs: c.urls, EyeBrow: "Library", Heading: "Add to Kukicha",
		Modes: []addModeView{
			{Value: upload.ModeFiles, Label: "Upload files"},
			{Value: upload.ModeSync, Label: "Server folder or bucket"},
		},
		ConfiguredRoots: configuredRootViews(c.configuredRoots),
		MaxRequestBytes: c.maxUploadRequestBytes,
		UploadLimit:     uploadLimitLabel(c.maxUploadRequestBytes),
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
		if r.ContentLength > c.maxUploadRequestBytes {
			apiError(w, http.StatusRequestEntityTooLarge, uploadSizeError(c.maxUploadRequestBytes))
			return
		}
		r.Body = http.MaxBytesReader(w, r.Body, c.maxUploadRequestBytes)
		parts, parseErr := r.MultipartReader()
		if parseErr != nil {
			apiError(w, http.StatusBadRequest, "Expected multipart file upload.")
			return
		}
		jobID, err = c.uploads.AcceptFiles(r.Context(), r.URL.Query().Get("root_id"), parts)
	case "application/json":
		r.Body = http.MaxBytesReader(w, r.Body, app.JSONRequestMaxBytes)
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
		var sizeError *http.MaxBytesError
		if errors.As(err, &sizeError) {
			apiError(w, http.StatusRequestEntityTooLarge, uploadSizeError(sizeError.Limit))
			return
		}
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

// Include exact bytes so rounded display units never hide the actual limit.
func uploadLimitLabel(limit int64) string {
	value := float64(limit)
	units := []string{"B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB"}
	unit := 0
	for value >= 1024 && unit < len(units)-1 {
		value /= 1024
		unit++
	}
	if unit == 0 {
		return fmt.Sprintf("%d bytes", limit)
	}
	return fmt.Sprintf("%.2f %s (%d bytes)", value, units[unit], limit)
}

func uploadSizeError(limit int64) string {
	return "Upload exceeds the " + uploadLimitLabel(limit) + " request limit, including multipart headers. Remove files or upload them in smaller batches."
}
