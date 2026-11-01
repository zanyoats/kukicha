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

type unrecognizedTrackView struct {
	query.UnrecognizedTrack
	Index      int
	Filename   string
	ArtistText string
	EditorData string
}

type unrecognizedTrackGroup struct {
	ConfigID  string
	Directory string
	Label     string
	Tracks    []unrecognizedTrackView
}

func (c *webRouter) showUnrecognizedTracks(w http.ResponseWriter, r *http.Request) {
	tracks, err := query.UnrecognizedTracks(r.Context(), c.db)
	if err != nil {
		internalServerError(w, "unable to load unrecognized tracks", "error", err)
		return
	}
	var groups []unrecognizedTrackGroup
	// App-managed upload directories include a globally unique job prefix.
	positions := make(map[string]int)
	for index, track := range tracks {
		slash := strings.LastIndex(track.UploadedPath, "/") + 1
		directory := track.UploadedPath[:slash]
		position, exists := positions[directory]
		if !exists {
			label := "./"
			if directory != "" {
				folder := strings.TrimSuffix(directory, "/")
				label = ".../" + folder[strings.LastIndex(folder, "/")+1:] + "/"
			}
			position = len(groups)
			positions[directory] = position
			groups = append(groups, unrecognizedTrackGroup{ConfigID: track.ConfigID, Directory: directory, Label: label})
		}
		data, err := json.Marshal(struct {
			UploadedPath string `json:"uploaded_path"`
			UpdateURL    string `json:"update_url"`
		}{track.UploadedPath, c.urls.UpdateUnrecognizedTrack})
		if err != nil {
			internalServerError(w, "unable to encode track editor", "error", err)
			return
		}
		groups[position].Tracks = append(groups[position].Tracks, unrecognizedTrackView{
			UnrecognizedTrack: track, Index: index, Filename: track.UploadedPath[slash:],
			ArtistText: strings.Join(track.Artists, "\n"), EditorData: string(data),
		})
	}
	renderPage(w, c.renderer, "unrecognized_tracks.gohtml", struct {
		EyeBrow        string
		Heading        string
		URLs           URLs
		Groups         []unrecognizedTrackGroup
		MultipleGroups bool
	}{"Library", "Unrecognized Tracks", c.urls, groups, len(groups) > 1})
}

func (c *webRouter) updateUnrecognizedTrack(w http.ResponseWriter, r *http.Request) {
	var input struct {
		UploadedPath string    `json:"uploaded_path"`
		Album        string    `json:"album"`
		Artists      []*string `json:"artists"`
	}
	if !decodeJSONRequest(w, r, &input) {
		return
	}
	if strings.TrimSpace(input.UploadedPath) == "" {
		apiError(w, http.StatusBadRequest, "An uploaded path is required.")
		return
	}
	artists := make([]string, 0, len(input.Artists))
	for _, artist := range input.Artists {
		if artist == nil {
			apiError(w, http.StatusBadRequest, "Each artist must be a string, not null.")
			return
		}
		artists = append(artists, *artist)
	}
	if err := command.UpdateUnrecognizedTrack(r.Context(), c.db, input.UploadedPath, input.Album, artists); err != nil {
		switch {
		case errors.Is(err, command.ErrInvalidTrackIdentity):
			apiError(w, http.StatusBadRequest, err.Error())
		case errors.Is(err, command.ErrUnrecognizedTrackNotFound):
			apiError(w, http.StatusNotFound, err.Error())
		default:
			slog.ErrorContext(r.Context(), "unable to save track identity", "error", err)
			apiError(w, http.StatusInternalServerError, "Unable to save this track. Please try again.")
		}
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
