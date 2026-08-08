package identicon

import (
	"crypto/sha256"
	"fmt"
	"strings"
)

// SVG function relies on 7x7 identicon grid
const gridSize = 7

// SVG returns an identicon deterministically derived from identifier.
// It constructs the first vertical half, the middle, and a mirror of the first
// half to form a pattern emanating from the center.
func SVG(identifier string) string {
	digest := sha256.Sum256([]byte(identifier))

	var cells strings.Builder

	// Constructs a 7x7 grid
	// Use the first 28 bits (bytes 0-3) to decide whether to draw a square
	// at its cell coordinates for the first 4 columns.
	// The last 3 columns are mirrored version of the first 3.
	for row := range gridSize {
		for column := 0; column <= gridSize/2; column++ {
			bitIndex := row*(gridSize/2+1) + column
			active := isBitActive(digest, bitIndex)

			if row == gridSize/2 && column == gridSize/2 {
				active = true
			}

			if !active {
				continue
			}

			writeCell(&cells, column+1, row+1)

			mirroredColumn := gridSize - 1 - column
			if mirroredColumn != column {
				writeCell(&cells, mirroredColumn+1, row+1)
			}
		}
	}

	firstHue := (int(digest[4])<<8 | int(digest[5])) % 360
	secondHue := (firstHue + 45 + int(digest[6])%91) % 360

	return fmt.Sprintf(
		`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 9 9" role="img" aria-label="Instance identicon">
  <defs>
    <linearGradient id="gradient" x1="1" y1="1" x2="8" y2="8" gradientUnits="userSpaceOnUse">
      <stop stop-color="hsl(%d 68%% 48%%)"/>
      <stop offset="1" stop-color="hsl(%d 68%% 38%%)"/>
    </linearGradient>
  </defs>
  <rect width="9" height="9" fill="hsl(%d 35%% 92%%)"/>
  <path fill="url(#gradient)" d="%s"/>
</svg>`,
		firstHue,
		secondHue,
		firstHue,
		cells.String(),
	)
}

func isBitActive(digest [sha256.Size]byte, index int) bool {
	byteIndex := index / 8
	bitIndex := uint(index % 8)

	return digest[byteIndex]&(1<<bitIndex) != 0
}

func writeCell(
	svg *strings.Builder,
	column int,
	row int,
) {
	fmt.Fprintf(
		svg,
		`M%d %dh1v1H%dz`,
		column,
		row,
		column,
	)
}
