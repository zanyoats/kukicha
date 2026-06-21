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
)

var (
	errNoID3v2 = errors.New("expected ID3 header")
	errNoID3v1 = errors.New("expected ID3v1 header")
)

type id3Metadata struct {
	Format     string
	TextFrames id3TextFrameMap
	Artwork    *embeddedArtwork
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
			return firstNonEmpty(frame.Values...)
		}
	}
	for _, frame := range frames["TXXX"] {
		if descriptionMatches(frame.Description, descriptions...) {
			return firstNonEmpty(frame.Values...)
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
		for _, genre := range splitFieldList(decodeID3Genre(value)) {
			genres = append(genres, genre)
		}
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

func isID3v2Format(format string) bool {
	switch format {
	case formatID3v22, formatID3v23, formatID3v24:
		return true
	default:
		return false
	}
}

func readID3TextFrames(r io.ReadSeeker) (id3TextFrameMap, error) {
	metadata, err := readID3Metadata(r)
	return metadata.TextFrames, err
}

func readID3Metadata(r io.ReadSeeker) (id3Metadata, error) {
	if _, err := r.Seek(0, io.SeekStart); err != nil {
		return id3Metadata{}, err
	}

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

	tagSize := int(syncsafeUint32(header[6:10]))
	body := make([]byte, tagSize)
	if _, err := io.ReadFull(r, body); err != nil {
		return id3Metadata{}, err
	}
	if header[5]&0x80 != 0 {
		body = removeID3Unsynchronisation(body)
	}

	offset, err := id3FrameStartOffset(version, header[5], body)
	if err != nil {
		return id3Metadata{}, err
	}

	frames := make(id3TextFrameMap)
	var artwork *embeddedArtwork
	for offset < len(body) {
		id, payload, next, ok, err := readID3NamedFrame(version, body, offset)
		if err != nil {
			return id3Metadata{}, err
		}
		if !ok {
			break
		}
		if payload == nil {
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
		} else if artwork == nil && isID3PictureFrame(id) {
			artwork, err = decodeID3PictureFrame(id, payload)
			if err != nil {
				return id3Metadata{}, err
			}
		}
		offset = next
	}

	return id3Metadata{
		Format:     id3Format(version),
		TextFrames: frames,
		Artwork:    artwork,
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

func readID3NamedTextFrame(version int, body []byte, offset int) (string, id3TextFrame, int, bool, error) {
	id, payload, next, ok, err := readID3NamedFrame(version, body, offset)
	if err != nil || !ok {
		return "", id3TextFrame{}, offset, ok, err
	}
	if !isID3TextFrame(id) {
		return id, id3TextFrame{}, next, true, nil
	}

	frame, err := decodeID3NamedTextFrame(id, payload)
	if err != nil {
		return "", id3TextFrame{}, offset, false, err
	}

	return id, frame, next, true, nil
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

func decodeID3PictureFrame(id string, payload []byte) (*embeddedArtwork, error) {
	switch id {
	case "PIC":
		return decodeID3PICFrame(payload)
	case "APIC":
		return decodeID3APICFrame(payload)
	default:
		return nil, nil
	}
}

func decodeID3PICFrame(payload []byte) (*embeddedArtwork, error) {
	if len(payload) < 5 {
		return nil, fmt.Errorf("invalid PIC frame")
	}

	encoding := payload[0]
	format := strings.ToLower(string(payload[1:4]))
	rest := payload[5:]
	_, data, ok := splitID3EncodedString(rest, encoding)
	if !ok {
		return nil, fmt.Errorf("invalid PIC description")
	}

	return &embeddedArtwork{
		MIMEType: id3PictureFormatMIMEType(format),
		Data:     data,
	}, nil
}

func decodeID3APICFrame(payload []byte) (*embeddedArtwork, error) {
	if len(payload) == 0 {
		return nil, fmt.Errorf("invalid APIC frame")
	}

	encoding := payload[0]
	mimeEnd := bytes.IndexByte(payload[1:], 0)
	if mimeEnd < 0 {
		return nil, fmt.Errorf("invalid APIC MIME type")
	}
	mime := string(payload[1 : 1+mimeEnd])
	rest := payload[1+mimeEnd+1:]
	if len(rest) == 0 {
		return nil, fmt.Errorf("invalid APIC picture type")
	}

	_, data, ok := splitID3EncodedString(rest[1:], encoding)
	if !ok {
		return nil, fmt.Errorf("invalid APIC description")
	}

	return &embeddedArtwork{
		MIMEType: normalizeImageMIMEType(mime),
		Data:     data,
	}, nil
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

var id3v1Genres = []string{
	"Blues",
	"Classic Rock",
	"Country",
	"Dance",
	"Disco",
	"Funk",
	"Grunge",
	"Hip-Hop",
	"Jazz",
	"Metal",
	"New Age",
	"Oldies",
	"Other",
	"Pop",
	"R&B",
	"Rap",
	"Reggae",
	"Rock",
	"Techno",
	"Industrial",
	"Alternative",
	"Ska",
	"Death Metal",
	"Pranks",
	"Soundtrack",
	"Euro-Techno",
	"Ambient",
	"Trip-Hop",
	"Vocal",
	"Jazz+Funk",
	"Fusion",
	"Trance",
	"Classical",
	"Instrumental",
	"Acid",
	"House",
	"Game",
	"Sound Clip",
	"Gospel",
	"Noise",
	"AlternRock",
	"Bass",
	"Soul",
	"Punk",
	"Space",
	"Meditative",
	"Instrumental Pop",
	"Instrumental Rock",
	"Ethnic",
	"Gothic",
	"Darkwave",
	"Techno-Industrial",
	"Electronic",
	"Pop-Folk",
	"Eurodance",
	"Dream",
	"Southern Rock",
	"Comedy",
	"Cult",
	"Gangsta",
	"Top 40",
	"Christian Rap",
	"Pop/Funk",
	"Jungle",
	"Native American",
	"Cabaret",
	"New Wave",
	"Psychedelic",
	"Rave",
	"Showtunes",
	"Trailer",
	"Lo-Fi",
	"Tribal",
	"Acid Punk",
	"Acid Jazz",
	"Polka",
	"Retro",
	"Musical",
	"Rock & Roll",
	"Hard Rock",
	"Folk",
	"Folk-Rock",
	"National Folk",
	"Swing",
	"Fast Fusion",
	"Bebob",
	"Latin",
	"Revival",
	"Celtic",
	"Bluegrass",
	"Avantgarde",
	"Gothic Rock",
	"Progressive Rock",
	"Psychedelic Rock",
	"Symphonic Rock",
	"Slow Rock",
	"Big Band",
	"Chorus",
	"Easy Listening",
	"Acoustic",
	"Humour",
	"Speech",
	"Chanson",
	"Opera",
	"Chamber Music",
	"Sonata",
	"Symphony",
	"Booty Bass",
	"Primus",
	"Porn Groove",
	"Satire",
	"Slow Jam",
	"Club",
	"Tango",
	"Samba",
	"Folklore",
	"Ballad",
	"Power Ballad",
	"Rhythmic Soul",
	"Freestyle",
	"Duet",
	"Punk Rock",
	"Drum Solo",
	"Acapella",
	"Euro-House",
	"Dance Hall",
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
