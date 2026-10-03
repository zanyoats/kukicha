package jobs

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"log/slog"
	"path/filepath"
	"runtime/debug"
	"strconv"
	"sync"
	"time"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/scan"
	scanartwork "github.com/zanyoats/kukicha/internal/scan/artwork"
	"github.com/zanyoats/kukicha/internal/usecase/command"
	"github.com/zanyoats/kukicha/internal/usecase/query"
	"github.com/zanyoats/kukicha/internal/walk"
)

// Complete rebuild of library using cached resources. Reads and processes the
// the files. Any metadata changes to the files will be reflected.
//
// Use one connection for the entire job.
// This connections uses temporary tables and serializes writes from
// different goroutine workers.
type Sync struct {
	DB            *sql.DB
	Conf          *app.AppConfig
	Notifications NotificationPublisher
	logger        *slog.Logger
}

// NotificationPublisher lets jobs announce milestones without transport dependencies.
type NotificationPublisher interface {
	Publish(app.Notification)
}

func (j *Sync) notify(jobID int64, message string) {
	if j.Notifications != nil {
		j.Notifications.Publish(app.Notification{JobID: jobID, Message: message})
	}
}

var ErrITunesArtworkLookup = errors.New("iTunes artwork lookup")

func (j *Sync) Run(ctx context.Context) error {
	j.logger = app.JobLogger(ctx)
	jobID, ok := app.JobIDFromContext(ctx)
	if !ok {
		return errors.New("sync requires a job ID in context")
	}

	conn, err := j.DB.Conn(ctx)
	if err != nil {
		return err
	}
	defer conn.Close()
	// This is kind of a hack to create a derived context that uses this `conn`
	// instead of `j.DB`. Any writes need to happen on `conn` for this job otherwise
	// sqlite could reject writes coming from another connection on `j.DB`
	//
	// The underying insert from slog does something like this
	// ```
	//    db := h.db
	//    if scoped, ok := ctx.Value(jobLogDBKey{}).(JobLogDB); ok {
	//.       db = scoped
	//    }
	//    ...
	// ```
	// Below method sets jobLogDBKey with `conn`
	ctx = command.WithJobLogDB(ctx, conn)

	// Recovery preserves IDs and claims the oldest job first, so a later Sync
	// cannot overwrite this marker before the interrupted Sync is retried.
	generatedJobID, err := query.AppMetadata(ctx, conn, app.LibraryGeneratedJobId)
	if err != nil && !errors.Is(err, query.ErrAppMetadataNotFound) {
		return err
	}
	if err == nil && generatedJobID == strconv.FormatInt(jobID, 10) {
		return nil
	}

	if err := command.PrepareScanResults(
		ctx,
		conn,
		j.Conf.Settings.Server.Database.CacheSizeKiB,
	); err != nil {
		return err
	}

	j.notify(jobID, "Scanning local files.")
	if err := j.localFileScanWithWorkers(ctx, conn); err != nil {
		return err
	}

	result, err := command.BuildLibraryFromScan(
		ctx, conn,
		scan.UnknownItem,
		j.Conf.Settings.Scan.SecondaryGenreSplitTokens,
		scan.RecognizedAudioExtensions,
		scan.RecognizedPlaylistExtensions,
		scan.RecognizedSidecarExtensions,
	)
	if err != nil {
		return err
	}

	// BuildLibraryFromScan has committed, so the library is ready to use.
	message := "Library scan completed, refresh to view your library."
	if j.Conf.Settings.Scan.ITunes.Enabled {
		message += " iTunes library artwork lookup is starting."
	}
	j.notify(jobID, message)

	if j.Conf.Settings.Scan.ITunes.Enabled {
		if err := j.lookupITunesArtwork(ctx, conn); err != nil {
			return err
		}
	}

	if result.LogStats, err = query.JobLogCounts(ctx, conn, jobID); err != nil {
		return err
	}

	// Only mark this sync complete after all artwork has been attached. An
	// interrupted lookup retries the rebuild and reuses cached lookup results.
	if err := command.MarkLibrarySyncComplete(ctx, conn, jobID); err != nil {
		return err
	}

	s, l := result.ScanStats, result.LogStats
	j.logger.InfoContext(ctx, "library sync complete",
		"processed_files", s.ProcessedFiles,
		"roots", s.LibraryRoots,
		"albums", s.LibraryAlbums,
		"tracks", s.LibraryTracks,
		"playlists", s.LibraryPlaylists,
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

func (j *Sync) lookupITunesArtwork(ctx context.Context, conn *sql.Conn) error {
	client := scanartwork.NewITunesClient(
		nil,
		scanartwork.ITunesClientConfig{
			RequestTimeout: j.Conf.Settings.Scan.ITunes.RequestTimeout.Duration,
			LookupInterval: j.Conf.Settings.Scan.ITunes.LookupInterval.Duration,
			RetryAttempts:  j.Conf.Settings.Scan.ITunes.RetryAttempts,
		},
	)

	const batchSize = 100
	var afterID int64
	for {
		candidates, lastID, err := query.LibraryArtworkCandidates(ctx, conn, afterID, batchSize)
		if err != nil {
			return err
		}
		if lastID == 0 {
			return nil
		}

		resolved := make(map[int64]bool)
		for _, candidate := range candidates {
			if err := ctx.Err(); err != nil {
				return err
			}
			if resolved[candidate.TrackID] {
				continue
			}
			result, _, err := lookupCachedArtwork(
				ctx, conn, client, candidate.Kind, candidate.LookupID,
			)
			if err != nil {
				if ctx.Err() != nil {
					return ctx.Err()
				}
				if !errors.Is(err, ErrITunesArtworkLookup) {
					return err
				}
				j.logger.ErrorContext(ctx, fmt.Sprintf(
					"iTunes artwork lookup failed: kind %s, lookup ID %s: %v",
					candidate.Kind, candidate.LookupID, err,
				), "root_path", candidate.RootPath, "path", candidate.Path)
				continue
			}

			if result.ResultKind != app.ITunesResultHit {
				continue
			}

			// Cache into artwork thumbnails table, then repair itunes cache
			// with hashed `name` value
			name, err := command.CacheArtworkThumbnails(
				ctx, conn, result.Bytes,
				fmt.Sprintf("iTunes %s artwork %s", candidate.Kind, candidate.LookupID),
				candidate.RootPath, candidate.Path,
			)
			if err != nil {
				return err
			}
			if name == "" {
				continue
			}

			if err := command.LinkCachedITunesArtworkThumbnail(
				ctx, conn, result.LookupKind, result.LookupID, name,
			); err != nil {
				return err
			}

			if err := command.AttachLibraryArtwork(ctx, conn, candidate.TrackID, name); err != nil {
				return err
			}
			resolved[candidate.TrackID] = true
		}

		afterID = lastID
	}
}

type work struct {
	root      app.Root
	localFile walk.LocalWalkedFile
	fileType  app.FileType
}

func (j *Sync) localFileScanWithWorkers(
	ctx context.Context,
	conn *sql.Conn,
) error {
	workCtx, cancel := context.WithCancel(ctx)
	defer cancel()

	workers := j.Conf.Settings.Jobs.Workers.Resolve()
	if j.Conf.Settings.Jobs.Workers.IsAuto() {
		j.logger.InfoContext(ctx, "resolved number of workers",
			"workers", workers,
		)
	}
	queue := make(chan work, workers)

	var pool sync.WaitGroup
	var failOnce sync.Once
	var firstErr error
	var firstWork *work

	// Callers should provide a non-nil error
	fail := func(err error, item *work) {
		failOnce.Do(func() {
			firstErr = err

			if item != nil {
				firstWork = item
			}

			cancel()
		})
	}

	progress := &syncProgress{
		logger:   j.logger,
		lastLog:  time.Now(),
		interval: j.Conf.Settings.Scan.ProgressLogInterval.Duration,
	}

	// Start the workers/consumers
	for range workers {
		pool.Add(1)

		go func() {
			defer pool.Done()

			for {
				select {
				case <-workCtx.Done():
					return
				case item, ok := <-queue:
					if !ok {
						return
					}

					// prioritize cancellation if also occurred
					if workCtx.Err() != nil {
						return
					}

					if err := j.workOn(workCtx, conn, item); err != nil {
						fail(
							fmt.Errorf(
								"work on %q: %w",
								filepath.Join(item.root.RootPath, item.localFile.RelPath),
								err,
							),
							&item,
						)
						return
					}

					progress.complete(workCtx)
				}
			}
		}()
	}

	// Produce work in this goroutine
	if err := func() error {
		for _, root := range j.Conf.Roots {
			if err := workCtx.Err(); err != nil {
				return err
			}

			j.logger.InfoContext(ctx, "scanning library root: starting",
				"path", root.RootPath,
			)

			if err := walk.Dir(root.RootPath, func(
				localFile walk.LocalWalkedFile,
				walkErr error,
			) error {
				if err := workCtx.Err(); err != nil {
					return err
				}

				if walkErr != nil {
					j.logger.ErrorContext(workCtx, fmt.Sprintf(
						"skipped work on file: %v",
						walkErr,
					), "root_path", root.RootPath, "path", localFile.RelPath)
					return nil
				}

				if localFile.StatErr != nil {
					j.logger.ErrorContext(workCtx, fmt.Sprintf(
						"skipped work on file: %v",
						localFile.StatErr,
					), "root_path", root.RootPath, "path", localFile.RelPath)
					return nil
				}

				fileType, recognizable := scan.IsRecognizable(
					localFile.Name,
					j.Conf.Settings.Scan.RecognizedSidecarArtwork,
				)
				if !recognizable {
					return nil
				}

				item := work{
					root:      root,
					localFile: localFile,
					fileType:  fileType,
				}

				select {
				case queue <- item:
					return nil
				case <-workCtx.Done():
					return workCtx.Err()
				}
			}); err != nil {
				return fmt.Errorf(
					"walk root %q: %w", root.RootPath, err,
				)
			}

			j.logger.InfoContext(ctx, "library root queued",
				"path", root.RootPath,
			)
		}

		return nil
	}(); err != nil {
		// Record a producer failure and request shutdown.
		fail(err, nil)
	}

	// Producer queued all work; workers drain queued work then exit.
	close(queue)

	// Join the workers, even after failure or cancellation.
	pool.Wait()

	if firstErr != nil {
		// All workers have stopped. Use the original job context:
		// workCtx was canceled by fail().
		if firstWork != nil && ctx.Err() == nil {
			j.logger.ErrorContext(ctx, fmt.Sprintf("cannot process file: %v", firstErr),
				"root_path", firstWork.root.RootPath, "path", firstWork.localFile.RelPath)
		}

		return firstErr
	}

	// Workers can exit on parent cancellation without calling fail, therefore
	// `firstErr` can be nil. This check honors ensures the job ends early in
	// in this case with giving the calling the sense it was successful.
	if err := ctx.Err(); err != nil {
		return err
	}

	j.logger.InfoContext(ctx, "library scan completed",
		"processed_files", progress.count(),
	)

	return nil
}

// Called from a worker goroutine
// Implements the processing of a recognizable file; essentially
// 1. parses file metadata and normalizes its,
// 2. appends it to a temporary table to hold scanned tracks.
func (j *Sync) workOn(
	workCtx context.Context,
	conn *sql.Conn,
	item work,
) (err error) {
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("scan worker panic: %v\n%s", r, debug.Stack())
		}
	}()

	fileIdentity := app.FileIdentity{
		Root: item.root,
		Path: item.localFile.RelPath,
		Ext:  item.localFile.Ext,
		Stem: item.localFile.Stem,
	}
	fileSnapshot := app.FileSnapshot{
		FileType:    item.fileType,
		FileSize:    item.localFile.FileSize,
		FileModTime: item.localFile.FileModTime,
	}
	scannedFile := &app.ScannedFile{
		FileIdentity: fileIdentity,
		FileSnapshot: fileSnapshot,
		Metadata: &app.TrackMetadata{
			Artists:         []string{},
			ArtistSort:      []string{},
			AlbumArtists:    []string{},
			AlbumArtistSort: []string{},
			Performers:      []string{},
			Genres:          []string{},
		},
		NormalizedMetadata: &app.NormalizedTrackMetadata{},
	}

	switch item.fileType {
	case app.FileTypeAudio:
		localPath := filepath.Join(item.root.RootPath, item.localFile.RelPath)

		if err := scan.ApplyTrackMetadata(localPath, j.Conf, scannedFile); err != nil {
			j.logger.ErrorContext(workCtx, err.Error(), "root_path", item.root.RootPath, "path", item.localFile.RelPath)
		}

		// Since this call involves a db connection its error should
		// fail the job.
		err = scan.ApplyMetadataNormalizations(workCtx, j.Conf, conn, scannedFile)

	case app.FileTypeSidecarCover, app.FileTypeSidecarOther:

	default: // app.FileTypePlaylist
		j.logger.InfoContext(workCtx, "skipping unsupported playlist item", "root_path", scannedFile.RootPath, "path", scannedFile.Path)
	}

	if err == nil {
		err = command.InsertScannedFile(
			workCtx,
			conn,
			scannedFile,
			j.Conf.Settings.Scan.LocalArtworkMaxBytes,
		)
	}

	return err
}

type syncProgress struct {
	logger    *slog.Logger
	mu        sync.Mutex
	processed int
	lastLog   time.Time
	interval  time.Duration
}

func (p *syncProgress) complete(ctx context.Context) {
	p.mu.Lock()
	defer p.mu.Unlock()

	p.processed++

	now := time.Now()
	if p.interval > 0 && now.Sub(p.lastLog) >= p.interval {
		p.logger.InfoContext(ctx, "library scan progress",
			"processed_files", p.processed,
		)
		p.lastLog = now
	}
}

func (p *syncProgress) count() int {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.processed
}

type itunesArtworkCache interface {
	ExecContext(
		context.Context,
		string,
		...any,
	) (sql.Result, error)
	QueryRowContext(
		context.Context,
		string,
		...any,
	) *sql.Row
}

// lookupCachedArtwork returns the result and whether it came from the cache.
func lookupCachedArtwork(
	ctx context.Context,
	db itunesArtworkCache,
	client *scanartwork.ITunesClient,
	kind app.ITunesLookupKind,
	lookupID string,
) (app.ITunesLookupResult, bool, error) {
	cached, found, err := query.LoadCachedITunesArtwork(
		ctx, db, kind, lookupID,
	)
	if err != nil {
		if !errors.Is(err, query.ErrInvalidCachedITunesArtwork) {
			return app.ITunesLookupResult{}, false, err
		}

		// Invalid cached data: fall through to fetch and replace it.
	} else if found {
		return cached, true, nil
	}

	if client == nil {
		return app.ITunesLookupResult{}, false, errors.New("iTunes client is nil")
	}

	result, err := client.LookupArtwork(ctx, kind, lookupID)
	if err != nil {
		return app.ITunesLookupResult{}, false, fmt.Errorf(
			"%w: %w", ErrITunesArtworkLookup, err,
		)
	}

	if err := command.StoreCachedITunesArtwork(ctx, db, result); err != nil {
		return app.ITunesLookupResult{}, false, err
	}

	return result, false, nil
}
