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
	ConfiguredRoots []app.Root
	RootsByPath     map[string]*query.Root
	LatestSyncID    int64
}

func (c *appRoutes) showRoots(w http.ResponseWriter, r *http.Request) {
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
	rootsByPath := make(map[string]*query.Root, len(roots))
	for i := range roots {
		rootsByPath[roots[i].Path] = &roots[i]
	}
	model := rootsViewModel{
		EyeBrow:         "Settings",
		Heading:         "Roots",
		URLs:            c.urls,
		ConfiguredRoots: c.configuredRoots,
		RootsByPath:     rootsByPath,
		LatestSyncID:    latestSyncID,
	}
	renderPage(w, c.renderer, "roots.gohtml", model)
}
