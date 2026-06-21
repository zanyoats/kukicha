package scan

import (
	"encoding/binary"
	"fmt"
	"io"
	"strconv"
	"strings"
)

type mp4BoxHeader struct {
	Name string
	End  int64
}

type mp4Item struct {
	Name       string
	Mean       string
	CustomName string
	Data       []mp4Data
}

type mp4Data struct {
	Flags   uint32
	Locale  uint32
	Payload []byte
}

func readMP4ItemList(r io.ReadSeeker) ([]mp4Item, error) {
	end, err := r.Seek(0, io.SeekEnd)
	if err != nil {
		return nil, err
	}
	if _, err := r.Seek(0, io.SeekStart); err != nil {
		return nil, err
	}
	return walkMP4Boxes(r, end)
}

func walkMP4Boxes(r io.ReadSeeker, end int64) ([]mp4Item, error) {
	var items []mp4Item

	for {
		pos, err := r.Seek(0, io.SeekCurrent)
		if err != nil {
			return nil, err
		}
		if pos >= end {
			return items, nil
		}
		if end-pos < 8 {
			return items, nil
		}

		box, err := readMP4BoxHeader(r, end)
		if err != nil {
			return nil, err
		}

		switch box.Name {
		case "moov", "udta":
			// iTunes-style tags live under moov.udta.meta.ilst; these two boxes
			// are plain containers on that path.
			childItems, err := walkMP4Boxes(r, box.End)
			if err != nil {
				return nil, err
			}
			items = append(items, childItems...)

		case "meta":
			payloadStart, err := r.Seek(0, io.SeekCurrent)
			if err != nil {
				return nil, err
			}
			if box.End-payloadStart >= 4 {
				// The ISO BMFF "meta" box is a FullBox, so its child boxes start
				// after the four-byte version/flags field.
				if _, err := r.Seek(4, io.SeekCurrent); err != nil {
					return nil, err
				}
				childItems, err := walkMP4Boxes(r, box.End)
				if err != nil {
					return nil, err
				}
				items = append(items, childItems...)
			}

		case "ilst":
			// "ilst" contains one child box per metadata item; the child box
			// name is the atom/tag key such as "\xa9nam", "trkn", or "----".
			childItems, err := readMP4IlstItems(r, box.End)
			if err != nil {
				return nil, err
			}
			items = append(items, childItems...)
		}

		if _, err := r.Seek(box.End, io.SeekStart); err != nil {
			return nil, err
		}
	}
}

func readMP4BoxHeader(r io.ReadSeeker, parentEnd int64) (mp4BoxHeader, error) {
	start, err := r.Seek(0, io.SeekCurrent)
	if err != nil {
		return mp4BoxHeader{}, err
	}

	var header [8]byte
	if _, err := io.ReadFull(r, header[:]); err != nil {
		return mp4BoxHeader{}, err
	}

	size := uint64(binary.BigEndian.Uint32(header[:4]))
	headerSize := uint64(8)
	// MP4 box sizes are big-endian. A size of 1 means an extended 64-bit
	// size follows; a size of 0 means the box runs to the end of its parent.
	switch size {
	case 1:
		var large [8]byte
		if _, err := io.ReadFull(r, large[:]); err != nil {
			return mp4BoxHeader{}, err
		}
		size = binary.BigEndian.Uint64(large[:])
		headerSize = 16
	case 0:
		size = uint64(parentEnd - start)
	}

	if size < headerSize {
		return mp4BoxHeader{}, fmt.Errorf("invalid MP4 box %q size %d", string(header[4:8]), size)
	}

	boxEnd := start + int64(size)
	if boxEnd > parentEnd {
		return mp4BoxHeader{}, fmt.Errorf("MP4 box %q extends past parent", string(header[4:8]))
	}

	return mp4BoxHeader{
		Name: string(header[4:8]),
		End:  boxEnd,
	}, nil
}

func readMP4IlstItems(r io.ReadSeeker, end int64) ([]mp4Item, error) {
	var items []mp4Item

	for {
		pos, err := r.Seek(0, io.SeekCurrent)
		if err != nil {
			return nil, err
		}
		if pos >= end {
			return items, nil
		}
		if end-pos < 8 {
			return items, nil
		}

		itemBox, err := readMP4BoxHeader(r, end)
		if err != nil {
			return nil, err
		}

		item := mp4Item{Name: itemBox.Name}
		if err := readMP4ItemChildren(r, itemBox.End, &item); err != nil {
			return nil, err
		}
		if len(item.Data) > 0 || item.Mean != "" || item.CustomName != "" {
			items = append(items, item)
		}

		if _, err := r.Seek(itemBox.End, io.SeekStart); err != nil {
			return nil, err
		}
	}
}

func readMP4ItemChildren(r io.ReadSeeker, end int64, item *mp4Item) error {
	for {
		pos, err := r.Seek(0, io.SeekCurrent)
		if err != nil {
			return err
		}
		if pos >= end {
			return nil
		}
		if end-pos < 8 {
			return nil
		}

		childBox, err := readMP4BoxHeader(r, end)
		if err != nil {
			return err
		}

		// Apple metadata item children use "data" for the typed value. Free-form
		// "----" items also carry "mean" and "name" boxes for namespace/key.
		switch childBox.Name {
		case "data":
			data, err := readMP4DataBox(r, childBox.End)
			if err != nil {
				return err
			}
			item.Data = append(item.Data, data)

		case "mean":
			mean, err := readMP4FullTextBox(r, childBox.End)
			if err != nil {
				return err
			}
			item.Mean = mean

		case "name":
			name, err := readMP4FullTextBox(r, childBox.End)
			if err != nil {
				return err
			}
			item.CustomName = name
		}

		if _, err := r.Seek(childBox.End, io.SeekStart); err != nil {
			return err
		}
	}
}

func readMP4DataBox(r io.ReadSeeker, end int64) (mp4Data, error) {
	payload, err := readMP4BoxPayload(r, end)
	if err != nil {
		return mp4Data{}, err
	}
	if len(payload) < 8 {
		return mp4Data{}, fmt.Errorf("invalid MP4 data box: expected at least 8 bytes, got %d", len(payload))
	}

	// The first byte is the FullBox version. The next three bytes are the
	// iTunes data type flags, followed by a 32-bit locale indicator.
	flags := uint32(payload[1])<<16 | uint32(payload[2])<<8 | uint32(payload[3])
	return mp4Data{
		Flags:   flags,
		Locale:  binary.BigEndian.Uint32(payload[4:8]),
		Payload: payload[8:],
	}, nil
}

func readMP4FullTextBox(r io.ReadSeeker, end int64) (string, error) {
	payload, err := readMP4BoxPayload(r, end)
	if err != nil {
		return "", err
	}
	if len(payload) < 4 {
		return "", fmt.Errorf("invalid MP4 full text box: expected at least 4 bytes, got %d", len(payload))
	}
	// "mean" and "name" are FullBoxes too; their text begins after version/flags.
	return strings.TrimRight(string(payload[4:]), "\x00"), nil
}

func readMP4BoxPayload(r io.ReadSeeker, end int64) ([]byte, error) {
	pos, err := r.Seek(0, io.SeekCurrent)
	if err != nil {
		return nil, err
	}
	if end < pos {
		return nil, fmt.Errorf("invalid MP4 box bounds")
	}

	payload := make([]byte, end-pos)
	if _, err := io.ReadFull(r, payload); err != nil {
		return nil, err
	}
	return payload, nil
}

func indexMP4Items(items []mp4Item) map[string]mp4Item {
	byName := make(map[string]mp4Item, len(items))
	for _, item := range items {
		if _, ok := byName[item.Name]; !ok {
			byName[item.Name] = item
		}
		// Free-form "----" atoms are addressed by their "name" child box,
		// not only by the literal "----" atom name.
		if item.CustomName != "" {
			if _, ok := byName[item.CustomName]; !ok {
				byName[item.CustomName] = item
			}
		}
	}
	return byName
}

func firstMP4Text(items map[string]mp4Item, keys ...string) string {
	for _, key := range keys {
		item, ok := lookupMP4Item(items, key)
		if !ok {
			continue
		}
		if value := mp4ItemText(item); value != "" {
			return value
		}
	}
	return ""
}

func firstMP4Int(items map[string]mp4Item, keys ...string) int {
	for _, key := range keys {
		item, ok := lookupMP4Item(items, key)
		if !ok {
			continue
		}
		if value, ok := mp4ItemInt64(item); ok {
			return int(value)
		}
	}
	return 0
}

func firstMP4IntPair(items map[string]mp4Item, keys ...string) (int, int, bool) {
	for _, key := range keys {
		item, ok := lookupMP4Item(items, key)
		if !ok {
			continue
		}
		if first, second, ok := mp4ItemIntPair(item); ok {
			return first, second, true
		}
	}
	return 0, 0, false
}

func firstMP4Int64(items map[string]mp4Item, keys ...string) int64 {
	for _, key := range keys {
		item, ok := lookupMP4Item(items, key)
		if !ok {
			continue
		}
		if value, ok := mp4ItemInt64(item); ok {
			return value
		}
	}
	return 0
}

func firstMP4Bool(items map[string]mp4Item, keys ...string) (bool, bool) {
	for _, key := range keys {
		item, ok := lookupMP4Item(items, key)
		if !ok {
			continue
		}
		if value, ok := mp4ItemBool(item); ok {
			return value, true
		}
	}
	return false, false
}

func lookupMP4Item(items map[string]mp4Item, key string) (mp4Item, bool) {
	if item, ok := items[key]; ok {
		return item, true
	}
	for itemKey, item := range items {
		if mp4KeyMatches(itemKey, key) {
			return item, true
		}
	}
	return mp4Item{}, false
}

func mp4TextValues(items []mp4Item, keys ...string) []string {
	var values []string
	for _, key := range keys {
		for _, item := range items {
			if !mp4ItemMatches(item, key) {
				continue
			}
			values = append(values, mp4ItemTextValues(item)...)
		}
	}
	return cleanStringValues(values...)
}

func mp4ItemMatches(item mp4Item, key string) bool {
	return mp4KeyMatches(item.Name, key) || mp4KeyMatches(item.CustomName, key)
}

func mp4KeyMatches(got, want string) bool {
	if got == "" {
		return false
	}
	normalized := normalizeDescriptor(got)
	return strings.EqualFold(got, want) || (normalized != "" && normalized == normalizeDescriptor(want))
}

func mp4ItemTextValues(item mp4Item) []string {
	var values []string
	for _, data := range item.Data {
		if value, ok := mp4TextDataValue(data); ok {
			values = append(values, value)
		}
	}
	return values
}

func mp4TextDataValue(data mp4Data) (string, bool) {
	// Data type 1 is declared UTF-8 text; looksLikeText covers taggers that
	// leave the type flag unset but still write textual payloads.
	if data.Flags == 1 || looksLikeText(data.Payload) {
		value := cleanTagText(string(data.Payload))
		return value, value != ""
	}
	return "", false
}

func mp4ItemText(item mp4Item) string {
	for _, data := range item.Data {
		switch item.Name {
		case "gnre":
			if len(data.Payload) >= 2 {
				// The legacy "gnre" atom stores a 1-based ID3v1 genre code.
				code := int(binary.BigEndian.Uint16(data.Payload[:2]))
				if genre := mp4GenreName(code); genre != "" {
					return genre
				}
				return strconv.Itoa(code)
			}
		}

		switch data.Flags {
		case 1:
			// iTunes data type 1 is UTF-8 text.
			return cleanTagText(string(data.Payload))
		case 21:
			// iTunes data type 21 is an integer stored as a big-endian payload.
			return strconv.FormatUint(bigEndianUint(data.Payload), 10)
		}
		if looksLikeText(data.Payload) {
			return cleanTagText(string(data.Payload))
		}
	}
	return ""
}

func mp4ItemInt64(item mp4Item) (int64, bool) {
	for _, data := range item.Data {
		switch item.Name {
		case "trkn", "disk":
			if len(data.Payload) >= 4 {
				// Track/disc numbers are packed as reserved bytes followed by a
				// big-endian 16-bit number and, when present, a 16-bit total.
				return int64(binary.BigEndian.Uint16(data.Payload[2:4])), true
			}
		}

		if data.Flags == 21 {
			return int64(bigEndianUint(data.Payload)), true
		}
		if data.Flags == 1 || looksLikeText(data.Payload) {
			if value, ok := parseInt64Text(string(data.Payload)); ok {
				return value, true
			}
		}
	}
	return 0, false
}

func mp4ItemIntPair(item mp4Item) (int, int, bool) {
	for _, data := range item.Data {
		switch item.Name {
		case "trkn", "disk":
			if len(data.Payload) >= 6 {
				// The packed layout is two reserved bytes, current number, then
				// optional total, each numeric field big-endian 16-bit.
				first := int(binary.BigEndian.Uint16(data.Payload[2:4]))
				second := int(binary.BigEndian.Uint16(data.Payload[4:6]))
				return first, second, true
			}
			if len(data.Payload) >= 4 {
				first := int(binary.BigEndian.Uint16(data.Payload[2:4]))
				return first, 0, true
			}
		}

		if data.Flags == 1 || looksLikeText(data.Payload) {
			first, second := parseSlashPair(string(data.Payload))
			if first != 0 || second != 0 {
				return first, second, true
			}
		}
	}
	return 0, 0, false
}

func mp4ItemBool(item mp4Item) (bool, bool) {
	for _, data := range item.Data {
		if data.Flags == 21 {
			return bigEndianUint(data.Payload) != 0, true
		}
		if data.Flags == 1 || looksLikeText(data.Payload) {
			if value, ok := parseBoolText(string(data.Payload)); ok {
				return value, true
			}
		}
	}
	return false, false
}

func mp4Genres(items []mp4Item) []string {
	var genres []string
	// "\xa9gen" is Apple's text genre atom, "gnre" is the legacy numeric
	// genre atom, and "genre" catches free-form or normalized variants.
	for _, key := range []string{"\xa9gen", "gnre", "genre"} {
		for _, item := range items {
			if !mp4ItemMatches(item, key) {
				continue
			}
			genres = append(genres, mp4ItemGenreValues(item)...)
		}
	}
	return genres
}

func mp4Artwork(items []mp4Item) *embeddedArtwork {
	for _, item := range items {
		if !mp4ItemMatches(item, "covr") {
			continue
		}
		for _, data := range item.Data {
			if len(data.Payload) == 0 {
				continue
			}
			return &embeddedArtwork{
				MIMEType: mp4ArtworkMIMEType(data),
				Data:     data.Payload,
			}
		}
	}
	return nil
}

func mp4ArtworkMIMEType(data mp4Data) string {
	switch data.Flags {
	case 13:
		return "image/jpeg"
	case 14:
		return "image/png"
	default:
		return ""
	}
}

func mp4ItemGenreValues(item mp4Item) []string {
	var genres []string
	for _, data := range item.Data {
		if item.Name == "gnre" {
			if len(data.Payload) >= 2 {
				code := int(binary.BigEndian.Uint16(data.Payload[:2]))
				if genre := mp4GenreName(code); genre != "" {
					genres = append(genres, genre)
				} else {
					genres = append(genres, strconv.Itoa(code))
				}
			}
			continue
		}
		if value, ok := mp4TextDataValue(data); ok {
			genres = append(genres, splitFieldList(value)...)
		}
	}
	return genres
}

func mp4GenreName(code int) string {
	// MP4 "gnre" stores the ID3v1 genre index plus one.
	index := code - 1
	if index < 0 || index >= len(id3v1Genres) {
		return ""
	}
	return id3v1Genres[index]
}
