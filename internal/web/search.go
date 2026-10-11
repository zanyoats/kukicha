package web

import "net/http"

type searchViewModel struct {
	EyeBrow string
	Heading string
	URLs    URLs
}

func (c *webRouter) showSearch(w http.ResponseWriter, r *http.Request) {
	renderPage(w, c.renderer, "search.gohtml", searchViewModel{
		EyeBrow: "Discover",
		Heading: "Search Results",
		URLs:    c.urls,
	})
}
