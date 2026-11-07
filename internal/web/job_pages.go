package web

import (
	"database/sql"
	"errors"
	"net/http"

	"github.com/zanyoats/kukicha/internal/usecase/query"
)

type jobsViewModel struct {
	EyeBrow     string
	Heading     string
	URLs        URLs
	Jobs        query.JobBatch
	NextJobsURL string
}

func (c *webRouter) showJobs(w http.ResponseWriter, r *http.Request) {
	before, err := parseBefore(r)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	var jobs query.JobBatch
	if r.URL.Query().Has("job_id") {
		id, parseErr := parsePositiveID(r, "job_id")
		if parseErr != nil {
			http.Error(w, parseErr.Error(), http.StatusBadRequest)
			return
		}
		job, readErr := query.JobByID(r.Context(), c.db, id)
		if errors.Is(readErr, sql.ErrNoRows) {
			http.NotFound(w, r)
			return
		}
		err = readErr
		jobs.Entries = []query.Job{job}
	} else {
		jobs, err = query.Jobs(r.Context(), c.db, before, c.perPage)
	}
	if err != nil {
		internalServerError(w, "unable to load jobs", "error", err)
		return
	}
	model := jobsViewModel{EyeBrow: "Settings", Heading: "Jobs", URLs: c.urls, Jobs: jobs}
	if jobs.NextBefore != 0 {
		model.NextJobsURL = c.urls.JobsURL(jobs.NextBefore)
	}
	renderPage(w, c.renderer, "jobs.gohtml", model)
}

type jobLogsViewModel struct {
	EyeBrow     string
	Heading     string
	URLs        URLs
	Job         query.Job
	LogStats    query.JobLogStats
	Logs        query.JobLogBatch
	NextLogsURL string
}

func (c *webRouter) showJobLogs(w http.ResponseWriter, r *http.Request) {
	id, err := parsePositiveID(r, "job_id")
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	before, err := parseBefore(r)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	job, err := query.JobByID(r.Context(), c.db, id)
	if errors.Is(err, sql.ErrNoRows) {
		http.NotFound(w, r)
		return
	}
	if err != nil {
		internalServerError(w, "unable to load job", "error", err)
		return
	}
	stats, err := query.JobLogCounts(r.Context(), c.db, id)
	if err != nil {
		internalServerError(w, "unable to load job log counts", "error", err)
		return
	}
	logs, err := query.JobLogs(r.Context(), c.db, id, before, c.perPage)
	if err != nil {
		internalServerError(w, "unable to load job logs", "error", err)
		return
	}
	model := jobLogsViewModel{EyeBrow: "Jobs", Heading: "Job logs", URLs: c.urls, Job: job, LogStats: stats, Logs: logs}
	if logs.NextBefore != 0 {
		model.NextLogsURL = c.urls.JobLogsURL(id, logs.NextBefore)
	}
	renderPage(w, c.renderer, "job_logs.gohtml", model)
}

func parseBefore(r *http.Request) (int64, error) {
	if !r.URL.Query().Has("before") {
		return 0, nil
	}
	return parsePositiveID(r, "before")
}
