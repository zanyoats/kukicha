package scan

import (
	"net/http"
	"strconv"
	"strings"
	"unicode/utf8"
)

const UnknownItem = "__Unknown"

const (
	formatID3v1  = "ID3v1"
	formatID3v22 = "ID3v2.2"
	formatID3v23 = "ID3v2.3"
	formatID3v24 = "ID3v2.4"
	formatMP4    = "MP4"
	formatVorbis = "VORBIS"
)

// NOTE: ensure stays synced with `scan.scanTrackMetadata`
var RecognizedAudioExtensions = map[string]bool{
	/* MP4 */
	".m4a": true,
	".m4p": true,
	".m4v": true,
	/* VORBIS */
	".flac": true,
	".oga":  true,
	".ogg":  true,
	".opus": true,
	/* MP3 ID3{v1,v2.2,v2.3,v2.4}*/
	".mp3": true,
}

var RecognizedPlaylistExtensions = map[string]bool{
	".m3u":  true,
	".m3u8": true,
	".pls":  true,
}

var RecognizedSidecarExtensions = map[string]bool{
	".jpg":  true,
	".jpeg": true,
	".png":  true,
	".webp": true,
	".gif":  true,
	".pdf":  true,
}

type artworkPictureType int

const (
	artworkPictureTypeUnspecified artworkPictureType = iota - 1
	artworkPictureTypeOther
	artworkPictureTypeFileIcon
	artworkPictureTypeOtherFileIcon
	artworkPictureTypeFrontCover
	artworkPictureTypeBackCover
	artworkPictureTypeLeafletPage
	artworkPictureTypeMedia
	artworkPictureTypeLeadArtist
	artworkPictureTypeArtist
	artworkPictureTypeConductor
	artworkPictureTypeBand
	artworkPictureTypeComposer
	artworkPictureTypeLyricist
	artworkPictureTypeRecordingLocation
	artworkPictureTypeDuringRecording
	artworkPictureTypeDuringPerformance
	artworkPictureTypeVideoScreenCapture
	artworkPictureTypeBrightColoredFish
	artworkPictureTypeIllustration
	artworkPictureTypeBandArtistLogo
	artworkPictureTypePublisherStudioLogo
)

func splitGenreFieldList(splitTokens map[string]bool, value string) []string {
	value = cleanTagText(strings.ReplaceAll(value, "\x00", ";"))
	if value == "" {
		return []string{}
	}

	parts := strings.FieldsFunc(value, func(r rune) bool {
		return splitTokens[string(r)]
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

type artwork struct {
	PictureType artworkPictureType
	MIMEType    string
	Data        []byte
}

func normalizeArtworkPictureType(raw uint32) artworkPictureType {
	if raw <= uint32(artworkPictureTypePublisherStudioLogo) {
		return artworkPictureType(raw)
	}
	return artworkPictureTypeUnspecified
}

func artworkPictureTypeString(pictureType artworkPictureType) string {
	switch pictureType {
	case artworkPictureTypeOther:
		return "other"
	case artworkPictureTypeFileIcon:
		return "file_icon"
	case artworkPictureTypeOtherFileIcon:
		return "other_file_icon"
	case artworkPictureTypeFrontCover:
		return "front_cover"
	case artworkPictureTypeBackCover:
		return "back_cover"
	case artworkPictureTypeLeafletPage:
		return "leaflet_page"
	case artworkPictureTypeMedia:
		return "media"
	case artworkPictureTypeLeadArtist:
		return "lead_artist"
	case artworkPictureTypeArtist:
		return "artist"
	case artworkPictureTypeConductor:
		return "conductor"
	case artworkPictureTypeBand:
		return "band"
	case artworkPictureTypeComposer:
		return "composer"
	case artworkPictureTypeLyricist:
		return "lyricist"
	case artworkPictureTypeRecordingLocation:
		return "recording_location"
	case artworkPictureTypeDuringRecording:
		return "during_recording"
	case artworkPictureTypeDuringPerformance:
		return "during_performance"
	case artworkPictureTypeVideoScreenCapture:
		return "video_screen_capture"
	case artworkPictureTypeBrightColoredFish:
		return "bright_colored_fish"
	case artworkPictureTypeIllustration:
		return "illustration"
	case artworkPictureTypeBandArtistLogo:
		return "band_artist_logo"
	case artworkPictureTypePublisherStudioLogo:
		return "publisher_studio_logo"
	default:
		return "unspecified"
	}
}

func sniffImageMIMEType(data []byte) string {
	if len(data) >= 12 && string(data[:4]) == "RIFF" && string(data[8:12]) == "WEBP" {
		return "image/webp"
	}
	if mime := http.DetectContentType(data); strings.HasPrefix(mime, "image/") {
		return mime
	}
	return ""
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
	return result
}

// Some formats and encoders pad text fields with trailing NUL bytes; trim those
// before the usual whitespace cleanup so callers see the intended tag value.
func cleanTagText(value string) string {
	return strings.TrimSpace(strings.TrimRight(value, "\x00"))
}

func normalizeDescriptor(value string) string {
	value = strings.ToLower(strings.TrimSpace(value))
	replacer := strings.NewReplacer(
		" ", "",
		"_", "",
		"-", "",
		".", "",
		"/", "",
	)
	return replacer.Replace(value)
}

func setIfZero[T comparable](target *T, value T) {
	var zero T
	if *target == zero && value != zero {
		*target = value
	}
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
