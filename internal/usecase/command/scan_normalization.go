package command

import (
	_ "embed"
	"strings"
)

// The Unicode White_Space set used by Go's unicode.IsSpace/strings.TrimSpace.
// SQLite trim() treats this parameter as a set of characters, not a substring.
const normalizationWhitespace = "\u0009\u000a\u000b\u000c\u000d\u0020\u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"

//go:embed sql/normalize_scan_tracks.sql
var normalizeScanTracksSQL string

//go:embed sql/split_genre_values.sql
var splitGenreValuesSQL string

// Both normalization stages use the same literal, ordered delimiter semantics.
func withGenreSplitter(statement string) string {
	return strings.ReplaceAll(statement, "/* split_genre_values */", splitGenreValuesSQL)
}
