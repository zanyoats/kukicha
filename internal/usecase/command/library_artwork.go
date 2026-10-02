package command

import (
	"context"
	"database/sql"
	_ "embed"
)

//go:embed sql/attach_library_artwork.sql
var attachLibraryArtworkSQL string

// AttachLibraryArtwork attaches both iTunes thumbnail sizes, preserving local artwork.
func AttachLibraryArtwork(ctx context.Context, conn *sql.Conn, trackID int64, name string) error {
	_, err := conn.ExecContext(ctx, attachLibraryArtworkSQL, sql.Named("track_id", trackID), sql.Named("name", name))
	return err
}
