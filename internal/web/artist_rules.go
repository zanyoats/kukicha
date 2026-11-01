package web

import (
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"strings"

	"github.com/zanyoats/kukicha/internal/usecase/command"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

type artistRulesViewModel struct {
	EyeBrow  string
	Heading  string
	URLs     URLs
	Rules    []artistRuleViewModel
	Artists  []query.HomeItem
	ArtistID int64
}

type artistRuleViewModel struct {
	query.ArtistRule
	ArtworkURL string
	EditorData string
}

type artistRuleEditorData struct {
	AlbumKey        string   `json:"album_key"`
	MappedArtists   []string `json:"mapped_artists"`
	OriginalArtists []string `json:"original_artists"`
	UpdateURL       string   `json:"update_url"`
}

func (c *webRouter) showArtistRules(w http.ResponseWriter, r *http.Request) {
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
	model := artistRulesViewModel{
		EyeBrow: "Settings", Heading: "Artist Rules", URLs: c.urls,
		Artists: artists, ArtistID: artistID,
	}
	var rules []query.ArtistRule
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
		rules, err = query.ArtistRules(r.Context(), c.db, artistID)
		if err != nil {
			internalServerError(w, "unable to load artist rules", "error", err)
			return
		}

	}
	views := make([]artistRuleViewModel, 0, len(rules))
	for _, rule := range rules {
		artworkURL := c.urls.AlbumArtworkURL(rule.AlbumID, query.AlbumArtwork64)
		data, err := json.Marshal(artistRuleEditorData{
			AlbumKey:        rule.AlbumKey,
			MappedArtists:   rule.MappedArtists,
			OriginalArtists: rule.OriginalArtists,
			UpdateURL:       c.urls.UpdateArtistRule,
		})
		if err != nil {
			internalServerError(w, "unable to encode artist rule editor data", "error", err)
			return
		}
		views = append(views, artistRuleViewModel{
			ArtistRule: rule,
			ArtworkURL: artworkURL,
			EditorData: string(data),
		})
	}
	model.Rules = views
	renderPage(w, c.renderer, "artist_rules.gohtml", model)
}

func (c *webRouter) updateArtistRule(w http.ResponseWriter, r *http.Request) {
	var input struct {
		AlbumKey      string    `json:"album_key"`
		MappedArtists []*string `json:"mapped_artists"`
	}
	if !decodeJSONRequest(w, r, &input) {
		return
	}
	if strings.TrimSpace(input.AlbumKey) == "" {
		apiError(w, http.StatusBadRequest, "An album key is required.")
		return
	}
	artists := make([]string, 0, len(input.MappedArtists))
	for _, value := range input.MappedArtists {
		if value == nil {
			apiError(
				w,
				http.StatusBadRequest,
				"Artist must be a string and not null",
			)
			return
		}
		artists = append(artists, *value)
	}
	if err := command.UpdateArtistRule(r.Context(), c.db, input.AlbumKey, artists); err != nil {
		switch {
		case errors.Is(err, command.ErrInvalidArtistMapping),
			errors.Is(err, command.ErrEmptyArtistMapping):
			apiError(w, http.StatusBadRequest, err.Error())
		case errors.Is(err, command.ErrArtistRuleNotFound):
			apiError(w, http.StatusNotFound, err.Error())
		default:
			slog.ErrorContext(r.Context(), "unable to update artist rule", "error", err)
			apiError(w, http.StatusInternalServerError, "Unable to save the artist rule.")
		}
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (c *webRouter) deleteArtistRule(w http.ResponseWriter, r *http.Request) {
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
	if err := command.DeleteArtistRule(r.Context(), c.db, input.AlbumKey); err != nil {
		internalServerError(w, "unable to delete artist rule", "error", err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
