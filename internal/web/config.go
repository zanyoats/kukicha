package web

import "net/http"

type configPage struct {
	Title       string
	Path        string
	Description string
}

var configPages = []configPage{
	{"Roots", "/roots", "Music library roots will appear here."},
	{"Metadata Overrides", "/metadataOverrides", "Metadata overrides will appear here."},
	{"Listening Data", "/listeningData", "Listening data will appear here."},
	{"Cache", "/cache", "Cache information will appear here."},
	{"Jobs", "/jobs", "Jobs will appear here."},
	{"Help", "/help", "Help and app information will appear here."},
}

type configViewModel struct {
	sharedURLs
	configPage
}

func (c *appRoutes) showConfig(page configPage) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		renderPage(w, c.renderer, "config.gohtml", configViewModel{
			sharedURLs: getSharedURLs(c.appBasePath),
			configPage: page,
		})
	}
}
