package web

import (
	"errors"
	"net/http"
	"strconv"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

type rootsViewModel struct {
	EyeBrow         string
	Heading         string
	URLs            URLs
	ConfiguredRoots []configuredRootView
	RootsByKey      map[string]*query.Root
	LatestSyncID    int64
}

// configuredRootView keeps source-specific fields out of templates.
type configuredRootView struct {
	Label    string
	RootKey  string
	Location string
	Type     string
	Remote   bool
}

func configuredRootViews(roots []app.Root) []configuredRootView {
	views := make([]configuredRootView, 0, len(roots))
	for _, root := range roots {
		switch root := root.(type) {
		case *app.LocalRoot:
			views = append(views, configuredRootView{Label: root.Path, RootKey: root.ConfigID(), Location: root.Location(), Type: "local"})
		case *app.RemoteRoot:
			views = append(views, configuredRootView{Label: root.ID, RootKey: root.ConfigID(), Location: root.Location(), Type: "remote", Remote: true})
		}
	}
	return views
}

func (c *webRouter) showRoots(w http.ResponseWriter, r *http.Request) {
	roots, err := query.Roots(r.Context(), c.db)
	if err != nil {
		internalServerError(w, "unable to load roots", "error", err)
		return
	}
	var latestSyncID int64
	generatedID, err := query.AppMetadata(r.Context(), c.db, app.LibraryGeneratedJobId)
	if err != nil && !errors.Is(err, query.ErrAppMetadataNotFound) {
		internalServerError(w, "unable to load latest sync job", "error", err)
		return
	}
	if err == nil {
		latestSyncID, err = strconv.ParseInt(generatedID, 10, 64)
		if err != nil || latestSyncID <= 0 {
			internalServerError(w, "invalid latest successful sync job ID", "job_id", generatedID)
			return
		}
	}
	rootsByKey := make(map[string]*query.Root, len(roots))
	for i := range roots {
		rootsByKey[roots[i].ConfigID] = &roots[i]
	}
	model := rootsViewModel{
		EyeBrow:         "Settings",
		Heading:         "Roots",
		URLs:            c.urls,
		ConfiguredRoots: configuredRootViews(c.configuredRoots),
		RootsByKey:      rootsByKey,
		LatestSyncID:    latestSyncID,
	}
	renderPage(w, c.renderer, "roots.gohtml", model)
}
