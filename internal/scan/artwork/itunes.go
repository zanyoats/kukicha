package artwork

import (
	"context"
	"database/sql"
	"errors"
	"fmt"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/usecase/command"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

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
	client *ITunesClient,
	kind app.ITunesLookupKind,
	lookupID string,
) (app.ITunesLookupResult, bool, error) {
	cached, found, err := query.LoadCachedITunesArtwork(
		ctx,
		db,
		kind,
		lookupID,
	)
	if err != nil {
		return app.ITunesLookupResult{}, false, fmt.Errorf(
			"%w: load cached result: %w",
			command.ErrITunesArtworkCache,
			err,
		)
	}
	if found {
		return cached, true, nil
	}

	if client == nil {
		return app.ITunesLookupResult{}, false, errors.New("iTunes client is nil")
	}

	result, err := client.LookupArtwork(ctx, kind, lookupID)
	if err != nil {
		return app.ITunesLookupResult{}, false, err
	}

	if err := command.StoreCachedITunesArtwork(ctx, db, result); err != nil {
		return app.ITunesLookupResult{}, false, fmt.Errorf(
			"%w: store result: %w",
			command.ErrITunesArtworkCache,
			err,
		)
	}

	return result, false, nil
}
