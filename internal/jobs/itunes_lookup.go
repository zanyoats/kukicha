package jobs

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"log/slog"

	"github.com/zanyoats/kukicha/internal/app"
	scanartwork "github.com/zanyoats/kukicha/internal/scan/artwork"
	"github.com/zanyoats/kukicha/internal/usecase/command"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

var ErrITunesArtworkLookup = errors.New("iTunes artwork lookup")

// Attempts to fetch artwork for tracks with iTunes's store ids. A lot of
// iTunes tracks don't embed the artwork in the file metadata. This job attempts
// to get more artwork coverage.
type ItunesLookup struct {
	DB     *sql.DB
	Conf   *app.AppConfig
	logger *slog.Logger
}

func (j *ItunesLookup) Run(ctx context.Context) error {
	j.logger = app.JobLogger(ctx)
	if !j.Conf.Settings.Scan.ITunes.Enabled {
		j.logger.WarnContext(ctx, "iTunes lookup is not enabled")
		return nil
	}

	client := scanartwork.NewITunesClient(
		nil,
		scanartwork.ITunesClientConfig{
			RequestTimeout: j.Conf.Settings.Scan.ITunes.RequestTimeout.Duration,
			LookupInterval: j.Conf.Settings.Scan.ITunes.LookupInterval.Duration,
			RetryAttempts:  j.Conf.Settings.Scan.ITunes.RetryAttempts,
		},
	)

	// TODO: Batch by track IDs since candidates can be a large list
	// 1. Load up to 100 track IDs after the cursor.
	// 2. Load candidates for that selected ID range.
	// 3. Close rows and process candidates.
	// 4. Advance to the last ID from step 1.

	candidates, err := query.LibraryArtworkCandidates(ctx, j.DB)
	if err != nil {
		return err
	}

	resolved := make(map[int64]bool)
	for _, candidate := range candidates {
		if err := ctx.Err(); err != nil {
			return err
		}
		if resolved[candidate.TrackID] {
			continue
		}
		result, _, err := LookupCachedArtwork(
			ctx, j.DB, client, candidate.Kind, candidate.LookupID,
		)
		if err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			if !errors.Is(err, ErrITunesArtworkLookup) {
				return err
			}
			j.logger.WarnContext(ctx, fmt.Sprintf(
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
			ctx, j.DB, result.Bytes,
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
			ctx, j.DB, result.LookupKind, result.LookupID, name,
		); err != nil {
			return err
		}

		resolved[candidate.TrackID] = true
	}
	// Apply the same final selection that the next Sync will use.
	tx, err := j.DB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	if err := command.ReconcileITunesArtwork(ctx, tx); err != nil {
		return err
	}
	return tx.Commit()
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

// LookupCachedArtwork returns the result and whether it came from the cache.
func LookupCachedArtwork(
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
