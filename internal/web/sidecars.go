package web

import (
	"database/sql"
	"errors"
	"net/http"
	"path"
	"strings"

	"github.com/zanyoats/kukicha/internal/usecase/query"
)

type sidecarViewModel struct {
	Name  string
	URL   string
	Image bool
	Video bool
}

type sidecarsViewModel struct {
	URLs     URLs
	EyeBrow  string
	Heading  string
	AlbumID  int64
	Sidecars []sidecarViewModel
}

func (c *webRouter) showAlbumSidecars(w http.ResponseWriter, r *http.Request) {
	albumID, err := parseAlbumID(r)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	album, err := query.AlbumByFilters(r.Context(), c.db, query.AlbumFilters{AlbumID: albumID})
	if errors.Is(err, query.ErrAlbumNotFound) {
		http.NotFound(w, r)
		return
	}
	if err != nil {
		internalServerError(w, "unable to load album", "error", err)
		return
	}
	sidecars, err := query.AlbumSidecars(r.Context(), c.db, albumID)
	if err != nil {
		internalServerError(w, "unable to load sidecars", "error", err)
		return
	}
	model := sidecarsViewModel{URLs: c.urls, EyeBrow: album.Title, Heading: "Sidecars", AlbumID: albumID}
	for _, file := range sidecars {
		item := sidecarViewModel{Name: path.Base(file.RelativePath), URL: c.urls.SidecarFileURL(file.ID)}
		switch strings.ToLower(file.Extension) {
		case ".jpg", ".jpeg", ".png", ".webp", ".gif":
			item.Image = true
		case ".m4v", ".mp4", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".mpg", ".mpeg":
			item.Video = true
		}
		model.Sidecars = append(model.Sidecars, item)
	}
	renderPage(w, c.renderer, "sidecars.gohtml", model)
}

func (c *webRouter) serveSidecarFile(w http.ResponseWriter, r *http.Request) {
	id, err := parsePositiveID(r, "sidecar_id")
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	file, err := query.SidecarFileByID(r.Context(), c.db, id)
	if errors.Is(err, sql.ErrNoRows) {
		http.NotFound(w, r)
		return
	}
	if err != nil {
		internalServerError(w, "unable to load sidecar", "error", err)
		return
	}
	c.serveLibraryFile(w, r, file)
}
