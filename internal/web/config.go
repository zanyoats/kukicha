package web

import "net/http"

type configPage struct {
	Title       string
	URL         string
	Description string
}

func configPages(urls URLs) []configPage {
	return []configPage{
		{"Roots", urls.Roots, "Music library roots will appear here."},
		{"Metadata Overrides", urls.MetadataOverrides, "Metadata overrides will appear here."},
		{"Listening Data", urls.ListeningData, "Listening data will appear here."},
		{"Cache", urls.Cache, "Cache information will appear here."},
		{"Jobs", urls.Jobs, "Jobs will appear here."},
		{"Help", urls.Help, "Help and app information will appear here."},
	}
}

type configViewModel struct {
	URLs URLs
	configPage
}

func (c *appRoutes) showConfig(page configPage) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		renderPage(w, c.renderer, "config.gohtml", configViewModel{
			URLs:       c.urls,
			configPage: page,
		})
	}
}
