package textkey

import (
	"regexp"
	"strings"
	"unicode"

	"golang.org/x/text/unicode/norm"
)

func NormDisplay(s string) string {
	return strings.TrimSpace(s)
}

// NOTE: combining marks stripped globally.
func NormKey(s string) string {
	var b strings.Builder
	b.Grow(len(s))

	for _, r := range norm.NFD.String(s) {
		switch {
		case r == '&':
			b.WriteString(" and ")

		case isQuote(r):
			// skip apostrophes and quotation marks

		case unicode.Is(unicode.Mn, r):
			// skip accents/diacritics

		case r == '_':
			b.WriteRune(r)

		case unicode.IsLetter(r) || unicode.IsDigit(r):
			b.WriteRune(unicode.ToLower(r))

		default:
			b.WriteRune(' ')
		}
	}

	return b.String()
}

func Slugify(s string) string {
	norm := NormKey(s)

	chunks := strings.Fields(norm)

	return strings.Join(chunks, "-")
}

func isQuote(r rune) bool {
	switch r {
	case '\'', '"', '`', '’', '‘', 'ʼ', '“', '”', '„', '‟', '«', '»', '‹', '›':
		return true
	default:
		return false
	}
}

func FirstNonZeroValue[T comparable](values ...T) T {
	var zero T
	for _, value := range values {
		if value != zero {
			return value
		}
	}
	return zero
}

func FirstNonEmptySlice[T any](values ...[]T) []T {
	for _, value := range values {
		if len(value) > 0 {
			return value
		}
	}
	return nil
}

func IsWordToken(s string) bool {
	if s == "" {
		return false
	}

	for _, r := range s {
		if !unicode.IsLetter(r) {
			return false
		}
	}

	return true
}

func MatchesWordBoundary(key string, token string) (bool, error) {
	if matches, err := regexp.MatchString(
		`(?i)(^|[^\pL\pN])`+regexp.QuoteMeta(token)+`($|[^\pL\pN])`,
		key,
	); err != nil {
		return false, err
	} else if matches {
		return true, nil
	} else {
		return false, nil
	}
}

func Expands(key string, expands map[string]string) (string, error) {
	for shorthand, expanded := range expands {
		if matches, err := MatchesWordBoundary(key, shorthand); err != nil {
			return "", err
		} else if matches {
			key = strings.ReplaceAll(key, shorthand, expanded)
		}
	}
	return key, nil
}
