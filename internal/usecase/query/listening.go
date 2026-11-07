package query

import (
	"context"
	"database/sql"
	_ "embed"
	"encoding/json"
	"time"
)

//go:embed sql/listening_summary.sql
var listeningSummarySQL string

//go:embed sql/listening_months.sql
var listeningMonthsSQL string

type ListeningSummary struct {
	Count, Seconds, PreviousCount, PreviousSeconds int64
	Days, PreviousDays                             int
}

// ListeningSummaryAt compares server-local year-to-date periods, including today's
// calendar day in the daily average. February 29 maps to February 28.
func ListeningSummaryAt(ctx context.Context, db *sql.DB, at time.Time) (ListeningSummary, error) {
	at = at.In(time.Local)
	year, month, day := at.Date()
	lastDay := time.Date(year-1, month+1, 0, 0, 0, 0, 0, at.Location()).Day()
	if day > lastDay {
		day = lastDay
	}
	previous := time.Date(year-1, month, day, at.Hour(), at.Minute(), at.Second(), at.Nanosecond(), at.Location())
	result := ListeningSummary{Days: at.YearDay(), PreviousDays: previous.YearDay()}
	err := db.QueryRowContext(ctx, listeningSummarySQL,
		sql.Named("start", time.Date(year, 1, 1, 0, 0, 0, 0, at.Location()).UnixMilli()),
		sql.Named("end", at.UnixMilli()),
		sql.Named("previous_start", time.Date(year-1, 1, 1, 0, 0, 0, 0, at.Location()).UnixMilli()),
		sql.Named("previous_end", previous.UnixMilli()),
	).Scan(&result.Count, &result.Seconds, &result.PreviousCount, &result.PreviousSeconds)
	return result, err
}

type ListeningMonths struct {
	Current  [12]*int64 `json:"current"`
	Previous [12]int64  `json:"previous"`
	Year     int        `json:"year"`
}

func ListeningMonthsAt(ctx context.Context, db *sql.DB, at time.Time) (ListeningMonths, error) {
	at = at.In(time.Local)
	result := ListeningMonths{Year: at.Year()}
	for i := 0; i < int(at.Month()); i++ {
		result.Current[i] = new(int64)
	}
	// Compute calendar boundaries in Go so SQL uses the same server timezone,
	// including historical daylight-saving offsets, as the summary.
	type period struct {
		Year  int   `json:"year"`
		Month int   `json:"month"`
		Start int64 `json:"start"`
		End   int64 `json:"end"`
	}
	periods := make([]period, 0, 24)
	for year := at.Year() - 1; year <= at.Year(); year++ {
		for month := time.January; month <= time.December; month++ {
			start := time.Date(year, month, 1, 0, 0, 0, 0, at.Location())
			if !start.Before(at) {
				continue
			}
			end := start.AddDate(0, 1, 0)
			if end.After(at) {
				end = at
			}
			periods = append(periods, period{year, int(month), start.UnixMilli(), end.UnixMilli()})
		}
	}
	boundaries, err := json.Marshal(periods)
	if err != nil {
		return result, err
	}
	rows, err := db.QueryContext(ctx, listeningMonthsSQL, string(boundaries))
	if err != nil {
		return result, err
	}
	defer rows.Close()
	for rows.Next() {
		var year, month int
		var count int64
		if err := rows.Scan(&year, &month, &count); err != nil {
			return result, err
		}
		if year == at.Year() {
			result.Current[month-1] = &count
		} else {
			result.Previous[month-1] = count
		}
	}
	return result, rows.Err()
}

//go:embed sql/listening_hours.sql
var listeningHoursSQL string

// ListeningHoursAt counts starts by server-local hour. Convert each instant in
// Go so repeated DST hours combine and skipped hours require no special case.
func ListeningHoursAt(ctx context.Context, db *sql.DB, at time.Time) ([24]int64, error) {
	var counts [24]int64
	at = at.In(time.Local)
	start := time.Date(at.Year(), 1, 1, 0, 0, 0, 0, at.Location())
	rows, err := db.QueryContext(ctx, listeningHoursSQL, start.UnixMilli(), at.UnixMilli())
	if err != nil {
		return counts, err
	}
	defer rows.Close()
	for rows.Next() {
		var playedAt int64
		if err := rows.Scan(&playedAt); err != nil {
			return counts, err
		}
		counts[time.UnixMilli(playedAt).In(at.Location()).Hour()]++
	}
	return counts, rows.Err()
}
