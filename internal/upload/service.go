package upload

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"mime/multipart"
	"os"
	"path/filepath"
	"strings"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/usecase/command"
)

const (
	ModeFiles = "files"
	ModeSync  = "sync"
)

// SyncRequest accepts exactly one input. Sources never become configured roots.
type SyncRequest struct {
	Mode   string            `json:"mode"`
	Folder string            `json:"folder,omitempty"`
	Remote *app.RemoteSource `json:"remote,omitempty"`
}

var ErrEnqueue = errors.New("unable to create build job")

type Service struct {
	DB    *sql.DB
	Roots []app.Root
}

// AcceptFiles streams files and extracts archives into an OS temp directory, then
// queues one build job. Every call is independent; duplicate uploads are allowed.
// The executor removes the directory after success. Failures leave it behind
// for system/manual cleanup; OS temp storage is not a recovery guarantee.
func (s *Service) AcceptFiles(ctx context.Context, rootID string, parts *multipart.Reader) (int64, error) {
	root, err := s.destination(rootID)
	if err != nil {
		return 0, err
	}
	tempDir, err := os.MkdirTemp("", "kukicha-add-")
	if err != nil {
		return 0, err
	}
	if err := extractParts(ctx, tempDir, parts); err != nil {
		return 0, err
	}
	return s.enqueue(ctx, root, tempDir, ModeFiles, nil)
}

// AcceptSync queues a source for direct scanning and later transfer. Each request
// creates a new job; source files are never changed by request handling.
func (s *Service) AcceptSync(ctx context.Context, rootID string, request SyncRequest) (int64, error) {
	root, err := s.destination(rootID)
	if err != nil {
		return 0, err
	}
	source, err := s.normalizeSource(request)
	if err != nil {
		return 0, err
	}
	return s.enqueue(ctx, root, "", ModeSync, source)
}

func (s *Service) destination(rootID string) (app.Root, error) {
	for _, root := range s.Roots {
		if root.ConfigID() == rootID {
			return root, nil
		}
	}
	return nil, errors.New("unknown destination root")
}

func (s *Service) enqueue(ctx context.Context, root app.Root, tempDir, mode string, source *app.BuildSource) (int64, error) {
	payload, err := json.Marshal(app.BuildJobPayload{
		Mode: app.BuildAdd, SourceType: app.BuildSourceType(mode), TempPath: tempDir, RootID: root.ConfigID(),
		RootLocation: Location(root), Source: source,
	})
	if err != nil {
		return 0, err
	}
	if err := command.ValidateJobSubmission(command.JobSubmission{Type: "build", Payload: payload}); err != nil {
		return 0, err
	}
	result, err := s.DB.ExecContext(ctx, `INSERT INTO jobs (type,payload) VALUES ('build',?)`, string(payload))
	if err != nil {
		return 0, errors.Join(ErrEnqueue, err)
	}
	id, err := result.LastInsertId()
	if err != nil {
		return 0, errors.Join(ErrEnqueue, err)
	}
	return id, nil
}

func resolveMissingPath(name string) (string, error) {
	resolved, err := filepath.EvalSymlinks(name)
	if err == nil {
		return resolved, nil
	}
	if !errors.Is(err, os.ErrNotExist) {
		return "", err
	}
	parent := filepath.Dir(name)
	if parent == name {
		return "", err
	}
	resolved, err = resolveMissingPath(parent)
	if err != nil {
		return "", err
	}
	return filepath.Join(resolved, filepath.Base(name)), nil
}

func (s *Service) normalizeSource(request SyncRequest) (*app.BuildSource, error) {
	if request.Mode != ModeSync {
		return nil, errors.New("JSON add requests require mode sync")
	}
	if (request.Folder == "") == (request.Remote == nil) {
		return nil, errors.New("provide exactly one server folder or one remote source")
	}
	var spec app.BuildSource
	if request.Remote != nil {
		remote := request.Remote
		if err := remote.Normalize(); err != nil {
			return nil, err
		}
		for _, root := range s.Roots {
			if target, ok := root.(*app.RemoteRoot); ok && target.EndpointURL == remote.EndpointURL && target.Bucket == remote.Bucket &&
				(strings.HasPrefix(target.Prefix, remote.Prefix) || strings.HasPrefix(remote.Prefix, target.Prefix)) {
				return nil, errors.New("remote source overlaps a managed root")
			}
		}
		spec.Remote = remote
	} else {
		if !filepath.IsAbs(request.Folder) {
			return nil, errors.New("source folder must be an absolute server path")
		}
		resolved, err := filepath.EvalSymlinks(filepath.Clean(request.Folder))
		if err != nil {
			return nil, err
		}
		info, err := os.Stat(resolved)
		if err != nil {
			return nil, err
		}
		if !info.IsDir() {
			return nil, fmt.Errorf("source %q must be a directory", request.Folder)
		}
		for _, root := range s.Roots {
			if local, ok := root.(*app.LocalRoot); ok {
				target, err := resolveMissingPath(local.Path)
				if err != nil {
					return nil, err
				}
				if inside(target, resolved) || inside(resolved, target) {
					return nil, errors.New("source folder overlaps a managed root")
				}
			}
		}
		spec.Folder = &app.LocalSource{Path: resolved}
	}
	return &spec, nil
}

// Location includes the endpoint as well as the bucket and prefix. Credentials
// are deliberately excluded: rotating credentials must not relocate an upload.
func Location(root app.Root) string {
	if remote, ok := root.(*app.RemoteRoot); ok {
		b, _ := json.Marshal([]string{"s3", remote.EndpointURL, remote.Bucket, remote.Prefix})
		return string(b)
	}
	return root.Location()
}

func inside(parent, child string) bool {
	rel, err := filepath.Rel(parent, child)
	return err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator))
}
