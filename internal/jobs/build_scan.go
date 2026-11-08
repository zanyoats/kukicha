package jobs

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"log/slog"
	"path/filepath"
	"runtime/debug"
	"sync"
	"time"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/scan"
	"github.com/zanyoats/kukicha/internal/scan/duration"
	"github.com/zanyoats/kukicha/internal/upload"
	"github.com/zanyoats/kukicha/internal/usecase/command"
	"github.com/zanyoats/kukicha/internal/walk"
)

type fileScanner struct {
	Conf          *app.AppConfig
	Notifications NotificationPublisher
	logger        *slog.Logger
}

// NotificationPublisher lets jobs announce milestones without transport dependencies.
type NotificationPublisher interface {
	Publish(app.Notification)
}

func (j *fileScanner) notify(jobID int64, message string) {
	if j.Notifications != nil {
		j.Notifications.Publish(app.Notification{JobID: jobID, Message: message})
	}
}

// The unit work is a local or remote file.
type work struct {
	source  scanSource
	rootPos int
	file    logicalFile
}

// A file has logical and physical parts. A remote file has a local file path
// after it has been downloaded.
type logicalFile struct {
	Name, RelPath, Ext, Stem string
	FileSize                 int64
	FileModTime              time.Time
}

type physicalFile struct{ path string }

type libraryItem struct {
	root    app.Root
	rootPos int
	logicalFile
	physicalFile
	fileType app.FileType
}

func (j *fileScanner) fileScanWithWorkers(
	ctx context.Context,
	conn *sql.Conn,
	sources []scanSource,
	destination app.Root,
	rootPos int,
	pathPrefix string,
	transfer func(context.Context, string, string) error,
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
	// On a producer panic, stop and join workers before returning control
	// to the caller that owns the sources and database connection.
	defer func() {
		cancel()
		pool.Wait()
	}()
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

	jobID, _ := app.JobIDFromContext(ctx) // Validated by the caller.
	progress := &syncProgress{
		logger:   j.logger,
		message:  "library scan progress",
		countKey: "processed_files",
		lastLog:  time.Now(),
		interval: j.Conf.Settings.Scan.ProgressLogInterval.Duration,
		notify: func(processed int) {
			j.notify(jobID, fmt.Sprintf("Processed %d files.", processed))
		},
	}

	// Start the workers/consumers
	for range workers {
		pool.Add(1)

		go func() {
			defer pool.Done()
			var activeWork *work
			defer func() {
				if r := recover(); r != nil {
					fail(fmt.Errorf("scan worker panic: %v\n%s", r, debug.Stack()), activeWork)
				}
			}()

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

					activeWork = &item
					if err := j.workOn(workCtx, conn, item, destination, pathPrefix, transfer); err != nil {
						fail(
							fmt.Errorf(
								"work on %q: %w",
								item.source.Name()+":"+item.file.RelPath,
								err,
							),
							&item,
						)
						return
					}

					progress.complete(workCtx)
					activeWork = nil
				}
			}
		}()
	}

	ignore := func(path string) bool {
		return app.IsIgnoredScanPath(path, j.Conf.Settings.Scan.IgnorePrefixes, j.Conf.Settings.Scan.IgnoreFiles)
	}

	// Produce work in this goroutine
	if err := func() error {
		for _, source := range sources {
			j.logger.InfoContext(ctx, "scanning source: starting",
				"source", source.Name(),
			)

			switch source := source.(type) {
			case *remoteSource:
				location := source.source
				if err := walk.Bucket(workCtx, source.client, location.Bucket, location.Prefix, func(
					remoteFile walk.RemoteWalkedFile,
					walkErr error,
				) error {
					if err := workCtx.Err(); err != nil {
						return err
					}
					if walkErr != nil {
						return walkErr
					}

					if ignore(location.Prefix + remoteFile.RelPath) {
						return nil
					}

					item := work{
						source: source, rootPos: rootPos,
						file: logicalFile{
							Name: remoteFile.Name, RelPath: remoteFile.RelPath, Ext: remoteFile.Ext, Stem: remoteFile.Stem,
							FileSize: remoteFile.FileSize, FileModTime: remoteFile.FileModTime,
						},
					}
					select {
					case queue <- item:
						return nil
					case <-workCtx.Done():
						return workCtx.Err()
					}
				}); err != nil {
					return fmt.Errorf("list remote source %q: %w", source.Name(), err)
				}

			case *localSource:
				if err := workCtx.Err(); err != nil {
					return err
				}

				if err := walk.Dir(source.path, func(
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
						), "source", source.Name(), "path", localFile.RelPath)
						return nil
					}

					if ignore(localFile.Name) {
						return nil
					}

					if localFile.StatErr != nil {
						j.logger.ErrorContext(workCtx, fmt.Sprintf(
							"skipped work on file: %v",
							localFile.StatErr,
						), "source", source.Name(), "path", localFile.RelPath)
						return nil
					}

					item := work{
						source: source, rootPos: rootPos,
						file: logicalFile{
							Name: localFile.Name, RelPath: localFile.RelPath, Ext: localFile.Ext, Stem: localFile.Stem,
							FileSize: localFile.FileSize, FileModTime: localFile.FileModTime,
						},
					}

					select {
					case queue <- item:
						return nil
					case <-workCtx.Done():
						return workCtx.Err()
					}
				}); err != nil {
					return fmt.Errorf(
						"walk source %q: %w", source.Name(), err,
					)
				}

			}

			j.logger.InfoContext(ctx, "source queued",
				"source", source.Name(),
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
				"source", firstWork.source.Name(), "path", firstWork.file.RelPath)
		}

		return firstErr
	}

	// Workers can exit on parent cancellation without calling fail. Do not
	// report success merely because firstErr is nil.
	if err := ctx.Err(); err != nil {
		return err
	}

	j.logger.InfoContext(ctx, "library scan completed",
		"processed_files", progress.count(),
	)

	return nil
}

func (j *fileScanner) workOn(ctx context.Context, conn *sql.Conn, input work, destination app.Root, pathPrefix string, transfer func(context.Context, string, string) error) (err error) {
	if err := upload.ValidateRelativePath(filepath.ToSlash(input.file.RelPath)); err != nil {
		return err
	}
	prepared, err := input.source.Prepare(ctx, input.file)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, prepared.Release()) }()
	fileType, recognizable := scan.IsRecognizable(input.file.Name, j.Conf.Settings.Scan.RecognizedSidecarArtwork)
	item := libraryItem{
		root: destination, rootPos: input.rootPos,
		logicalFile: input.file, physicalFile: physicalFile{path: prepared.Path},
		fileType: fileType,
	}
	item.RelPath = pathPrefix + filepath.ToSlash(item.RelPath)
	if recognizable {
		if err := j.processLibraryItem(ctx, conn, item); err != nil {
			return err
		}
	}
	return transfer(ctx, prepared.Path, item.RelPath)
}

// Called from a worker goroutine
// Implements the processing of a recognizable file; essentially
// 1. parses original file metadata,
// 2. appends it to a temporary table to hold scanned tracks.
func (j *fileScanner) processLibraryItem(
	workCtx context.Context,
	conn *sql.Conn,
	item libraryItem,
) error {
	fileIdentity := app.FileIdentity{
		Root: item.root,
		Path: item.logicalFile.RelPath,
		Ext:  item.logicalFile.Ext,
		Stem: item.logicalFile.Stem,
	}
	fileSnapshot := app.FileSnapshot{
		FileType:    item.fileType,
		FileSize:    item.logicalFile.FileSize,
		FileModTime: item.logicalFile.FileModTime,
	}
	scannedFile := &app.ScannedFile{
		RootPos:      item.rootPos,
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
	}

	if item.fileType == app.FileTypeAudio {
		if err := scan.ApplyTrackMetadata(item.physicalFile.path, j.Conf, scannedFile); err != nil {
			if errors.Is(err, duration.ErrIncompleteFinalMP3Frame) {
				j.logger.WarnContext(workCtx, fmt.Sprintf(
					"Track import will continue. Recovered duration %d:%02d from complete audio frames; ignored an incomplete final MP3 frame. Details: %v",
					scannedFile.DurationSeconds/60, scannedFile.DurationSeconds%60, err,
				), "root", item.root.ConfigID(), "path", item.logicalFile.RelPath)
			} else {
				j.logger.ErrorContext(workCtx, err.Error(), "root", item.root.ConfigID(), "path", item.logicalFile.RelPath)
			}
		}
	}

	return command.InsertScannedFile(
		workCtx,
		conn,
		scannedFile,
		j.Conf.Settings.Scan.LocalArtworkMaxBytes,
		item.physicalFile.path,
	)
}
