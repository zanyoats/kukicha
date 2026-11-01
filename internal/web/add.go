package web

import (
	"net/http"
	"sort"
	"strings"

	"github.com/zanyoats/kukicha/internal/scan"
)

func (c *webRouter) showAdd(w http.ResponseWriter, r *http.Request) {
	extensions := make([]string, 0, len(scan.RecognizedAudioExtensions))
	for extension := range scan.RecognizedAudioExtensions {
		extensions = append(extensions, extension)
	}
	sort.Strings(extensions)
	extensions = append(extensions, ".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz", ".tbz2", ".tar.xz", ".txz", ".tar.zst", ".tzst")
	renderPage(w, c.renderer, "add.gohtml", struct {
		URLs                     URLs
		EyeBrow, Heading, Accept string
		ConfiguredRoots          []configuredRootView
	}{
		URLs: c.urls, EyeBrow: "Library", Heading: "Add to Kukicha",
		Accept:          strings.Join(extensions, ","),
		ConfiguredRoots: configuredRootViews(c.configuredRoots),
	})
}
