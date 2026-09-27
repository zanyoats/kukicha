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
	LogStats        query.JobLogStats
	ShowLogs        bool
	Logs            query.JobLogBatch
	NextLogsURL     string
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
	var jobID int64
	generatedID, err := query.AppMetadata(r.Context(), c.db, app.LibraryGeneratedJobId)
	if err != nil && !errors.Is(err, query.ErrAppMetadataNotFound) {
		internalServerError(w, "unable to load latest sync job", "error", err)
		return
	}
	if err == nil {
		jobID, err = strconv.ParseInt(generatedID, 10, 64)
		if err != nil || jobID <= 0 {
			internalServerError(w, "invalid latest sync job ID", "job_id", generatedID)
			return
		}
	}
	stats, err := query.JobLogCounts(r.Context(), c.db, jobID)
	if err != nil {
		internalServerError(w, "unable to load roots or sync logs", "error", err)
		return
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
		LogStats:        stats,
		ShowLogs:        showLogs,
	}
	if showLogs {
		model.Logs, err = query.JobLogs(r.Context(), c.db, jobID, before, c.perPage)
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
