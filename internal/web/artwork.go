package web

import (
	"errors"
	"log/slog"
	"net/http"

	"github.com/zanyoats/kukicha/internal/usecase/query"
)

func (c *appRoutes) getAlbumArtwork(w http.ResponseWriter, r *http.Request) {
	albumID, err := parseAlbumID(r)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	size, err := parseArtworkSize(r)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	artwork, err := query.AlbumArtworkByID(
		r.Context(),
		c.db,
		albumID,
		size,
	)

	switch {
	case errors.Is(err, query.ErrAlbumNotFound):
		http.Error(
			w,
			"Album not found",
			http.StatusNotFound,
		)
		return

	case errors.Is(err, query.ErrAlbumArtworkNotFound):
		switch size {
		case query.AlbumArtwork256:
			renderImage(w, "image/svg+xml", AlbumArtwork256FallbackArtwork)
		case query.AlbumArtwork64:
			renderImage(w, "image/svg+xml", AlbumArtwork64FallbackArtwork)
		}
		return

	case err != nil:
		internalServerError(
			w,
			"unable to load album artwork",
			"album_id", albumID,
			"error", err,
		)
		return
	}

	contentType := artwork.ContentType
	if contentType == "" {
		contentType = http.DetectContentType(artwork.Bytes)
	}

	invalidArtwork := len(artwork.Bytes) == 0 ||
		len(contentType) < len("image/") ||
		contentType[:len("image/")] != "image/"

	if invalidArtwork {
		slog.WarnContext(
			r.Context(),
			"album has invalid resolved artwork",
			"album_id", albumID,
		)
		switch size {
		case query.AlbumArtwork256:
			renderImage(w, "image/svg+xml", AlbumArtwork256FallbackArtwork)
		case query.AlbumArtwork64:
			renderImage(w, "image/svg+xml", AlbumArtwork64FallbackArtwork)
		}
		return
	}

	renderImage(w, contentType, artwork.Bytes)
}
