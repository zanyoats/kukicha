package web

import (
	"fmt"
	"net/http"

	"github.com/zanyoats/kukicha/internal/usecase/query"
)

type homeViewModel struct {
	LibraryGenerated          bool
	LibraryHasTracks          bool
	HasConfiguredRoots        bool
	InitialBuildState         string
	ConfiguredRoots           []configuredRootView
	ConfigFilePath            string
	RecognizedAudioExtensions []string
	EyeBrow                   string
	Heading                   string
	URLs                      URLs
	Artists                   []homeLink
	Genres                    []homeLink
	Styles                    []homeLink
}

type homeLink struct {
	ID                     int64
	Name                   string
	URL                    string
	RadioViable            bool
	RadioUnavailableReason string
}

func (c *webRouter) showHome(w http.ResponseWriter, r *http.Request) {
	home, err := query.Home(r.Context(), c.db)
	if err != nil {
		internalServerError(w, "unable to load home page library data", "error", err)
		return
	}

	renderPage(w, c.renderer, "home.gohtml", homeViewModel{
		LibraryGenerated:          home.LibraryGenerated,
		LibraryHasTracks:          home.LibraryHasTracks,
		HasConfiguredRoots:        len(c.configuredRoots) > 0,
		InitialBuildState:         home.InitialBuildState,
		ConfiguredRoots:           configuredRootViews(c.configuredRoots),
		ConfigFilePath:            c.configFilePath,
		RecognizedAudioExtensions: c.recognizedAudioExtensions,
		EyeBrow:                   "Kukicha",
		Heading:                   "Browse",
		URLs:                      c.urls,
		Artists: homeLinks(home.Artists, c.radioMinTracks, func(id int64) string {
			return c.urls.ArtistURL(id)
		}),
		Genres: homeLinks(home.Genres, c.radioMinTracks, func(id int64) string {
			return c.urls.GenreURL(id)
		}),
		Styles: homeLinks(home.Styles, c.radioMinTracks, func(id int64) string {
			return c.urls.StyleURL(id)
		}),
	})
}

func homeLinks(items []query.HomeItem, minTracks int64, itemURL func(int64) string) []homeLink {
	links := make([]homeLink, 0, len(items))
	for _, item := range items {
		reason := ""
		if item.TrackCount < minTracks {
			unit := "tracks"
			if item.TrackCount == 1 {
				unit = "track"
			}
			reason = fmt.Sprintf("Only %d %s; this radio requires at least %d tracks.", item.TrackCount, unit, minTracks)
		}
		links = append(links, homeLink{
			ID:                     item.ID,
			Name:                   item.Name,
			URL:                    itemURL(item.ID),
			RadioViable:            item.TrackCount >= minTracks,
			RadioUnavailableReason: reason,
		})
	}

	return links
}
