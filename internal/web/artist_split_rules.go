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

type artistSplitRulesViewModel struct {
	EyeBrow string
	Heading string
	URLs    URLs
	Rules   []artistSplitRuleViewModel
}

type artistSplitRuleViewModel struct {
	query.ArtistSplitRule
	ArtworkURL string
	EditorData string
}

type artistSplitRuleEditorData struct {
	AlbumKey      string   `json:"album_key"`
	MappedArtists []string `json:"mapped_artists"`
	UpdateURL     string   `json:"update_url"`
}

func (c *webRouter) showArtistSplitRules(w http.ResponseWriter, r *http.Request) {
	rules, err := query.ArtistSplitRules(r.Context(), c.db)
	if err != nil {
		internalServerError(w, "unable to load artist split rules", "error", err)
		return
	}
	views := make([]artistSplitRuleViewModel, 0, len(rules))
	for _, rule := range rules {
		artworkURL := c.urls.AlbumArtworkURL(rule.AlbumID, query.AlbumArtwork64)
		data, err := json.Marshal(artistSplitRuleEditorData{
			AlbumKey:      rule.AlbumKey,
			MappedArtists: rule.MappedArtists,
			UpdateURL:     c.urls.UpdateArtistSplitRule,
		})
		if err != nil {
			internalServerError(w, "unable to encode artist split rule editor data", "error", err)
			return
		}
		views = append(views, artistSplitRuleViewModel{
			ArtistSplitRule: rule,
			ArtworkURL:      artworkURL,
			EditorData:      string(data),
		})
	}
	renderPage(w, c.renderer, "artist_split_rules.gohtml", artistSplitRulesViewModel{
		EyeBrow: "Settings",
		Heading: "Artist Split Rules",
		URLs:    c.urls,
		Rules:   views,
	})
}

func (c *webRouter) updateArtistSplitRule(w http.ResponseWriter, r *http.Request) {
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
	if err := command.UpdateArtistSplitRule(r.Context(), c.db, input.AlbumKey, artists); err != nil {
		switch {
		case errors.Is(err, command.ErrInvalidArtistMapping),
			errors.Is(err, command.ErrEmptyArtistMapping):
			apiError(w, http.StatusBadRequest, err.Error())
		case errors.Is(err, command.ErrArtistSplitRuleNotFound):
			apiError(w, http.StatusNotFound, err.Error())
		default:
			slog.ErrorContext(r.Context(), "unable to update artist split rule", "error", err)
			apiError(w, http.StatusInternalServerError, "Unable to save the artist split rule.")
		}
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
