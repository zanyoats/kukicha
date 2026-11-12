package query

import (
	"context"
	"database/sql"
	_ "embed"
	"slices"
	"time"
)

//go:embed sql/listening_years.sql
var listeningYearsSQL string

// ListeningYears finds occupied calendar years in the timezone from at.Location(). Each
// indexed seek skips directly to the next occupied year rather than reading
// every scrobble or interpreting local timestamps in SQLite.
func ListeningYears(ctx context.Context, db *sql.DB, at time.Time) ([]int, error) {
	start := time.Date(1, 1, 1, 0, 0, 0, 0, at.Location())
	years := []int{}
	for {
		var first sql.NullInt64
		if err := db.QueryRowContext(ctx, listeningYearsSQL, start.UnixMilli(), at.UnixMilli()).Scan(&first); err != nil {
			return nil, err
		}
		if !first.Valid {
			break
		}
		year := time.UnixMilli(first.Int64).In(at.Location()).Year()
		years = append(years, year)
		start = time.Date(year+1, 1, 1, 0, 0, 0, 0, at.Location())
	}
	slices.Reverse(years)
	return years, nil
}
