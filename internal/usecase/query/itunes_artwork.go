package query

import (
	"context"
	"database/sql"
	_ "embed"
	"fmt"

	"github.com/zanyoats/kukicha/internal/app"
)

//go:embed sql/library_artwork_candidates.sql
var libraryArtworkCandidatesSQL string

type LibraryArtworkCandidate struct {
	TrackID        int64
	RootPath, Path string
	Kind           app.ITunesLookupKind
	LookupID       string
}

// LibraryArtworkCandidates loads candidates for up to limit tracks after afterID.
// Both reads share a transaction, which ends before the caller starts network
// work. It returns the last selected track ID, even if no candidates were found.
// A zero ID ends iteration.
func LibraryArtworkCandidates(
	ctx context.Context,
	conn *sql.Conn,
	afterID int64,
	limit int,
) ([]LibraryArtworkCandidate, int64, error) {
	if limit < 1 {
		return nil, 0, fmt.Errorf("invalid artwork candidate batch size: %d", limit)
	}
	tx, err := conn.BeginTx(ctx, nil)
	if err != nil {
		return nil, 0, err
	}
	defer tx.Rollback()

	var lastID sql.NullInt64
	if err := tx.QueryRowContext(ctx, `
		SELECT MAX(id) FROM (
			SELECT id FROM library_tracks WHERE id > ? ORDER BY id LIMIT ?
		)`, afterID, limit).Scan(&lastID); err != nil {
		return nil, 0, err
	}
	if !lastID.Valid {
		return nil, 0, tx.Commit()
	}

	rows, err := tx.QueryContext(ctx, libraryArtworkCandidatesSQL,
		sql.Named("after_id", afterID), sql.Named("last_id", lastID.Int64),
	)
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()
	var candidates []LibraryArtworkCandidate
	for rows.Next() {
		var c LibraryArtworkCandidate
		if err := rows.Scan(&c.TrackID, &c.RootPath, &c.Path, &c.Kind, &c.LookupID); err != nil {
			return nil, 0, err
		}
		candidates = append(candidates, c)
	}
	if err := rows.Err(); err != nil {
		return nil, 0, err
	}
	if err := rows.Close(); err != nil {
		return nil, 0, err
	}
	if err := tx.Commit(); err != nil {
		return nil, 0, err
	}
	return candidates, lastID.Int64, nil
}
