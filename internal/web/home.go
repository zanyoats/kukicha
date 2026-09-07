package web

import (
	"net/http"

	"github.com/zanyoats/kukicha/internal/usecase/query"
)

type homeViewModel struct {
	URLs    URLs
	Artists []homeLink
	Genres  []homeLink
	Styles  []homeLink
}

type homeLink struct {
	Name string
	URL  string
}

func (c *appRoutes) showHome(w http.ResponseWriter, r *http.Request) {
	home, err := query.Home(r.Context(), c.db)
	if err != nil {
		internalServerError(w, "unable to load home page library data", "error", err)
		return
	}

	renderPage(w, c.renderer, "home.gohtml", homeViewModel{
		URLs: c.urls,
		Artists: homeLinks(home.Artists, func(id int64) string {
			return c.urls.ArtistURL(id)
		}),
		Genres: homeLinks(home.Genres, func(id int64) string {
			return c.urls.GenreURL(id)
		}),
		Styles: homeLinks(home.Styles, func(id int64) string {
			return c.urls.StyleURL(id)
		}),
	})
}

func homeLinks(items []query.HomeItem, itemURL func(int64) string) []homeLink {
	links := make([]homeLink, 0, len(items))
	for _, item := range items {
		links = append(links, homeLink{
			Name: item.Name,
			URL:  itemURL(item.ID),
		})
	}

	return links
}
