package query

import (
	"context"
	"database/sql"
	_ "embed"

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

// LibraryArtworkCandidates closes its rows before the caller starts network work.
func LibraryArtworkCandidates(ctx context.Context, conn *sql.Conn) ([]LibraryArtworkCandidate, error) {
	rows, err := conn.QueryContext(ctx, libraryArtworkCandidatesSQL)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var candidates []LibraryArtworkCandidate
	for rows.Next() {
		var c LibraryArtworkCandidate
		if err := rows.Scan(&c.TrackID, &c.RootPath, &c.Path, &c.Kind, &c.LookupID); err != nil {
			return nil, err
		}
		candidates = append(candidates, c)
	}
	return candidates, rows.Err()
}
