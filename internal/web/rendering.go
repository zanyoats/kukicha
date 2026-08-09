package web

import (
	"bytes"
	"fmt"
	"log"
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
		internalServerError(w, fmt.Sprintf(
			"error rendering page %q: %v",
			pageName, err,
		))
		return
	}

	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(http.StatusOK)
	if _, err := buf.WriteTo(w); err != nil {
		log.Printf("error rendering page response: %v", err)
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
		log.Printf("error rendering image response: %v", err)
	}
}
