package web

import (
	"errors"
	"log/slog"
	"net/http"
	"strconv"
	"strings"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/usecase/command"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

type rootsViewModel struct {
	EyeBrow         string
	Heading         string
	URLs            URLs
	ConfiguredRoots []configuredRootView
	RootsByKey      map[string]*query.Root
	LatestBuildID   int64
	LostRoots       []query.Root
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
	var latestBuildID int64
	generatedID, err := query.AppMetadata(r.Context(), c.db, app.LibraryGeneratedJobId)
	if err != nil && !errors.Is(err, query.ErrAppMetadataNotFound) {
		internalServerError(w, "unable to load latest build job", "error", err)
		return
	}
	if err == nil {
		latestBuildID, err = strconv.ParseInt(generatedID, 10, 64)
		if err != nil || latestBuildID <= 0 {
			internalServerError(w, "invalid latest generating build job ID", "job_id", generatedID)
			return
		}
	}
	rootsByKey := make(map[string]*query.Root, len(roots))
	var lostRoots []query.Root
	for i := range roots {
		rootsByKey[roots[i].ConfigID] = &roots[i]
		if roots[i].State == "lost" {
			lostRoots = append(lostRoots, roots[i])
		}
	}
	model := rootsViewModel{
		LostRoots:       lostRoots,
		EyeBrow:         "Settings",
		Heading:         "Roots",
		URLs:            c.urls,
		ConfiguredRoots: configuredRootViews(c.configuredRoots),
		RootsByKey:      rootsByKey,
		LatestBuildID:   latestBuildID,
	}
	renderPage(w, c.renderer, "roots.gohtml", model)
}

func (c *webRouter) dropRoot(w http.ResponseWriter, r *http.Request) {
	var input struct {
		ConfigID string `json:"config_id"`
	}
	if !decodeJSONRequest(w, r, &input) {
		return
	}
	if strings.TrimSpace(input.ConfigID) == "" {
		apiError(w, http.StatusBadRequest, "A root ID is required.")
		return
	}
	if err := command.MarkLostRootForRemoval(r.Context(), c.db, input.ConfigID); err != nil {
		if errors.Is(err, command.ErrRootDropBuildRunning) || errors.Is(err, command.ErrRootNotLost) {
			apiError(w, http.StatusConflict, err.Error())
		} else {
			slog.ErrorContext(r.Context(), "unable to mark root for removal", "root", input.ConfigID, "error", err)
			apiError(w, http.StatusInternalServerError, "Unable to drop the root. Please try again.")
		}
		return
	}
	slog.InfoContext(r.Context(), "root marked for removal", "root", input.ConfigID)
	w.WriteHeader(http.StatusNoContent)
}
