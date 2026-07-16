package scan

import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"strconv"
	"strings"
	"unicode/utf16"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/textkey"
)

func applyID3TrackFields(
	scannedFile *app.ScannedFile,
	format string,
	frames id3TextFrameMap,
) {
	if len(frames) == 0 {
		return
	}

	record := scannedFile.Metadata

	if values := frames.values("TP1", "TPE1"); len(values) > 0 {
		record.Artists = cleanStringValues(values...)
	}
	if len(record.ArtistSort) == 0 {
		values := frames.values("TSOP")
		if len(values) == 0 {
			values = frames.userTextValues("ARTISTSORT", "ARTIST SORT")
		}
		record.ArtistSort = cleanStringValues(values...)
	}
	if values := frames.values("TP2", "TPE2"); len(values) > 0 {
		record.AlbumArtists = cleanStringValues(values...)
	}
	if len(record.AlbumArtistSort) == 0 {
		values := frames.values("TSO2")
		if len(values) == 0 {
			values = frames.userTextValues("ALBUMARTISTSORT", "ALBUM ARTIST SORT")
		}
		record.AlbumArtistSort = cleanStringValues(values...)
	}
	if values := id3PerformerValues(frames); len(values) > 0 {
		record.Performers = values
	}
	setIfZero(&record.Composer, frames.first("TCM", "TCOM"))
	setIfZero(&record.Album, frames.first("TAL", "TALB"))
	setIfZero(&record.AlbumSort, textkey.FirstNonZeroValue(
		frames.first("TSOA"),
		frames.firstUserText("ALBUMSORT", "ALBUM SORT"),
	))
	setIfZero(&record.Title, frames.first("TT2", "TIT2"))
	setIfZero(&record.Work, frames.firstUserText("WORK"))
	setIfZero(&record.Grouping, textkey.FirstNonZeroValue(
		frames.first("TT1", "TIT1"),
		frames.firstUserText("GROUPING", "CONTENTGROUP"),
	))
	setIfZero(&record.MovementName, textkey.FirstNonZeroValue(
		frames.first("MVNM"),
		frames.firstUserText("MOVEMENTNAME", "MOVEMENT NAME"),
	))

	setIfZero(&record.MovementNumber, textkey.FirstNonZeroValue(
		frames.firstInt("MVIN"),
		frames.firstUserTextInt("MOVEMENTNUMBER", "MOVEMENT NUMBER"),
	))
	setIfZero(&record.MovementCount, textkey.FirstNonZeroValue(
		frames.firstSlashCount("MVIN"),
		frames.firstUserTextInt("MOVEMENTCOUNT", "MOVEMENT COUNT", "MOVEMENTTOTAL", "MOVEMENT TOTAL"),
	))
	if value, ok := frames.firstUserTextBool("SHOWMOVEMENT", "SHOW MOVEMENT"); ok {
		record.ShowMovement = value
	}

	if number, count := parseSlashPair(frames.first("TRK", "TRCK")); number != 0 || count != 0 {
		setIfZero(&record.TrackNumber, number)
		setIfZero(&record.TrackCount, count)
	}
	if number, count := parseSlashPair(frames.first("TPA", "TPOS")); number != 0 || count != 0 {
		setIfZero(&record.DiscNumber, number)
		setIfZero(&record.DiscCount, count)
	}

	setIfZero(&record.ReleaseDate, id3ReleaseDate(frames, format))
	setIfZero(&record.OriginalReleaseDate, id3OriginalReleaseDate(frames, format))
	setIfZero(&record.RecordedDate, id3RecordedDate(frames, format))
	setIfZero(&record.OriginalReleaseYear, yearFromDate(record.OriginalReleaseDate))

	setIfZero(&record.ItunesStoreTrackId, textkey.FirstNonZeroValue(
		id3FirstUserTextInt64(frames, "ITUNESSTORETRACKID", "ITUNES STORE TRACK ID", "CNID"),
		id3TextInt64(frames, "UFID"),
	))
	setIfZero(&record.ItunesStoreAlbumId, id3FirstUserTextInt64(frames, "ITUNESSTOREALBUMID", "ITUNES STORE ALBUM ID", "PLID"))
	setIfZero(&record.PurchaseDate, frames.firstUserText("PURCHASEDATE", "PURCHASE DATE"))
	if value, ok := frames.firstBool("TCP", "TCMP"); ok {
		record.IsCompilation = &value
	} else if value, ok := frames.firstUserTextBool("COMPILATION", "ISCOMPILATION", "ITUNESCOMPILATION"); ok {
		record.IsCompilation = &value
	}

	if genres := id3GenreValues(frames.values("TCO", "TCON")); len(genres) > 0 {
		record.Genres = genres
	}
}

func applyID3v1TrackFields(scannedFile *app.ScannedFile, metadata id3v1Metadata) {
	record := scannedFile.Metadata

	if metadata.Artist != "" {
		record.Artists = cleanStringValues(metadata.Artist)
	}
	setIfZero(&record.Album, metadata.Album)
	setIfZero(&record.Title, metadata.Title)
	setIfZero(&record.TrackNumber, metadata.Track)
	setIfZero(&record.ReleaseYear, metadata.Year)
	if metadata.Genre != "" {
		record.Genres = []string{metadata.Genre}
	}
}

func id3ReleaseDate(frames id3TextFrameMap, format string) string {
	switch format {
	case formatID3v22:
		return combineID3Date(frames.first("TYE"), frames.first("TDA"))
	case formatID3v23:
		return combineID3Date(frames.first("TYER"), frames.first("TDAT"))
	case formatID3v24:
		return frames.first("TDRL", "TDRC")
	default:
		return ""
	}
}

func id3OriginalReleaseDate(frames id3TextFrameMap, format string) string {
	switch format {
	case formatID3v22:
		return frames.first("TOR")
	case formatID3v23:
		return frames.first("TORY")
	case formatID3v24:
		return frames.first("TDOR")
	default:
		return ""
	}
}

func id3RecordedDate(frames id3TextFrameMap, format string) string {
	switch format {
	case formatID3v22:
		return frames.first("TRD")
	case formatID3v23:
		return frames.first("TRDA")
	case formatID3v24:
		return frames.first("TDRC")
	default:
		return ""
	}
}

func id3FirstUserTextInt64(frames id3TextFrameMap, descriptions ...string) int64 {
	if value, ok := parseInt64Text(frames.firstUserText(descriptions...)); ok {
		return value
	}
	return 0
}

func id3TextInt64(frames id3TextFrameMap, keys ...string) int64 {
	if value, ok := parseInt64Text(frames.first(keys...)); ok {
		return value
	}
	return 0
}

var (
	errNoID3v2 = errors.New("expected ID3 header")
	errNoID3v1 = errors.New("expected ID3v1 header")
)

type id3Metadata struct {
	Format     string
	TextFrames id3TextFrameMap
	Artwork    []artwork
}

type id3TextFrame struct {
	Description string
	Values      []string
}

type id3TextFrameMap map[string][]id3TextFrame

type id3v1Metadata struct {
	Title  string
	Artist string
	Album  string
	Year   int
	Track  int
	Genre  string
}

func (frames id3TextFrameMap) first(keys ...string) string {
	for _, value := range frames.values(keys...) {
		if value := cleanTagText(value); value != "" {
			return value
		}
	}
	return ""
}

func (frames id3TextFrameMap) firstInt(keys ...string) int {
	if value, ok := parseIntText(frames.first(keys...)); ok {
		return value
	}
	return 0
}

func (frames id3TextFrameMap) firstBool(keys ...string) (bool, bool) {
	return parseBoolText(frames.first(keys...))
}

func (frames id3TextFrameMap) firstSlashCount(keys ...string) int {
	for _, value := range frames.values(keys...) {
		if _, count := parseSlashPair(value); count != 0 {
			return count
		}
	}
	return 0
}

func (frames id3TextFrameMap) values(keys ...string) []string {
	var values []string
	seenKeys := make(map[string]bool, len(keys))
	for _, key := range keys {
		key = strings.ToUpper(key)
		if seenKeys[key] {
			continue
		}
		seenKeys[key] = true
		for _, frame := range frames[key] {
			values = append(values, frame.Values...)
		}
	}
	return values
}

func (frames id3TextFrameMap) firstUserText(descriptions ...string) string {
	for _, frame := range frames["TXX"] {
		if descriptionMatches(frame.Description, descriptions...) {
			return textkey.FirstNonZeroValue(frame.Values...)
		}
	}
	for _, frame := range frames["TXXX"] {
		if descriptionMatches(frame.Description, descriptions...) {
			return textkey.FirstNonZeroValue(frame.Values...)
		}
	}
	return ""
}

func (frames id3TextFrameMap) userTextValues(descriptions ...string) []string {
	var values []string
	for _, key := range []string{"TXX", "TXXX"} {
		for _, frame := range frames[key] {
			if descriptionMatches(frame.Description, descriptions...) {
				values = append(values, frame.Values...)
			}
		}
	}
	return values
}

func (frames id3TextFrameMap) firstUserTextInt(descriptions ...string) int {
	if value, ok := parseIntText(frames.firstUserText(descriptions...)); ok {
		return value
	}
	return 0
}

func (frames id3TextFrameMap) firstUserTextBool(descriptions ...string) (bool, bool) {
	return parseBoolText(frames.firstUserText(descriptions...))
}

func removeID3Unsynchronisation(data []byte) []byte {
	result := make([]byte, 0, len(data))
	for i := 0; i < len(data); i++ {
		if i > 0 && data[i-1] == 0xff && data[i] == 0x00 {
			continue
		}
		result = append(result, data[i])
	}
	return result
}

func id3GenreValues(values []string) []string {
	var genres []string
	for _, value := range values {
		genres = append(genres, decodeID3Genre(value))
	}
	return genres
}

func decodeID3Genre(value string) string {
	value = cleanTagText(value)
	if strings.HasPrefix(value, "(") {
		if end := strings.Index(value, ")"); end > 1 {
			if n, err := strconv.Atoi(value[1:end]); err == nil && n >= 0 && n < len(id3v1Genres) {
				return cleanTagText(id3v1Genres[n] + " " + value[end+1:])
			}
		}
	}
	return value
}

func id3PerformerValues(frames id3TextFrameMap) []string {
	var values []string
	values = append(values, frames.values("TP3", "TPE3")...)
	values = append(values, frames.userTextValues("PERFORMER", "PERFORMERS")...)
	values = append(values, id3PeopleFrameValues(frames, []string{"TMCL"}, true)...)
	values = append(values, id3PeopleFrameValues(frames, []string{"IPL", "IPLS", "TIPL"}, false)...)
	return cleanStringValues(values...)
}

func id3PeopleFrameValues(frames id3TextFrameMap, keys []string, includeAllRoles bool) []string {
	var people []string
	for _, key := range keys {
		for _, frame := range frames[key] {
			parts := frame.Values
			if len(parts) == 1 {
				parts = splitID3PeopleList(parts[0])
			}
			for i := 0; i+1 < len(parts); i += 2 {
				role := parts[i]
				person := parts[i+1]
				if includeAllRoles || isID3PerformerRole(role) {
					people = append(people, person)
				}
			}
		}
	}
	return people
}

func splitID3PeopleList(value string) []string {
	parts := strings.Split(value, "\x00")
	if len(parts) == 1 {
		parts = strings.Split(value, "/")
	}
	values := make([]string, 0, len(parts))
	for _, part := range parts {
		part = cleanTagText(part)
		if part != "" {
			values = append(values, part)
		}
	}
	return values
}

func isID3PerformerRole(role string) bool {
	switch normalizeDescriptor(role) {
	case "performer", "performers", "performance", "musician", "musicians", "instrument", "instruments", "vocal", "vocals", "vocalist", "vocalists", "guitar", "guitars", "bass", "drums", "piano", "keyboards", "synth", "synthesizer", "conductor":
		return true
	default:
		return false
	}
}

// readID3Metadata reads the leading ID3v2 tag from an MP3 file:
//
//	ID3v2 tag
//	  header: "ID3" version revision flags size
//	  optional extended header
//	  frame: text, URL, picture, private data, ...
//	  frame: ...
//	  padding
//	  optional ID3v2.4 footer
//	MPEG audio frames
//	optional ID3v1 "TAG" trailer
//
// The tag header tells us how many bytes belong to ID3v2, so this reader stays
// inside that tag body and never scans into the audio frames. An ID3v2.4 footer
// may sit between the tag body and audio, and the optional trailing ID3v1 tag is
// handled separately by readID3v1Metadata.
func readID3Metadata(r io.ReadSeeker) (id3Metadata, error) {
	// ID3v2, when present, lives at the beginning of the file before the MPEG
	// audio frames.
	if _, err := r.Seek(0, io.SeekStart); err != nil {
		return id3Metadata{}, err
	}

	// The fixed ID3v2 header is 10 bytes:
	//
	//	"ID3" major-version revision flags syncsafe-size
	//
	// The syncsafe size covers only the tag body after this header.
	var header [10]byte
	if _, err := io.ReadFull(r, header[:]); err != nil {
		return id3Metadata{}, err
	}
	if string(header[:3]) != "ID3" {
		return id3Metadata{}, errNoID3v2
	}

	version := int(header[3])
	if version < 2 || version > 4 {
		return id3Metadata{}, fmt.Errorf("unsupported ID3v2.%d", version)
	}

	// ID3v2 sizes are stored as syncsafe integers so no byte looks like an MPEG
	// frame sync marker. Once this body is read, the frame parser below only
	// walks the in-memory ID3 body slice, not any following footer or audio data.
	tagSize := int(syncsafeUint32(header[6:10]))
	body := make([]byte, tagSize)
	if _, err := io.ReadFull(r, body); err != nil {
		return id3Metadata{}, err
	}
	if header[5]&0x80 != 0 {
		// Unsynchronisation inserts 0x00 after 0xff bytes to avoid accidental
		// MPEG sync patterns in tag data; remove those inserted bytes before
		// decoding frames.
		body = removeID3Unsynchronisation(body)
	}

	// v2.3 and v2.4 may include an extended header before the first frame. v2.2
	// does not use the extended-header flag handled here.
	offset, err := id3FrameStartOffset(version, header[5], body)
	if err != nil {
		return id3Metadata{}, err
	}

	frames := make(id3TextFrameMap)
	var pics []artwork
	for offset < len(body) {
		// Frame headers vary by ID3v2 version:
		//
		//	v2.2: id(3) size(3) payload
		//	v2.3: id(4) size(4) flags(2) payload
		//	v2.4: id(4) syncsafe-size(4) flags(2) payload
		//
		// readID3NamedFrame returns ok=false at padding or malformed trailing
		// bytes, which marks the logical end of the tag before audio begins.
		id, payload, next, ok, err := readID3NamedFrame(version, body, offset)
		if err != nil {
			return id3Metadata{}, err
		}
		if !ok {
			break
		}
		if payload == nil {
			// Encrypted or compressed frames are intentionally skipped by
			// prepareID3FramePayload because this scanner cannot decode them.
			offset = next
			continue
		}
		if isID3TextFrame(id) {
			frame, err := decodeID3NamedTextFrame(id, payload)
			if err != nil {
				return id3Metadata{}, err
			}
			if len(frame.Values) > 0 {
				frames[id] = append(frames[id], frame)
			}
		} else if isID3PictureFrame(id) {
			// ID3v2.2 uses PIC and v2.3/v2.4 use APIC. Both carry picture type,
			// MIME/type information, an optional description, and the image bytes.
			pic, err := decodeID3PictureFrame(id, payload)
			if err != nil {
				return id3Metadata{}, err
			}
			pics = append(pics, pic)
		}
		offset = next
	}

	return id3Metadata{
		Format:     id3Format(version),
		TextFrames: frames,
		Artwork:    pics,
	}, nil
}

func id3FrameStartOffset(version int, flags byte, body []byte) (int, error) {
	if flags&0x40 == 0 {
		return 0, nil
	}

	switch version {
	case 3:
		if len(body) < 4 {
			return 0, fmt.Errorf("short ID3v2.3 extended header")
		}
		extendedSize := int(binary.BigEndian.Uint32(body[:4]))
		if 4+extendedSize > len(body) {
			return 0, fmt.Errorf("ID3v2.3 extended header exceeds tag")
		}
		return 4 + extendedSize, nil
	case 4:
		if len(body) < 4 {
			return 0, fmt.Errorf("short ID3v2.4 extended header")
		}
		extendedSize := int(syncsafeUint32(body[:4]))
		if extendedSize > len(body) {
			return 0, fmt.Errorf("ID3v2.4 extended header exceeds tag")
		}
		return extendedSize, nil
	default:
		return 0, nil
	}
}

func readID3NamedFrame(version int, body []byte, offset int) (string, []byte, int, bool, error) {
	id, size, payloadOffset, flags, ok, err := readID3FrameHeader(version, body, offset)
	if err != nil || !ok {
		return "", nil, offset, ok, err
	}
	next := payloadOffset + size
	if next > len(body) {
		return "", nil, offset, false, fmt.Errorf("ID3 frame %q exceeds tag", id)
	}

	payload, ok := prepareID3FramePayload(version, flags, body[payloadOffset:next])
	if !ok {
		return id, nil, next, true, nil
	}

	return id, payload, next, true, nil
}

func readID3FrameHeader(version int, body []byte, offset int) (string, int, int, []byte, bool, error) {
	switch version {
	case 2:
		if offset+6 > len(body) {
			return "", 0, offset, nil, false, nil
		}
		id := string(body[offset : offset+3])
		if isID3Padding(id) {
			return "", 0, offset, nil, false, nil
		}
		if !isID3FrameID(id) {
			return "", 0, offset, nil, false, nil
		}
		size := int(body[offset+3])<<16 | int(body[offset+4])<<8 | int(body[offset+5])
		if size == 0 {
			return "", 0, offset, nil, false, nil
		}
		return id, size, offset + 6, nil, true, nil
	case 3:
		if offset+10 > len(body) {
			return "", 0, offset, nil, false, nil
		}
		id := string(body[offset : offset+4])
		if isID3Padding(id) {
			return "", 0, offset, nil, false, nil
		}
		if !isID3FrameID(id) {
			return "", 0, offset, nil, false, nil
		}
		size := int(binary.BigEndian.Uint32(body[offset+4 : offset+8]))
		if size == 0 {
			return "", 0, offset, nil, false, nil
		}
		return id, size, offset + 10, body[offset+8 : offset+10], true, nil
	case 4:
		if offset+10 > len(body) {
			return "", 0, offset, nil, false, nil
		}
		id := string(body[offset : offset+4])
		if isID3Padding(id) {
			return "", 0, offset, nil, false, nil
		}
		if !isID3FrameID(id) {
			return "", 0, offset, nil, false, nil
		}
		size := int(syncsafeUint32(body[offset+4 : offset+8]))
		if size == 0 {
			return "", 0, offset, nil, false, nil
		}
		return id, size, offset + 10, body[offset+8 : offset+10], true, nil
	default:
		return "", 0, offset, nil, false, fmt.Errorf("unsupported ID3v2.%d", version)
	}
}

func prepareID3FramePayload(version int, flags []byte, payload []byte) ([]byte, bool) {
	if len(flags) < 2 {
		return payload, true
	}

	switch version {
	case 3:
		if flags[1]&0xc0 != 0 {
			return nil, false
		}
		if flags[1]&0x20 != 0 {
			if len(payload) < 1 {
				return nil, false
			}
			payload = payload[1:]
		}
	case 4:
		if flags[1]&0x0c != 0 {
			return nil, false
		}
		if flags[1]&0x40 != 0 {
			if len(payload) < 1 {
				return nil, false
			}
			payload = payload[1:]
		}
		if flags[1]&0x01 != 0 {
			if len(payload) < 4 {
				return nil, false
			}
			payload = payload[4:]
		}
		if flags[1]&0x02 != 0 {
			payload = removeID3Unsynchronisation(payload)
		}
	}
	return payload, true
}

func isID3TextFrame(id string) bool {
	return strings.HasPrefix(id, "T")
}

func isID3Padding(id string) bool {
	for _, b := range []byte(id) {
		if b != 0 {
			return false
		}
	}
	return true
}

func isID3FrameID(id string) bool {
	for _, r := range id {
		if (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9') {
			continue
		}
		return false
	}
	return true
}

func isID3PictureFrame(id string) bool {
	return id == "PIC" || id == "APIC"
}

func decodeID3NamedTextFrame(id string, payload []byte) (id3TextFrame, error) {
	if id == "TXX" || id == "TXXX" {
		return decodeID3UserTextFrame(payload)
	}
	values, err := decodeID3TextFrame(payload)
	return id3TextFrame{Values: values}, err
}

func decodeID3TextFrame(payload []byte) ([]string, error) {
	if len(payload) == 0 {
		return nil, nil
	}
	text, err := decodeID3EncodedText(payload[0], payload[1:])
	if err != nil {
		return nil, err
	}
	return splitID3TextValues(text), nil
}

func decodeID3UserTextFrame(payload []byte) (id3TextFrame, error) {
	if len(payload) == 0 {
		return id3TextFrame{}, nil
	}
	text, err := decodeID3EncodedText(payload[0], payload[1:])
	if err != nil {
		return id3TextFrame{}, err
	}
	parts := splitID3TextValues(text)
	if len(parts) == 0 {
		return id3TextFrame{}, nil
	}
	return id3TextFrame{
		Description: parts[0],
		Values:      parts[1:],
	}, nil
}

func decodeID3EncodedText(encoding byte, data []byte) (string, error) {
	switch encoding {
	case 0:
		runes := make([]rune, len(data))
		for i, b := range data {
			runes[i] = rune(b)
		}
		return string(runes), nil
	case 1:
		if len(data) < 2 {
			return "", nil
		}
		var order binary.ByteOrder = binary.LittleEndian
		switch {
		case data[0] == 0xfe && data[1] == 0xff:
			order = binary.BigEndian
			data = data[2:]
		case data[0] == 0xff && data[1] == 0xfe:
			order = binary.LittleEndian
			data = data[2:]
		}
		return decodeID3UTF16(data, order), nil
	case 2:
		return decodeID3UTF16(data, binary.BigEndian), nil
	case 3:
		return string(data), nil
	default:
		runes := make([]rune, len(data))
		for i, b := range data {
			runes[i] = rune(b)
		}
		return string(runes), nil
	}
}

func decodeID3UTF16(data []byte, order binary.ByteOrder) string {
	if len(data)%2 != 0 {
		data = data[:len(data)-1]
	}
	units := make([]uint16, len(data)/2)
	for i := range units {
		units[i] = order.Uint16(data[i*2 : i*2+2])
	}
	return strings.TrimPrefix(string(utf16.Decode(units)), "\ufeff")
}

func splitID3TextValues(text string) []string {
	parts := strings.Split(text, "\x00")
	values := make([]string, 0, len(parts))
	for _, part := range parts {
		part = cleanTagText(part)
		if part != "" {
			values = append(values, part)
		}
	}
	return values
}

func decodeID3PictureFrame(id string, payload []byte) (artwork, error) {
	switch id {
	case "PIC":
		return decodeID3PICFrame(payload)
	case "APIC":
		return decodeID3APICFrame(payload)
	default:
		return artwork{}, nil
	}
}

func decodeID3PICFrame(payload []byte) (artwork artwork, err error) {
	if len(payload) < 5 {
		err = fmt.Errorf("invalid PIC frame")
		return
	}

	encoding := payload[0]
	format := strings.ToLower(string(payload[1:4]))
	pictureType := normalizeArtworkPictureType(uint32(payload[4]))
	rest := payload[5:]
	_, data, ok := splitID3EncodedString(rest, encoding)
	if !ok {
		err = fmt.Errorf("invalid PIC description")
		return
	}

	artwork.PictureType = pictureType
	artwork.MIMEType = id3PictureFormatMIMEType(format)
	artwork.Data = data

	return
}

func decodeID3APICFrame(payload []byte) (artwork artwork, err error) {
	if len(payload) == 0 {
		err = fmt.Errorf("invalid APIC frame")
		return
	}

	encoding := payload[0]
	mimeEnd := bytes.IndexByte(payload[1:], 0)
	if mimeEnd < 0 {
		err = fmt.Errorf("invalid APIC MIME type")
		return
	}
	mime := string(payload[1 : 1+mimeEnd])
	rest := payload[1+mimeEnd+1:]
	if len(rest) == 0 {
		err = fmt.Errorf("invalid APIC picture type")
		return
	}

	pictureType := normalizeArtworkPictureType(uint32(rest[0]))
	_, data, ok := splitID3EncodedString(rest[1:], encoding)
	if !ok {
		err = fmt.Errorf("invalid APIC description")
		return
	}

	artwork.PictureType = pictureType
	artwork.MIMEType = normalizeImageMIMEType(mime)
	artwork.Data = data

	return
}

func splitID3EncodedString(data []byte, encoding byte) ([]byte, []byte, bool) {
	delimiter := []byte{0}
	if encoding == 1 || encoding == 2 {
		delimiter = []byte{0, 0}
	}

	parts := bytes.SplitN(data, delimiter, 2)
	if len(parts) != 2 {
		return nil, nil, false
	}
	if len(parts[1]) > 0 && parts[1][0] == 0 {
		parts[0] = append(parts[0], parts[1][0])
		parts[1] = parts[1][1:]
	}
	return parts[0], parts[1], true
}

func id3PictureFormatMIMEType(format string) string {
	switch format {
	case "jpg", "jpeg":
		return "image/jpeg"
	case "png":
		return "image/png"
	case "gif":
		return "image/gif"
	default:
		return normalizeImageMIMEType(format)
	}
}

func normalizeImageMIMEType(value string) string {
	value = strings.ToLower(strings.TrimSpace(value))
	switch value {
	case "image/jpg":
		return "image/jpeg"
	default:
		return value
	}
}

func id3Format(version int) string {
	switch version {
	case 2:
		return formatID3v22
	case 3:
		return formatID3v23
	case 4:
		return formatID3v24
	default:
		return ""
	}
}

func readID3v1Metadata(r io.ReadSeeker) (id3v1Metadata, error) {
	if _, err := r.Seek(-128, io.SeekEnd); err != nil {
		return id3v1Metadata{}, err
	}

	var data [128]byte
	if _, err := io.ReadFull(r, data[:]); err != nil {
		return id3v1Metadata{}, err
	}
	if string(data[:3]) != "TAG" {
		return id3v1Metadata{}, errNoID3v1
	}

	year, _ := strconv.Atoi(cleanTagText(string(data[93:97])))
	track := 0
	if data[125] == 0 {
		track = int(data[126])
	}

	genre := ""
	if int(data[127]) < len(id3v1Genres) {
		genre = id3v1Genres[int(data[127])]
	}

	return id3v1Metadata{
		Title:  cleanTagText(string(data[3:33])),
		Artist: cleanTagText(string(data[33:63])),
		Album:  cleanTagText(string(data[63:93])),
		Year:   year,
		Track:  track,
		Genre:  genre,
	}, nil
}

func combineID3Date(year, date string) string {
	year = strings.TrimSpace(year)
	date = strings.TrimSpace(date)
	if year == "" {
		return date
	}
	if len(date) == 4 && allDigits(date) {
		return fmt.Sprintf("%s-%s-%s", year, date[2:4], date[0:2])
	}
	return year
}

func syncsafeUint32(b []byte) uint32 {
	if len(b) < 4 {
		return 0
	}
	return uint32(b[0]&0x7f)<<21 | uint32(b[1]&0x7f)<<14 | uint32(b[2]&0x7f)<<7 | uint32(b[3]&0x7f)
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
