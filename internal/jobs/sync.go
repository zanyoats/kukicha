package jobs

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"runtime/debug"
	"strconv"
	"sync"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"
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
		// A recovered job must rebuild if configuration changed after its commit.
		matches, err := query.RootsMatchConfig(ctx, j.DB, j.Conf.Roots)
		if err != nil {
			return err
		}
		if matches {
			return nil
		}
	}

	if err := command.PrepareScanResults(
		ctx,
		conn,
		j.Conf.Settings.Server.Database.CacheSizeKiB,
	); err != nil {
		return err
	}

	if err := j.fileScanWithWorkers(ctx, conn); err != nil {
		return err
	}

	result, err := command.BuildLibraryFromScan(
		ctx, conn, command.BuildFromFiles,
		scan.UnknownItem,
		j.Conf.Settings.Scan.SecondaryGenreSplitTokens,
		scan.RecognizedAudioExtensions,
		scan.RecognizedPlaylistExtensions,
		scan.RecognizedSidecarExtensions,
	)
	if err != nil {
		return err
	}

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

// The unit work is a local or remote file.
type work struct {
	source   scanSource
	rootPos  int
	file     logicalFile
	fileType app.FileType
}

// A file has logical and physical parts. A remote file has a local file path
// after it has been downloaded.
type logicalFile struct {
	Name, RelPath, Ext, Stem string
	FileSize                 int64
	FileModTime              time.Time
}

type physicalFile struct{ path string }

// scanSource helps working on remote and local file sources.
type scanSource interface {
	Root() app.Root
	Prepare(context.Context, logicalFile) (preparedFile, error)
}

type preparedFile struct {
	Path    string
	Release func() error
}

type libraryItem struct {
	root    app.Root
	rootPos int
	logicalFile
	physicalFile
	fileType app.FileType
}

func (j *Sync) fileScanWithWorkers(
	ctx context.Context,
	conn *sql.Conn,
) error {
	var remoteSources []*remoteSource
	defer func() {
		for _, source := range remoteSources {
			if err := os.RemoveAll(source.tempDir); err != nil {
				j.logger.WarnContext(ctx, "cannot remove remote temporary directory", "error", err)
			}
		}
	}()
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
	// On a producer panic, stop and join workers before removing temporary
	// files or letting Run release the database connection.
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

	jobID, _ := app.JobIDFromContext(ctx) // Validated by Run.
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
					if err := j.workOn(workCtx, conn, item); err != nil {
						fail(
							fmt.Errorf(
								"work on %q: %w",
								item.source.Root().ConfigID()+":"+item.file.RelPath,
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

	// Produce work in this goroutine
	if err := func() error {
		for rootPos, value := range j.Conf.Roots {
			j.logger.InfoContext(ctx, "scanning library root: starting",
				"root", value.ConfigID(),
			)

			if err := command.InsertScanRoot(workCtx, conn, rootPos, value); err != nil {
				return err
			}

			switch root := value.(type) {
			case *app.RemoteRoot:
				source, err := newRemoteSource(workCtx, root)
				if err != nil {
					return err
				}
				remoteSources = append(remoteSources, source)
				if err := walk.Bucket(workCtx, source.client, root.Bucket, root.Prefix, func(
					remoteFile walk.RemoteWalkedFile,
					walkErr error,
				) error {
					if err := workCtx.Err(); err != nil {
						return err
					}
					if walkErr != nil {
						return walkErr
					}

					fileType, recognizable := scan.IsRecognizable(
						remoteFile.Name,
						j.Conf.Settings.Scan.RecognizedSidecarArtwork,
					)
					if !recognizable {
						return nil
					}

					item := work{
						source: source, rootPos: rootPos,
						file: logicalFile{
							Name: remoteFile.Name, RelPath: remoteFile.RelPath, Ext: remoteFile.Ext, Stem: remoteFile.Stem,
							FileSize: remoteFile.FileSize, FileModTime: remoteFile.FileModTime,
						},
						fileType: fileType,
					}
					select {
					case queue <- item:
						return nil
					case <-workCtx.Done():
						return workCtx.Err()
					}
				}); err != nil {
					return fmt.Errorf("list remote root %q: %w", root.ID, err)
				}

			case *app.LocalRoot:
				if err := workCtx.Err(); err != nil {
					return err
				}

				source := &localSource{root: root}
				if err := walk.Dir(root.Path, func(
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
						), "root", root.ConfigID(), "path", localFile.RelPath)
						return nil
					}

					if localFile.StatErr != nil {
						j.logger.ErrorContext(workCtx, fmt.Sprintf(
							"skipped work on file: %v",
							localFile.StatErr,
						), "root", root.ConfigID(), "path", localFile.RelPath)
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
						source: source, rootPos: rootPos,
						file: logicalFile{
							Name: localFile.Name, RelPath: localFile.RelPath, Ext: localFile.Ext, Stem: localFile.Stem,
							FileSize: localFile.FileSize, FileModTime: localFile.FileModTime,
						},
						fileType: fileType,
					}

					select {
					case queue <- item:
						return nil
					case <-workCtx.Done():
						return workCtx.Err()
					}
				}); err != nil {
					return fmt.Errorf(
						"walk root %q: %w", root.Path, err,
					)
				}

			}

			j.logger.InfoContext(ctx, "library root queued",
				"root", value.ConfigID(),
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
				"root", firstWork.source.Root().ConfigID(), "path", firstWork.file.RelPath)
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

func (j *Sync) workOn(ctx context.Context, conn *sql.Conn, input work) (err error) {
	prepared, err := input.source.Prepare(ctx, input.file)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, prepared.Release()) }()
	item := libraryItem{
		root: input.source.Root(), rootPos: input.rootPos,
		logicalFile: input.file, physicalFile: physicalFile{path: prepared.Path},
		fileType: input.fileType,
	}
	return j.processLibraryItem(ctx, conn, item)
}

// `localSource` implements `scanSource`
type localSource struct {
	root *app.LocalRoot
}

func (s *localSource) Root() app.Root { return s.root }

func (s *localSource) Prepare(ctx context.Context, file logicalFile) (preparedFile, error) {
	if err := ctx.Err(); err != nil {
		return preparedFile{}, err
	}
	return preparedFile{
		Path:    filepath.Join(s.root.Path, file.RelPath),
		Release: func() error { return nil },
	}, nil
}

// `remoteSource` implements `scanSource`
type remoteSource struct {
	root    *app.RemoteRoot
	client  *s3.Client
	tempDir string
}

func newRemoteSource(ctx context.Context, root *app.RemoteRoot) (*remoteSource, error) {
	client, err := walk.NewS3Client(ctx, root)
	if err != nil {
		return nil, fmt.Errorf("configure remote root %q: %w", root.ID, err)
	}
	dir, err := os.MkdirTemp("", "kukicha-remote-*")
	if err != nil {
		return nil, err
	}
	return &remoteSource{root: root, client: client, tempDir: dir}, nil
}

func (s *remoteSource) Root() app.Root { return s.root }

func (s *remoteSource) Prepare(ctx context.Context, file logicalFile) (_ preparedFile, err error) {
	if err := ctx.Err(); err != nil {
		return preparedFile{}, err
	}
	download, err := os.CreateTemp(s.tempDir, "object-*"+file.Ext)
	if err != nil {
		return preparedFile{}, err
	}
	release := func() error { return os.Remove(download.Name()) }
	// Failed preparation retains ownership of the partial download.
	defer func() {
		if err != nil {
			err = errors.Join(err, release())
		}
	}()
	response, err := s.client.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(s.root.Bucket),
		Key:    aws.String(s.root.Prefix + file.RelPath),
	})
	if err != nil {
		return preparedFile{}, errors.Join(err, download.Close())
	}
	_, copyErr := io.Copy(download, response.Body)
	if err := errors.Join(copyErr, response.Body.Close(), download.Close()); err != nil {
		return preparedFile{}, err
	}
	return preparedFile{Path: download.Name(), Release: release}, nil
}

// Called from a worker goroutine
// Implements the processing of a recognizable file; essentially
// 1. parses file metadata and normalizes its,
// 2. appends it to a temporary table to hold scanned tracks.
func (j *Sync) processLibraryItem(
	workCtx context.Context,
	conn *sql.Conn,
	item libraryItem,
) (err error) {
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
		NormalizedMetadata: &app.NormalizedTrackMetadata{},
	}

	switch item.fileType {
	case app.FileTypeAudio:
		if err := scan.ApplyTrackMetadata(item.physicalFile.path, j.Conf, scannedFile); err != nil {
			j.logger.ErrorContext(workCtx, err.Error(), "root", item.root.ConfigID(), "path", item.logicalFile.RelPath)
		}

		// Since this call involves a db connection its error should
		// fail the job.
		err = scan.ApplyMetadataNormalizations(workCtx, j.Conf, conn, scannedFile)

	case app.FileTypeSidecarCover, app.FileTypeSidecarOther:

	default: // app.FileTypePlaylist
		j.logger.InfoContext(workCtx, "skipping unsupported playlist item", "root", scannedFile.Root.ConfigID(), "path", scannedFile.Path)
	}

	if err == nil {
		err = command.InsertScannedFile(
			workCtx,
			conn,
			scannedFile,
			j.Conf.Settings.Scan.LocalArtworkMaxBytes,
			item.physicalFile.path,
		)
	}

	return err
}

type syncProgress struct {
	logger    *slog.Logger
	message   string
	countKey  string
	mu        sync.Mutex
	processed int
	lastLog   time.Time
	interval  time.Duration
	notify    func(int)
}

func (p *syncProgress) complete(ctx context.Context) {
	p.mu.Lock()
	defer p.mu.Unlock()

	p.processed++

	now := time.Now()
	if p.interval > 0 && now.Sub(p.lastLog) >= p.interval {
		p.logger.InfoContext(ctx, p.message,
			p.countKey, p.processed,
		)
		if p.notify != nil {
			p.notify(p.processed)
		}
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

func (j *Sync) lookupITunesArtwork(ctx context.Context, conn *sql.Conn) error {
	jobID, _ := app.JobIDFromContext(ctx) // Validated by Run.
	progress := &syncProgress{
		logger:   j.logger,
		message:  "iTunes artwork lookup progress",
		countKey: "processed_candidates",
		lastLog:  time.Now(),
		interval: j.Conf.Settings.Scan.ProgressLogInterval.Duration,
		notify: func(processed int) {
			j.notify(jobID, fmt.Sprintf("Processed %d iTunes artwork candidates.", processed))
		},
	}
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
			j.logger.InfoContext(ctx, "iTunes artwork lookup completed",
				"processed_candidates", progress.count(),
			)
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
				), "root", candidate.ConfigID, "path", candidate.Path)
				progress.complete(ctx)
				continue
			}

			if result.ResultKind != app.ITunesResultHit {
				progress.complete(ctx)
				continue
			}

			// Cache into artwork thumbnails table, then repair itunes cache
			// with hashed `name` value
			name, err := command.CacheArtworkThumbnails(
				ctx, conn, result.Bytes,
				fmt.Sprintf("iTunes %s artwork %s", candidate.Kind, candidate.LookupID),
				candidate.ConfigID, candidate.Path,
			)
			if err != nil {
				return err
			}
			if name == "" {
				progress.complete(ctx)
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
			progress.complete(ctx)
		}

		afterID = lastID
	}
}
