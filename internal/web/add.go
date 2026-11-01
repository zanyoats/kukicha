package web

import (
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"mime"
	"net/http"
	"sort"
	"strings"

	"github.com/zanyoats/kukicha/internal/scan"
	"github.com/zanyoats/kukicha/internal/upload"
)

func (c *webRouter) showAdd(w http.ResponseWriter, r *http.Request) {
	extensions := make([]string, 0, len(scan.RecognizedAudioExtensions))
	for extension := range scan.RecognizedAudioExtensions {
		extensions = append(extensions, extension)
	}
	sort.Strings(extensions)
	extensions = append(extensions, upload.ArchiveExtensions...)
	renderPage(w, c.renderer, "add.gohtml", struct {
		URLs                     URLs
		EyeBrow, Heading, Accept string
		ConfiguredRoots          []configuredRootView
	}{
		URLs: c.urls, EyeBrow: "Library", Heading: "Add to Kukicha",
		Accept:          strings.Join(extensions, ","),
		ConfiguredRoots: configuredRootViews(c.configuredRoots),
	})
}

// uploadFiles is deliberately gated before parsing or writing request content.
func (c *webRouter) uploadFiles(w http.ResponseWriter, r *http.Request) {
	if !upload.Enabled || c.uploads == nil {
		apiError(w, http.StatusServiceUnavailable, "Add is not enabled until both modes, root marker checks, and standalone sync removal are ready.")
		return
	}
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
