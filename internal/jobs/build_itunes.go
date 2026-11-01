package jobs

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"github.com/zanyoats/kukicha/internal/app"
	scanartwork "github.com/zanyoats/kukicha/internal/scan/artwork"
	"github.com/zanyoats/kukicha/internal/usecase/command"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

var ErrITunesArtworkLookup = errors.New("iTunes artwork lookup")

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

func (j *fileScanner) lookupITunesArtwork(ctx context.Context, conn *sql.Conn) error {
	jobID, _ := app.JobIDFromContext(ctx) // Validated by the caller.
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
