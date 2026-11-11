package jobs

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/scan"
	"github.com/zanyoats/kukicha/internal/upload"
	"github.com/zanyoats/kukicha/internal/usecase/command"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

// Build scans and transfers files before publishing the library and generation marker.
type Build struct {
	DB            *sql.DB
	Conf          *app.AppConfig
	Notifications NotificationPublisher
}

func (j *Build) Run(ctx context.Context) error {
	conn, err := j.DB.Conn(ctx)
	if err != nil {
		return err
	}
	defer conn.Close()
	ctx = command.WithJobLogDB(ctx, conn)
	id, ok := app.JobIDFromContext(ctx)
	if !ok {
		return errors.New("build requires a job ID")
	}
	var state string
	var cancellation sql.NullString
	if err := conn.QueryRowContext(
		ctx, "SELECT state, cancel_requested_at FROM jobs WHERE id = ? AND type = 'build'", id,
	).Scan(&state, &cancellation); err != nil {
		return err
	}
	if cancellation.Valid {
		return context.Canceled
	}
	if state != "running" {
		return errors.New("build job is not running")
	}
	var published bool
	if err := conn.QueryRowContext(ctx, "SELECT library_published FROM jobs WHERE id = ?", id).Scan(&published); err != nil {
		return err
	}
	scanner := &fileScanner{Conf: j.Conf, Notifications: j.Notifications, logger: app.JobLogger(ctx)}
	if published {
		if err := scanner.completePendingArtwork(ctx, conn); err != nil {
			app.JobLogger(ctx).WarnContext(ctx, "optional artwork lookup failed", "error", err)
			return ctx.Err()
		}
		return nil
	}

	var raw, createdAt string
	if err := conn.QueryRowContext(
		ctx, "SELECT payload, created_at FROM jobs WHERE id = ? AND type = 'build'", id,
	).Scan(&raw, &createdAt); err != nil {
		return err
	}
	if err := command.ValidateJobSubmission(
		command.JobSubmission{Type: "build", Payload: json.RawMessage(raw)},
	); err != nil {
		return err
	}
	var p app.BuildJobPayload
	if err := json.Unmarshal([]byte(raw), &p); err != nil {
		return err
	}
	roots, err := query.StoredRoots(ctx, conn, false)
	if err != nil {
		return err
	}
	logger := app.JobLogger(ctx)
	var destination app.Root
	var target *buildTarget
	if p.Mode == app.BuildAdd {
		for _, root := range roots {
			if root.ConfigID() == p.RootID {
				destination = root
				break
			}
		}
		if destination == nil || upload.Location(destination) != p.RootLocation {
			return errors.New("build destination configuration changed; resubmit the addition")
		}
		target, err = newBuildTarget(ctx, destination)
		if err != nil {
			return err
		}
		defer target.Close()
		if p.SourceType == app.BuildSourceFiles {
			info, err := os.Lstat(p.TempPath)
			if err != nil {
				return fmt.Errorf("build temporary inputs unavailable; resubmit: %w", err)
			}
			if !info.IsDir() {
				return errors.New("build temporary path must be a real directory")
			}
		}
		if err := validateBuildSource(p, roots); err != nil {
			return err
		}
	}
	if err := command.PrepareScanResultsFromLibrary(ctx, conn, j.Conf); err != nil {
		return err
	}

	if p.Mode == app.BuildAdd {
		destinationPath, err := buildDestinationPath(createdAt, id)
		if err != nil {
			return err
		}

		var rootPos int
		if err := conn.QueryRowContext(
			ctx, "SELECT root_pos FROM scan_roots WHERE config_id = ?", p.RootID,
		).Scan(&rootPos); err != nil {
			return err
		}
		var source scanSource
		if p.SourceType == app.BuildSourceSync {
			if p.Source.Remote != nil {
				remote, err := newRemoteSource(ctx, p.Source.Remote)
				if err != nil {
					return err
				}
				defer func() {
					if err := os.RemoveAll(remote.tempDir); err != nil {
						app.JobLogger(ctx).WarnContext(ctx, "cannot remove remote temporary directory", "source", remote.Name(), "error", err)
					}
				}()
				source = remote
			} else {
				source = &localSource{path: p.Source.Folder.Path}
			}
		} else {
			source = &localSource{path: p.TempPath}
		}
		if err := scanner.fileScanWithWorkers(ctx, conn, []scanSource{source}, destination, rootPos, destinationPath, target.Copy); err != nil {
			return err
		}
		if err := ctx.Err(); err != nil {
			return err
		}
	}
	if err := command.NormalizeScanTracks(ctx, conn, j.Conf); err != nil {
		return err
	}
	result, err := command.BuildLibraryFromScan(ctx, conn,
		app.UnknownItem, j.Conf.Settings.Scan.SecondaryGenreSplitTokens,
		scan.RecognizedAudioExtensions, scan.RecognizedSidecarExtensions)
	if err != nil {
		return err
	}
	if p.SourceType == app.BuildSourceFiles {
		if err := os.RemoveAll(p.TempPath); err != nil {
			logger.WarnContext(ctx, "cannot remove completed build temporary directory", "error", err)
		}
	}
	if err := scanner.completePendingArtwork(ctx, conn); err != nil {
		logger.WarnContext(ctx, "optional artwork lookup failed", "error", err)
		if ctx.Err() != nil {
			return ctx.Err()
		}
	}

	if result.LogStats, err = query.JobLogCounts(ctx, conn, id); err != nil {
		return err
	}

	s, l := result.ScanStats, result.LogStats
	logger.InfoContext(ctx, "library build complete",
		"processed_files", s.ProcessedFiles,
		"roots", s.LibraryRoots,
		"albums", s.LibraryAlbums,
		"tracks", s.LibraryTracks,
		"sidecar_files", s.SidecarFiles,
		"album_sidecars", s.LibraryAlbumSidecars,
		"unassociated_sidecars", s.UnassociatedSidecars,
		"tracks_with_unknown_titles", s.LibraryTrackTitleUnknowns,
		"albums_with_unknown_names", s.LibraryAlbumUnknowns,
		"albums_with_unknown_artists", s.LibraryAlbumArtistUnknowns,
		"errors", l.Errors,
		"warnings", l.Warnings,
		"info", l.Info,
		"debug", l.Debug,
	)
	return nil
}

func validateBuildSource(p app.BuildJobPayload, roots []app.Root) error {
	if p.Source == nil {
		return nil
	}
	if r := p.Source.Remote; r != nil {
		if err := r.Normalize(); err != nil {
			return err
		}
		for _, root := range roots {
			if target, ok := root.(*app.RemoteRoot); ok && r.EndpointURL == target.EndpointURL && r.Bucket == target.Bucket && (strings.HasPrefix(r.Prefix, target.Prefix) || strings.HasPrefix(target.Prefix, r.Prefix)) {
				return errors.New("sync source overlaps a managed storage location")
			}
		}
		return nil
	}
	source, err := filepath.EvalSymlinks(p.Source.Folder.Path)
	if err != nil {
		return err
	}
	if source != p.Source.Folder.Path {
		return errors.New("sync source location changed")
	}
	overlaps := func(a, b string) bool {
		rel, err := filepath.Rel(a, b)
		return err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator))
	}
	for _, root := range roots {
		if local, ok := root.(*app.LocalRoot); ok {
			target, err := filepath.EvalSymlinks(local.Path)
			if errors.Is(err, os.ErrNotExist) {
				target = filepath.Clean(local.Path)
			} else if err != nil {
				return err
			}
			if overlaps(source, target) || overlaps(target, source) {
				return errors.New("sync source overlaps a managed storage location")
			}
		}
	}
	return nil
}

// Persisted creation time and ID keep the output prefix stable across retries.
func buildDestinationPath(createdAt string, id int64) (string, error) {
	created, err := time.Parse(time.DateTime, createdAt)
	if err != nil {
		return "", fmt.Errorf("parse build job creation time: %w", err)
	}
	if id <= 0 {
		return "", errors.New("build job ID must be positive")
	}
	return fmt.Sprintf("%s/job-%d/", created.UTC().Format("2006-01-02T150405Z"), id), nil
}
