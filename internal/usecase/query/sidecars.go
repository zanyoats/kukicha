package query

import (
	"context"
	"database/sql"
)

type AlbumSidecar struct {
	ID           int64
	RelativePath string
	Extension    string
}

// Only expose sidecars whose root and album are currently available.
const availableSidecarsSQL = `
 FROM library_album_sidecars AS sidecars
 JOIN library_roots AS roots ON roots.id = sidecars.root_id
 JOIN available_library_albums AS albums ON albums.id = sidecars.album_id
 WHERE roots.state = 'available'
`

func AlbumHasSidecars(ctx context.Context, db *sql.DB, albumID int64) (bool, error) {
	var found bool
	err := db.QueryRowContext(ctx, "SELECT EXISTS (SELECT 1"+availableSidecarsSQL+" AND sidecars.album_id = ?)", albumID).Scan(&found)
	return found, err
}

func AlbumSidecars(ctx context.Context, db *sql.DB, albumID int64) ([]AlbumSidecar, error) {
	rows, err := db.QueryContext(ctx, "SELECT sidecars.id, sidecars.path, sidecars.ext"+availableSidecarsSQL+" AND sidecars.album_id = ? ORDER BY sidecars.path, sidecars.id", albumID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var result []AlbumSidecar
	for rows.Next() {
		var item AlbumSidecar
		if err := rows.Scan(&item.ID, &item.RelativePath, &item.Extension); err != nil {
			return nil, err
		}
		result = append(result, item)
	}
	return result, rows.Err()
}

func SidecarFileByID(ctx context.Context, db *sql.DB, id int64) (TrackAudio, error) {
	var file TrackAudio
	err := db.QueryRowContext(ctx, "SELECT roots.config_id, sidecars.path, sidecars.ext"+availableSidecarsSQL+" AND sidecars.id = ?", id).Scan(&file.ConfigID, &file.RelativePath, &file.Extension)
	return file, err
}
