package web

import (
	"net/http"

	"github.com/zanyoats/kukicha/internal/usecase/query"
)

type rootsViewModel struct {
	EyeBrow     string
	Heading     string
	URLs        URLs
	Roots       []query.Root
	LogStats    query.SyncLogStats
	ShowLogs    bool
	Logs        query.SyncLogBatch
	NextLogsURL string
}

func (c *appRoutes) showRoots(w http.ResponseWriter, r *http.Request) {
	showLogs := r.URL.Query().Get("logs") == "1"
	var before int64
	if r.URL.Query().Has("before") {
		var err error
		before, err = parsePositiveID(r, "before")
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
	}
	roots, err := query.Roots(r.Context(), c.db)
	if err != nil {
		internalServerError(w, "unable to load roots or sync logs", "error", err)
		return
	}
	stats, err := query.SyncLogCounts(r.Context(), c.db)
	if err != nil {
		internalServerError(w, "unable to load roots or sync logs", "error", err)
		return
	}
	model := rootsViewModel{
		EyeBrow:  "Settings",
		Heading:  "Roots",
		URLs:     c.urls,
		Roots:    roots,
		LogStats: stats,
		ShowLogs: showLogs,
	}
	if showLogs {
		model.Logs, err = query.SyncLogs(r.Context(), c.db, before, c.perPage)
		if err != nil {
			internalServerError(w, "unable to load roots or sync logs", "error", err)
			return
		}
		if model.Logs.NextBefore != 0 {
			model.NextLogsURL = c.urls.SyncLogsURL(model.Logs.NextBefore)
		}
	}
	renderPage(w, c.renderer, "roots.gohtml", model)
}
