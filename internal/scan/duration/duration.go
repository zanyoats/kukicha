package duration

import (
	"fmt"
	"math"
)

// truncateDurationSeconds converts a duration expressed as integer time units
// into whole seconds. Library and player duration displays intentionally floor
// fractional seconds rather than rounding them up.
func truncateDurationSeconds(units uint64, unitsPerSecond uint64) (int64, error) {
	if unitsPerSecond == 0 {
		return 0, fmt.Errorf("duration time scale must be positive")
	}

	seconds := units / unitsPerSecond
	if seconds > math.MaxInt64 {
		return 0, fmt.Errorf("duration exceeds supported range")
	}

	return int64(seconds), nil
}
