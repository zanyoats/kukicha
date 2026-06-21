package scan

import (
	"encoding/binary"
	"io"
	"strconv"
	"strings"
	"unicode/utf8"
)

func setStringIfEmpty(target *string, value string) {
	if *target == "" && value != "" {
		*target = value
	}
}

func cleanStringValues(values ...string) []string {
	seen := make(map[string]bool, len(values))
	result := make([]string, 0, len(values))
	for _, value := range values {
		value = cleanTagText(value)
		if value == "" {
			continue
		}
		key := strings.ToLower(value)
		if seen[key] {
			continue
		}
		seen[key] = true
		result = append(result, value)
	}
	if len(result) == 0 {
		return []string{}
	}
	return result
}

func setIntIfZero(target *int, value int) {
	if *target == 0 && value != 0 {
		*target = value
	}
}

func setInt64IfZero(target *int64, value int64) {
	if *target == 0 && value != 0 {
		*target = value
	}
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if value != "" {
			return value
		}
	}
	return ""
}

func firstNonZero(values ...int) int {
	for _, value := range values {
		if value != 0 {
			return value
		}
	}
	return 0
}

func descriptionMatches(value string, descriptions ...string) bool {
	normalized := normalizeDescriptor(value)
	for _, description := range descriptions {
		if normalized == normalizeDescriptor(description) {
			return true
		}
	}
	return false
}

func normalizeDescriptor(value string) string {
	value = strings.ToLower(strings.TrimSpace(value))
	replacer := strings.NewReplacer(" ", "", "_", "", "-", "", ".", "", "/", "")
	return replacer.Replace(value)
}

func splitFieldList(value string) []string {
	value = cleanTagText(strings.ReplaceAll(value, "\x00", ";"))
	if value == "" {
		return []string{}
	}

	parts := strings.FieldsFunc(value, func(r rune) bool {
		return r == ';'
	})
	result := make([]string, 0, len(parts))
	for _, part := range parts {
		part = cleanTagText(part)
		if part != "" && isCleanFieldListPart(part) {
			result = append(result, part)
		}
	}
	return result
}

func isCleanFieldListPart(value string) bool {
	if !utf8.ValidString(value) {
		return false
	}
	for _, r := range value {
		if r < 0x20 {
			return false
		}
	}
	return true
}

func cleanTagText(value string) string {
	return strings.TrimSpace(strings.TrimRight(value, "\x00"))
}

func parseIntText(value string) (int, bool) {
	n, ok := parseInt64Text(value)
	return int(n), ok
}

func parseInt64Text(value string) (int64, bool) {
	value = cleanTagText(value)
	if i := strings.IndexAny(value, "/;"); i >= 0 {
		value = strings.TrimSpace(value[:i])
	}
	if value == "" {
		return 0, false
	}
	n, err := strconv.ParseInt(value, 10, 64)
	if err != nil {
		return 0, false
	}
	return n, true
}

func parseSlashPair(value string) (int, int) {
	parts := strings.SplitN(cleanTagText(value), "/", 2)
	if len(parts) != 2 {
		first, _ := parseIntText(value)
		return first, 0
	}
	first, _ := parseIntText(parts[0])
	second, _ := parseIntText(parts[1])
	return first, second
}

func parseBoolText(value string) (bool, bool) {
	switch strings.ToLower(cleanTagText(value)) {
	case "1", "true", "t", "yes", "y", "on":
		return true, true
	case "0", "false", "f", "no", "n", "off":
		return false, true
	default:
		if n, ok := parseInt64Text(value); ok {
			return n != 0, true
		}
		return false, false
	}
}

func yearFromDate(value string) int {
	value = cleanTagText(value)
	for i := 0; i+4 <= len(value); i++ {
		candidate := value[i : i+4]
		if !allDigits(candidate) {
			continue
		}
		year, err := strconv.Atoi(candidate)
		if err == nil && year >= 1000 {
			return year
		}
	}
	return 0
}

func readLittleEndianUint32(r io.Reader) (uint32, error) {
	var b [4]byte
	if _, err := io.ReadFull(r, b[:]); err != nil {
		return 0, err
	}
	return binary.LittleEndian.Uint32(b[:]), nil
}

func bigEndianUint(b []byte) uint64 {
	var n uint64
	for _, x := range b {
		n = n<<8 | uint64(x)
	}
	return n
}

func looksLikeText(b []byte) bool {
	if len(b) == 0 || !utf8.Valid(b) {
		return false
	}
	for _, r := range string(b) {
		if r == '\n' || r == '\r' || r == '\t' {
			continue
		}
		if r < 0x20 {
			return false
		}
	}
	return true
}

func allDigits(value string) bool {
	if value == "" {
		return false
	}
	for _, r := range value {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

func syncsafeUint32(b []byte) uint32 {
	if len(b) < 4 {
		return 0
	}
	return uint32(b[0]&0x7f)<<21 | uint32(b[1]&0x7f)<<14 | uint32(b[2]&0x7f)<<7 | uint32(b[3]&0x7f)
}
