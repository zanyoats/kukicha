package web

import "fmt"

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
