package web

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"strconv"
	"strings"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/upload"
	"github.com/zanyoats/kukicha/internal/usecase/command"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

type rootsViewModel struct {
	EyeBrow         string
	Heading         string
	URLs            URLs
	Roots           []rootView
	RootsByKey      map[string]*query.Root
	LatestBuildID   int64
	Section         string
	Editing         bool
	AddressingStyle string
	ImportID        string
	MaxRequestBytes int64
	UploadLimit     string
	Modes           []addModeView
	Form            app.RootConfig
}

// rootView keeps source-specific fields out of templates.
type rootView struct {
	Label    string
	RootKey  string
	Location string
	Type     string
	Remote   bool
}

func rootViews(roots []app.Root) []rootView {
	views := make([]rootView, 0, len(roots))
	for _, root := range roots {
		switch root := root.(type) {
		case *app.LocalRoot:
			views = append(views, rootView{Label: root.Path, RootKey: root.ConfigID(), Location: root.Location(), Type: "local"})
		case *app.RemoteRoot:
			views = append(views, rootView{Label: root.ID, RootKey: root.ConfigID(), Location: root.Location(), Type: "remote", Remote: true})
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
	for i := range roots {
		rootsByKey[roots[i].ConfigID] = &roots[i]
	}
	stored, err := query.StoredRoots(r.Context(), c.db, true)
	if err != nil {
		internalServerError(w, "unable to load root settings", "error", err)
		return
	}
	section := "show"
	if r.URL.Query().Get("section") == "add" {
		section = "add"
	}
	importID := r.URL.Query().Get("import")
	if importID != "" && r.URL.Query().Get("edit") != "" {
		http.Error(w, "Choose either edit or import.", http.StatusBadRequest)
		return
	}
	if importID != "" {
		root := rootsByKey[importID]
		if root == nil || root.State != "available" {
			http.NotFound(w, r)
			return
		}
		section = "import"
	}
	form := app.RootConfig{RootStorageConfig: app.RootStorageConfig{Type: "local"}}
	editing := false
	if id := r.URL.Query().Get("edit"); id != "" {
		for _, root := range stored {
			if root.ConfigID() == id && rootsByKey[id] != nil && rootsByKey[id].State == "available" {
				form = query.RootSettings(root)
				editing = true
				break
			}
		}
		if !editing {
			http.NotFound(w, r)
			return
		}
		section = "edit"
	}
	model := rootsViewModel{
		Section: section, Editing: editing, Form: form,
		ImportID:        importID,
		MaxRequestBytes: c.maxUploadRequestBytes,
		UploadLimit:     uploadLimitLabel(c.maxUploadRequestBytes),
		Modes: []addModeView{
			{Value: upload.ModeFiles, Label: "Upload files"},
			{Value: upload.ModeSync, Label: "Server folder or bucket"},
		},
		EyeBrow:       "Settings",
		Heading:       "Storage",
		URLs:          c.urls,
		Roots:         rootViews(stored),
		RootsByKey:    rootsByKey,
		LatestBuildID: latestBuildID,
	}
	if form.AddressingStyle != nil {
		model.AddressingStyle = *form.AddressingStyle
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
		apiError(w, http.StatusBadRequest, "A storage location ID is required.")
		return
	}
	if err := command.MarkRootForRemoval(r.Context(), c.db, input.ConfigID); err != nil {
		if errors.Is(err, command.ErrRootNotFound) {
			apiError(w, http.StatusNotFound, err.Error())
		} else if errors.Is(err, command.ErrRootDropBuildRunning) || errors.Is(err, command.ErrRootNotAvailable) {
			apiError(w, http.StatusConflict, err.Error())
		} else {
			slog.ErrorContext(r.Context(), "unable to mark root for removal", "root", input.ConfigID, "error", err)
			apiError(w, http.StatusInternalServerError, "Unable to remove the storage location. Please try again.")
		}
		return
	}
	slog.InfoContext(r.Context(), "root marked for removal", "root", input.ConfigID)
	w.WriteHeader(http.StatusNoContent)
}

func (c *webRouter) restoreRoot(w http.ResponseWriter, r *http.Request) {
	var input struct {
		ConfigID string `json:"config_id"`
	}
	if !decodeJSONRequest(w, r, &input) {
		return
	}
	if strings.TrimSpace(input.ConfigID) == "" {
		apiError(w, http.StatusBadRequest, "A storage location ID is required.")
		return
	}
	if err := command.RestoreRoot(r.Context(), c.db, input.ConfigID); err != nil {
		if errors.Is(err, command.ErrRootNotFound) {
			apiError(w, http.StatusNotFound, err.Error())
		} else if errors.Is(err, command.ErrRootRestoreBuildRunning) || errors.Is(err, command.ErrRootNotPendingRemoval) {
			apiError(w, http.StatusConflict, err.Error())
		} else {
			slog.ErrorContext(r.Context(), "unable to restore root", "root", input.ConfigID, "error", err)
			apiError(w, http.StatusInternalServerError, "Unable to undo removal. Please try again.")
		}
		return
	}
	slog.InfoContext(r.Context(), "root restored", "root", input.ConfigID)
	w.WriteHeader(http.StatusNoContent)
}

func (c *webRouter) createRoot(w http.ResponseWriter, r *http.Request) {
	var input app.RootConfig
	if !decodeJSONRequest(w, r, &input) {
		return
	}
	validate := func(ctx context.Context, root app.Root) error {
		return upload.ValidateNew(ctx, root, c.conf.Settings.Scan.IgnorePrefixes, c.conf.Settings.Scan.IgnoreFiles)
	}
	writeRootResult(w, command.CreateRoot(r.Context(), c.db, c.conf.AppDir, input, validate))
}

func (c *webRouter) updateRoot(w http.ResponseWriter, r *http.Request) {
	id := r.URL.Query().Get("id")
	if strings.TrimSpace(id) == "" {
		apiError(w, http.StatusBadRequest, "A storage location ID is required.")
		return
	}
	var input app.RootStorageConfig
	if !decodeJSONRequest(w, r, &input) {
		return
	}
	writeRootResult(w, command.UpdateRoot(r.Context(), c.db, c.conf.AppDir, id, input))
}

func writeRootResult(w http.ResponseWriter, err error) {
	if err != nil {
		status := http.StatusBadRequest
		if errors.Is(err, command.ErrRootNotFound) {
			status = http.StatusNotFound
		} else if errors.Is(err, command.ErrRootChangeBuildRunning) || errors.Is(err, command.ErrRootNotAvailable) {
			status = http.StatusConflict
		}
		apiError(w, status, err.Error())
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
