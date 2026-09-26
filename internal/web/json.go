package web

import (
	"encoding/json"
	"io"
	"mime"
	"net/http"
)

func decodeJSONRequest(w http.ResponseWriter, r *http.Request, input any) bool {
	contentType, _, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if err != nil || contentType != "application/json" {
		apiError(w, http.StatusUnsupportedMediaType, "Content-Type must be application/json.")
		return false
	}
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20)) // 1 MiB
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(input); err != nil {
		apiError(w, http.StatusBadRequest, "Invalid JSON request.")
		return false
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		apiError(w, http.StatusBadRequest, "Expected a single JSON object.")
		return false
	}
	return true
}
