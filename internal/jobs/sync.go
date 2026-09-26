package jobs

import (
	"context"
	"database/sql"
	"encoding/json"
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
	"github.com/zanyoats/kukicha/internal/usecase/command"
	"github.com/zanyoats/kukicha/internal/usecase/query"
	"github.com/zanyoats/kukicha/internal/walk"
)

type Sync struct {
	DB   *sql.DB
	Conf *app.AppConfig
}

func (j *Sync) Run(ctx context.Context) error {
	jobID, ok := app.JobIDFromContext(ctx)
	if !ok {
		return errors.New("sync requires a job ID in context")
	}

	// Use one connection for the entire job.
	// This connections uses temporary tables and serializes writes from
	// different goroutine workers.
	conn, err := j.DB.Conn(ctx)
	if err != nil {
		return err
	}
	defer conn.Close()

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

	if err := localFileScanWithWorkers(ctx, conn, j.Conf); err != nil {
		return err
	}

	// Complete Sync job by atomically:
	//   1. building the entire library from scanned data in temp tables, and
	//   2. create subsequent iTunesLookup job (if enabled) to fetch artwork
	//      if neither: sidecar, embedded
	tx, err := conn.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	result, err := command.BuildLibraryFromScan(
		ctx, tx,
		scan.RecognizedAudioExtensions,
		scan.RecognizedPlaylistExtensions,
		scan.RecognizedSidecarExtensions,
		scan.UnknownItem,
		j.Conf.Settings.Scan.SecondaryGenreSplitTokens,
	)
	if err != nil {
		return err
	}
	if j.Conf.Settings.Scan.ITunes.Enabled {
		if _, err := command.InsertJob(ctx, tx, command.JobSubmission{
			Type:    "iTunesLookup",
			Payload: json.RawMessage(`{}`),
		}); err != nil {
			return err
		}
	}
	if err := tx.Commit(); err != nil {
		return err
	}

	s, l := result.ScanStats, result.LogStats
	slog.Info("library sync complete",
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
		"errors", l.ErrorCount,
		"warnings", l.WarnCount,
		"info", l.InfoCount,
	)

	return nil
}

type work struct {
	root      app.Root
	localFile walk.LocalWalkedFile
	fileType  app.FileType
}

func localFileScanWithWorkers(
	ctx context.Context,
	conn *sql.Conn,
	conf *app.AppConfig,
) error {
	workCtx, cancel := context.WithCancel(ctx)
	defer cancel()

	workers := conf.Settings.Jobs.Workers.Resolve()
	if conf.Settings.Jobs.Workers.IsAuto() {
		slog.Info("resolved number of workers",
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
		lastLog:  time.Now(),
		interval: conf.Settings.Scan.ProgressLogInterval.Duration,
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

					if err := workOn(workCtx, conn, conf, item); err != nil {
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

					progress.complete()
				}
			}
		}()
	}

	// Produce work in this goroutine
	if err := func() error {
		for _, root := range conf.Roots {
			if err := workCtx.Err(); err != nil {
				return err
			}

			slog.Info("scanning library root: starting",
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
					return command.InsertLibrarySyncLog(
						workCtx, conn, "error",
						fmt.Sprintf(
							"skipped work on file: %v",
							walkErr,
						),
						root.RootPath, localFile.RelPath,
					)
				}

				if localFile.StatErr != nil {
					return command.InsertLibrarySyncLog(
						workCtx, conn, "error",
						fmt.Sprintf(
							"skipped work on file: %v",
							localFile.StatErr,
						),
						root.RootPath, localFile.RelPath,
					)
				}

				fileType, recognizable := scan.IsRecognizable(
					localFile.Name,
					conf.Settings.Scan.RecognizedSidecarArtwork,
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

			slog.Info("library root queued",
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
		if firstWork != nil &&
			ctx.Err() == nil &&
			!errors.Is(firstErr, command.ErrSyncLogWrite) {

			logErr := command.InsertLibrarySyncLog(
				ctx, conn, "error",
				fmt.Sprintf("cannot process file: %v", firstErr),
				firstWork.root.RootPath,
				firstWork.localFile.RelPath,
			)
			if logErr != nil {
				slog.Error(
					"cannot record sync failure",
					"processing_error", firstErr,
					"log_error", logErr,
				)
			}
		}

		return firstErr
	}

	// Workers can exit on parent cancellation without calling fail, therefore
	// `firstErr` can be nil. This check honors ensures the job ends early in
	// in this case with giving the calling the sense it was successful.
	if err := ctx.Err(); err != nil {
		return err
	}

	slog.Info("library scan completed",
		"processed_files", progress.count(),
	)

	return nil
}

// Called from a worker goroutine
// Implements the processing of a recognizable file; essentially
// 1. parses file metadata and normalizes its,
// 2. appends it to a temporary table to hold scanned tracks.
func workOn(
	workCtx context.Context,
	conn *sql.Conn,
	conf *app.AppConfig,
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

		if err := scan.ApplyTrackMetadata(localPath, conf, scannedFile); err != nil {
			if err := command.InsertLibrarySyncLog(
				workCtx, conn, "error", err.Error(),
				item.root.RootPath, item.localFile.RelPath,
			); err != nil {
				return err
			}
		}

		// Since this call involves a db connection its error should
		// fail the job.
		err = scan.ApplyMetadataNormalizations(workCtx, conf, conn, scannedFile)

	case app.FileTypeSidecarCover, app.FileTypeSidecarOther:

	default: // app.FileTypePlaylist
		if err := command.InsertLibrarySyncLog(
			workCtx, conn, "info",
			"skipping unsupported playlist item",
			scannedFile.RootPath, scannedFile.Path,
		); err != nil {
			return err
		}
	}

	if err == nil {
		err = command.InsertScannedFile(
			workCtx,
			conn,
			scannedFile,
			conf.Settings.Scan.LocalArtworkMaxBytes,
		)
	}

	return err
}

type syncProgress struct {
	mu        sync.Mutex
	processed int
	lastLog   time.Time
	interval  time.Duration
}

func (p *syncProgress) complete() {
	p.mu.Lock()
	defer p.mu.Unlock()

	p.processed++

	now := time.Now()
	if p.interval > 0 && now.Sub(p.lastLog) >= p.interval {
		slog.Info("library scan progress",
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
