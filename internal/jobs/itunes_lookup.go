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

type ItunesLookup struct {
	DB   *sql.DB
	Conf *app.AppConfig
}

func (j *ItunesLookup) Run(ctx context.Context) error {
	if !j.Conf.Settings.Scan.ITunes.Enabled {
		slog.Warn("iTunes lookup is not enabled")
		return nil
	}

	conn, err := j.DB.Conn(ctx)
	if err != nil {
		return err
	}
	defer conn.Close()

	client := scanartwork.NewITunesClient(
		nil,
		scanartwork.ITunesClientConfig{
			RequestTimeout: j.Conf.Settings.Scan.ITunes.RequestTimeout.Duration,
			LookupInterval: j.Conf.Settings.Scan.ITunes.LookupInterval.Duration,
			RetryAttempts:  j.Conf.Settings.Scan.ITunes.RetryAttempts,
		},
	)

	candidates, err := query.LibraryArtworkCandidates(ctx, conn)
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
			ctx, conn, client, candidate.Kind, candidate.LookupID,
		)
		if err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			if !errors.Is(err, ErrITunesArtworkLookup) {
				return err
			}
			if logErr := command.InsertLibrarySyncLog(
				ctx, conn, "warn",
				fmt.Sprintf(
					"iTunes artwork lookup failed: kind %s, lookup ID %s: %v",
					candidate.Kind, candidate.LookupID, err,
				),
				candidate.RootPath, candidate.Path,
			); logErr != nil {
				return fmt.Errorf("%v: %w", err, logErr)
			}
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

		if err := command.AttachLibraryArtwork(
			ctx, conn, candidate.TrackID, name,
		); err != nil {
			return err
		}

		resolved[candidate.TrackID] = true
	}
	return ctx.Err()
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
