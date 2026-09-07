package web

import (
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"mime"
	"net/http"
	"strings"

	"github.com/zanyoats/kukicha/internal/usecase/command"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

type artistSplitRulesViewModel struct {
	URLs  URLs
	Rules []artistSplitRuleViewModel
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

func (c *appRoutes) showArtistSplitRules(w http.ResponseWriter, r *http.Request) {
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
		URLs:  c.urls,
		Rules: views,
	})
}

func (c *appRoutes) updateArtistSplitRule(w http.ResponseWriter, r *http.Request) {
	contentType, _, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if err != nil || contentType != "application/json" {
		artistSplitRuleError(w, http.StatusUnsupportedMediaType, "Content-Type must be application/json.")
		return
	}
	var input struct {
		AlbumKey      string    `json:"album_key"`
		MappedArtists []*string `json:"mapped_artists"`
	}
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20)) // 1 MiB
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&input); err != nil {
		artistSplitRuleError(w, http.StatusBadRequest, "Invalid JSON request.")
		return
	}
	// checks to make sure no more to parse:
	// e.g. {"album_key":"abc","mapped_artists":["Artist"]} {"extra":"object"}
	if err := decoder.Decode(new(any)); err != io.EOF {
		artistSplitRuleError(w, http.StatusBadRequest, "Expected a single JSON object.")
		return
	}
	if strings.TrimSpace(input.AlbumKey) == "" {
		artistSplitRuleError(w, http.StatusBadRequest, "An album key is required.")
		return
	}
	artists := make([]string, 0, len(input.MappedArtists))
	for _, value := range input.MappedArtists {
		if value == nil || strings.ContainsAny(*value, "\r\n") {
			artistSplitRuleError(w, http.StatusBadRequest, "Each artist must be a string on a single line.")
			return
		}
		if artist := strings.TrimSpace(*value); artist != "" {
			artists = append(artists, artist)
		}
	}
	if len(artists) == 0 {
		artistSplitRuleError(w, http.StatusBadRequest, "Enter at least one artist.")
		return
	}
	if err := command.UpdateArtistSplitRule(r.Context(), c.db, input.AlbumKey, artists); err != nil {
		if errors.Is(err, command.ErrArtistSplitRuleNotFound) {
			artistSplitRuleError(w, http.StatusNotFound, "Artist split rule not found.")
		} else {
			slog.ErrorContext(r.Context(), "unable to update artist split rule", "error", err)
			artistSplitRuleError(w, http.StatusInternalServerError, "Unable to save the artist split rule.")
		}
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func artistSplitRuleError(w http.ResponseWriter, status int, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": message})
}
