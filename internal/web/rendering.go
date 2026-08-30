package web

import (
	"bytes"
	"log/slog"
	"net/http"
	"strconv"
)

func renderPage(
	w http.ResponseWriter,
	renderer Renderer,
	pageName string,
	viewModel any,
) {
	var buf bytes.Buffer
	if err := renderer.Render(&buf, pageName, viewModel); err != nil {
		internalServerError(
			w,
			"unable to render page",
			"page", pageName,
			"error", err,
		)
		return
	}

	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(http.StatusOK)
	if _, err := buf.WriteTo(w); err != nil {
		slog.Debug("unable to write page response", "error", err)
	}
}

func renderImage(
	w http.ResponseWriter,
	contentType string,
	data []byte,
) {
	w.Header().Set("Content-Type", contentType)
	w.Header().Set(
		"Content-Length",
		strconv.Itoa(len(data)),
	)
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.WriteHeader(http.StatusOK)

	if _, err := w.Write(data); err != nil {
		slog.Debug("unable to write image response", "error", err)
	}
}
