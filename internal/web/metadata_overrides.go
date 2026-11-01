package web

import (
	"encoding/json"
	"errors"
	"net/http"
	"strings"

	"github.com/zanyoats/kukicha/internal/metadata"
	"github.com/zanyoats/kukicha/internal/usecase/command"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

func (c *webRouter) setAlbumMetadataOverride(w http.ResponseWriter, r *http.Request) {
	var input struct {
		AlbumKey string `json:"album_key"`
		URL      string `json:"url"`
	}
	if !decodeJSONRequest(w, r, &input) {
		return
	}
	if strings.TrimSpace(input.AlbumKey) == "" {
		apiError(w, http.StatusBadRequest, "An album key is required.")
		return
	}
	if _, err := metadata.ParseSource(input.URL); err != nil {
		apiError(w, http.StatusBadRequest, err.Error())
		return
	}
	if err := command.SetAlbumMetadataOverride(r.Context(), c.db, input.AlbumKey, input.URL); err != nil {
		if errors.Is(err, command.ErrOverrideAlbumNotFound) {
			apiError(w, http.StatusNotFound, err.Error())
			return
		}
		internalServerError(w, "unable to save album metadata override", "error", err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (c *webRouter) deleteAlbumMetadataOverride(w http.ResponseWriter, r *http.Request) {
	var input struct {
		AlbumKey string `json:"album_key"`
	}
	if !decodeJSONRequest(w, r, &input) {
		return
	}
	if strings.TrimSpace(input.AlbumKey) == "" {
		apiError(w, http.StatusBadRequest, "An album key is required.")
		return
	}
	if err := command.DeleteAlbumMetadataOverride(r.Context(), c.db, input.AlbumKey); err != nil {
		internalServerError(w, "unable to remove album metadata override", "error", err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

type metadataOverridesViewModel struct {
	EyeBrow  string
	Heading  string
	URLs     URLs
	Artists  []query.HomeItem
	ArtistID int64
	Albums   []metadataOverrideAlbum
}

type metadataOverrideAlbum struct {
	query.MetadataOverrideAlbum
	ArtworkURL string
	EditorData string
}

func (c *webRouter) showMetadataOverrides(w http.ResponseWriter, r *http.Request) {
	var artistID int64
	var err error
	if r.URL.Query().Has("artist_id") {
		artistID, err = parsePositiveID(r, "artist_id")
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
	}
	artists, err := query.LibraryArtists(r.Context(), c.db)
	if err != nil {
		internalServerError(w, "unable to load artists", "error", err)
		return
	}
	model := metadataOverridesViewModel{
		EyeBrow: "Settings", Heading: "Metadata Overrides", URLs: c.urls,
		Artists: artists, ArtistID: artistID,
	}
	if artistID != 0 {
		found := false
		for _, artist := range artists {
			if artist.ID == artistID {
				found = true
				break
			}
		}
		if !found {
			http.Error(w, "Artist not found", http.StatusNotFound)
			return
		}
		albums, err := query.MetadataOverrideAlbums(r.Context(), c.db, artistID)
		if err != nil {
			internalServerError(w, "unable to load metadata override albums", "error", err)
			return
		}
		for _, album := range albums {
			data, err := json.Marshal(struct {
				AlbumKey  string `json:"album_key"`
				UpdateURL string `json:"update_url"`
			}{album.AlbumKey, c.urls.AlbumMetadataOverride})
			if err != nil {
				internalServerError(w, "unable to encode metadata override editor", "error", err)
				return
			}
			model.Albums = append(model.Albums, metadataOverrideAlbum{
				MetadataOverrideAlbum: album,
				ArtworkURL:            c.urls.AlbumArtworkURL(album.ID, query.AlbumArtwork64),
				EditorData:            string(data),
			})
		}

	}
	renderPage(w, c.renderer, "metadata_overrides.gohtml", model)
}
