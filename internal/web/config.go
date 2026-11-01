package web

import "net/http"

type configPage struct {
	Title       string
	URL         string
	Description string
}

func configPages(urls URLs) []configPage {
	return []configPage{
		{"Listening Data", urls.ListeningData, "Listening data will appear here."},
	}
}

type configViewModel struct {
	EyeBrow string
	Heading string
	URLs    URLs
	configPage
}

func (c *webRouter) showConfig(page configPage) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		renderPage(w, c.renderer, "config.gohtml", configViewModel{
			EyeBrow:    "Settings",
			Heading:    page.Title,
			URLs:       c.urls,
			configPage: page,
		})
	}
}
