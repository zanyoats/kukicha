package web

import "net/http"

type searchViewModel struct {
	URLs URLs
}

func (c *appRoutes) showSearch(w http.ResponseWriter, r *http.Request) {
	renderPage(w, c.renderer, "search.gohtml", searchViewModel{
		URLs: c.urls,
	})
}
