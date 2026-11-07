package query

import (
	"context"
	"database/sql"
	_ "embed"
	"time"
)

//go:embed sql/listening_genres.sql
var listeningGenresSQL string

type ListeningGenre struct {
	ID     int64      `json:"id"`
	Name   string     `json:"name"`
	Counts [12]*int64 `json:"counts"`
}

// ListeningGenresAt counts each play once per stored track genre, including
// unknown fallback. Plays without a remaining genre assignment are excluded.
// Month boundaries use the same server timezone as the other listening charts.
func ListeningGenresAt(ctx context.Context, db *sql.DB, year int, at time.Time) ([]ListeningGenre, error) {
	at = at.In(time.Local)
	start := time.Date(year, 1, 1, 0, 0, 0, 0, at.Location())
	rows, err := db.QueryContext(ctx, listeningGenresSQL, start.UnixMilli(), at.UnixMilli())
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	genres := make([]ListeningGenre, 0)
	for rows.Next() {
		var playedAt int64
		var id int64
		var name string
		if err := rows.Scan(&playedAt, &id, &name); err != nil {
			return nil, err
		}
		if len(genres) == 0 || genres[len(genres)-1].ID != id {
			genre := ListeningGenre{ID: id, Name: name}
			for month := 0; month < listeningMonthCount(year, at); month++ {
				genre.Counts[month] = new(int64)
			}
			genres = append(genres, genre)
		}
		month := int(time.UnixMilli(playedAt).In(at.Location()).Month()) - 1
		*genres[len(genres)-1].Counts[month]++
	}
	return genres, rows.Err()
}
