package web

import (
	"fmt"
	"strings"
	"time"

	"github.com/zanyoats/kukicha/internal/usecase/query"
)

func formatDate(value string) string {
	trimmed := strings.TrimSpace(value)
	for _, layout := range []string{
		time.RFC3339,
		"2006-01-02T15:04:05",
		"2006-01-02 15:04:05",
		"2006-1-2",
		"2006/1/2",
	} {
		if date, err := time.Parse(layout, trimmed); err == nil {
			return date.Format("January 2, 2006")
		}
	}
	for _, layout := range []string{"2006-1", "2006/1"} {
		if date, err := time.Parse(layout, trimmed); err == nil {
			return date.Format("January 2006")
		}
	}

	return value
}

func formatDuration(seconds int64) string {
	if seconds < 0 {
		seconds = 0
	}

	hours := seconds / 3600
	minutes := (seconds % 3600) / 60
	remainingSeconds := seconds % 60
	if hours > 0 {
		return fmt.Sprintf(
			"%d:%02d:%02d",
			hours,
			minutes,
			remainingSeconds,
		)
	}

	return fmt.Sprintf("%d:%02d", minutes, remainingSeconds)
}

func yesNo(value *bool) string {
	if value != nil && *value {
		return "Yes"
	}

	return "No"
}

func playerArtworkSize() query.AlbumArtworkSize {
	return query.AlbumArtwork64
}
