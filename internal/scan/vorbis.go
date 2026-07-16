package scan

import (
	"bytes"
	"encoding/base64"
	"encoding/binary"
	"fmt"
	"io"
	"strings"

	"github.com/zanyoats/kukicha/internal/app"
)

const (
	flacMagic = "fLaC"
	oggMagic  = "OggS"
)

var (
	oggVorbisCommentPrefix = []byte{0x03, 'v', 'o', 'r', 'b', 'i', 's'}
	oggOpusTagsPrefix      = []byte("OpusTags")
)

type vorbisCommentMap map[string][]string

type vorbisMetadata struct {
	Comments vorbisCommentMap
	Artwork  []artwork
}

func (comments vorbisCommentMap) first(keys ...string) string {
	for _, value := range comments.values(keys...) {
		if value := cleanTagText(value); value != "" {
			return value
		}
	}
	return ""
}

func (comments vorbisCommentMap) firstInt(keys ...string) int {
	if value, ok := parseIntText(comments.first(keys...)); ok {
		return value
	}
	return 0
}

func (comments vorbisCommentMap) firstInt64(keys ...string) int64 {
	if value, ok := parseInt64Text(comments.first(keys...)); ok {
		return value
	}
	return 0
}

func (comments vorbisCommentMap) firstBool(keys ...string) (bool, bool) {
	return parseBoolText(comments.first(keys...))
}

func (comments vorbisCommentMap) values(keys ...string) []string {
	var values []string
	seenKeys := make(map[string]bool, len(keys))
	for _, key := range keys {
		key = strings.ToLower(key)
		if got := comments[key]; len(got) > 0 {
			if !seenKeys[key] {
				values = append(values, got...)
				seenKeys[key] = true
			}
			continue
		}

		normalized := normalizeDescriptor(key)
		for vorbisKey, got := range comments {
			if seenKeys[vorbisKey] || normalizeDescriptor(vorbisKey) != normalized {
				continue
			}
			values = append(values, got...)
			seenKeys[vorbisKey] = true
		}
	}
	return values
}

func applyVorbisTrackFields(scannedFile *app.ScannedFile, comments vorbisCommentMap) {
	record := scannedFile.Metadata

	if len(comments) == 0 {
		return
	}
	if values := comments.values("artist"); len(values) > 0 {
		record.Artists = cleanStringValues(values...)
	}
	if values := comments.values("artistsort", "artist_sort", "artist sort"); len(values) > 0 {
		record.ArtistSort = cleanStringValues(values...)
	}
	if values := comments.values("albumartist", "album_artist", "album artist"); len(values) > 0 {
		record.AlbumArtists = cleanStringValues(values...)
	}
	if values := comments.values("albumartistsort", "album_artist_sort", "album artist sort"); len(values) > 0 {
		record.AlbumArtistSort = cleanStringValues(values...)
	}
	if values := comments.values("performer", "performers"); len(values) > 0 {
		record.Performers = cleanStringValues(values...)
	}
	setIfZero(&record.Composer, comments.first("composer"))
	setIfZero(&record.Album, comments.first("album"))
	setIfZero(&record.AlbumSort, comments.first("albumsort", "album_sort", "album sort"))
	setIfZero(&record.Title, comments.first("title"))
	setIfZero(&record.Work, comments.first("work"))
	setIfZero(&record.Grouping, comments.first("grouping", "contentgroup", "content_group"))
	setIfZero(&record.MovementName, comments.first("movementname", "movement_name", "movement name"))
	setIfZero(&record.MovementNumber, comments.firstInt("movementnumber", "movement_number", "movement number"))
	setIfZero(&record.MovementCount, comments.firstInt("movementcount", "movement_count", "movementtotal", "movement_total"))
	if value, ok := comments.firstBool("showmovement", "show_movement", "show movement"); ok {
		record.ShowMovement = value
	}
	setIfZero(&record.TrackNumber, comments.firstInt("tracknumber", "track_number"))
	setIfZero(&record.TrackCount, comments.firstInt("tracktotal", "track_total", "totaltracks", "total_tracks", "trackcount", "track_count"))
	setIfZero(&record.DiscNumber, comments.firstInt("discnumber", "disc_number"))
	setIfZero(&record.DiscCount, comments.firstInt("disctotal", "disc_total", "totaldiscs", "total_discs", "disccount", "disc_count"))
	setIfZero(&record.ReleaseDate, comments.first("releasedate", "release_date", "date", "year"))
	setIfZero(&record.OriginalReleaseDate, comments.first("originalreleasedate", "original_release_date", "originaldate", "original_date", "originalyear", "original_year"))
	setIfZero(&record.RecordedDate, comments.first("recordeddate", "recorded_date", "recordingdate", "recording_date"))
	setIfZero(&record.OriginalReleaseYear, yearFromDate(record.OriginalReleaseDate))
	setIfZero(&record.ItunesStoreTrackId, comments.firstInt64("itunesstoretrackid", "itunes_store_track_id", "cnid"))
	setIfZero(&record.ItunesStoreAlbumId, comments.firstInt64("itunesstorealbumid", "itunes_store_album_id", "plid"))
	setIfZero(&record.PurchaseDate, comments.first("purchasedate", "purchase_date"))
	if value, ok := comments.firstBool("compilation", "iscompilation", "is_compilation", "itunescompilation"); ok {
		record.IsCompilation = &value
	}
	if genres := comments.values("genre"); len(genres) > 0 {
		record.Genres = genres
	}
}

// readVorbisMetadata sniffs the container and then lets the container-specific
// reader find the Vorbis comment payload.
//
// FLAC keeps metadata before the audio frames:
//
//	"fLaC"
//	metadata block: STREAMINFO
//	metadata block: VORBIS_COMMENT -> vendor + key/value comments
//	metadata block: PICTURE        -> optional artwork
//	metadata block: ...
//	audio frames
//
// Ogg keeps both metadata and audio inside "OggS" pages. Packets can span page
// boundaries, so this is the logical packet order rather than a promise that
// each row is exactly one page:
//
//	"OggS" page(s): identification/header packet
//	"OggS" page(s): Vorbis comment packet, or OpusTags packet
//	"OggS" page(s): setup packet (Vorbis only)
//	"OggS" page(s): audio packets
func readVorbisMetadata(r io.ReadSeeker) (vorbisMetadata, error) {
	if _, err := r.Seek(0, io.SeekStart); err != nil {
		return vorbisMetadata{}, err
	}

	var magic [4]byte
	if _, err := io.ReadFull(r, magic[:]); err != nil {
		return vorbisMetadata{}, err
	}

	switch string(magic[:]) {
	case flacMagic:
		return readFLACVorbisMetadata(r)
	case oggMagic:
		// FLAC parsing starts after the "fLaC" marker, but Ogg parsing expects
		// to read the "OggS" capture pattern as part of the first page header.
		if _, err := r.Seek(0, io.SeekStart); err != nil {
			return vorbisMetadata{}, err
		}
		return readOggVorbisMetadata(r)
	default:
		return vorbisMetadata{}, fmt.Errorf("unsupported Vorbis comment container %q", string(magic[:]))
	}
}

func readFLACVorbisMetadata(r io.Reader) (vorbisMetadata, error) {
	var metadata vorbisMetadata
	foundComments := false

	// readVorbisMetadata has already consumed the "fLaC" marker, so the reader
	// is positioned at the first FLAC metadata block. Each block starts with a
	// four-byte header: 1 last-block bit, 7 block-type bits, then a 24-bit
	// big-endian payload length.
	for {
		var header [4]byte
		if _, err := io.ReadFull(r, header[:]); err != nil {
			return vorbisMetadata{}, err
		}

		last := header[0]&0x80 != 0
		blockType := header[0] & 0x7f
		blockLen := int(header[1])<<16 | int(header[2])<<8 | int(header[3])

		switch blockType {
		case 4:
			// VORBIS_COMMENT stores the tag payload directly in the FLAC block.
			// The block envelope is big-endian, but the Vorbis comment payload
			// itself uses little-endian lengths for the vendor string and comments.
			data := make([]byte, blockLen)
			if _, err := io.ReadFull(r, data); err != nil {
				return vorbisMetadata{}, err
			}
			comments, err := parseVorbisCommentPayload(data)
			if err != nil {
				return vorbisMetadata{}, err
			}
			metadata.Comments = comments
			foundComments = true

		case 6:
			// PICTURE blocks carry embedded artwork in the same binary structure
			// used by the METADATA_BLOCK_PICTURE Vorbis comment tag.
			data := make([]byte, blockLen)
			if _, err := io.ReadFull(r, data); err != nil {
				return vorbisMetadata{}, err
			}
			artwork, err := parseVorbisPictureBlock(data)
			if err != nil {
				return vorbisMetadata{}, err
			}
			metadata.Artwork = append(metadata.Artwork, artwork)

		default:
			// Other metadata block types, such as STREAMINFO, SEEKTABLE, and
			// PADDING, are not needed for library metadata.
			if _, err := io.CopyN(io.Discard, r, int64(blockLen)); err != nil {
				return vorbisMetadata{}, err
			}
		}

		if last {
			// The last metadata block is followed by audio frames, so stop here
			// instead of reading into the audio stream.
			if !foundComments {
				return vorbisMetadata{}, fmt.Errorf("FLAC Vorbis comment block not found")
			}
			if len(metadata.Artwork) == 0 {
				// Some encoders put cover art in a base64-encoded
				// METADATA_BLOCK_PICTURE tag instead of a dedicated PICTURE block.
				vorbisArtworkFromComments(&metadata)
			}
			return metadata, nil
		}
	}
}

func readOggVorbisMetadata(r io.Reader) (vorbisMetadata, error) {
	var packet []byte
	for {
		// Ogg stores data in pages. The fixed 27-byte page header starts with
		// "OggS"; its last byte tells us how many lacing values follow.
		var header [27]byte
		if _, err := io.ReadFull(r, header[:]); err != nil {
			if err == io.EOF || err == io.ErrUnexpectedEOF {
				return vorbisMetadata{}, fmt.Errorf("Ogg Vorbis comment packet not found")
			}
			return vorbisMetadata{}, err
		}
		if string(header[:4]) != oggMagic {
			return vorbisMetadata{}, fmt.Errorf("invalid Ogg page capture pattern %q", string(header[:4]))
		}

		// The segment table is a list of lacing values. Each value is the byte
		// length of the matching chunk in the page payload.
		segmentCount := int(header[26])
		segments := make([]byte, segmentCount)
		if _, err := io.ReadFull(r, segments); err != nil {
			return vorbisMetadata{}, err
		}

		// The page payload follows the segment table and is exactly the sum of
		// those lacing values.
		pageLen := 0
		for _, segmentLen := range segments {
			pageLen += int(segmentLen)
		}
		pageData := make([]byte, pageLen)
		if _, err := io.ReadFull(r, pageData); err != nil {
			return vorbisMetadata{}, err
		}

		offset := 0
		for _, segmentLen := range segments {
			n := int(segmentLen)
			packet = append(packet, pageData[offset:offset+n]...)
			offset += n
			// A lacing value of 255 means this packet continues into the next
			// segment. A value below 255 terminates the current packet.
			if n == 255 {
				continue
			}

			// The first complete packet is usually the identification header; keep
			// scanning until the completed packet is the Vorbis comment payload.
			comments, ok, err := parseOggVorbisCommentPacket(packet)
			if err != nil {
				return vorbisMetadata{}, err
			}
			if ok {
				vorbisMetadata := vorbisMetadata{Comments: comments}
				// Ogg Vorbis/Opus cover art is stored as METADATA_BLOCK_PICTURE
				// tags inside the comment packet, not as separate Ogg page types.
				vorbisArtworkFromComments(&vorbisMetadata)
				return vorbisMetadata, nil
			}
			packet = packet[:0]
		}
	}
}

func parseVorbisCommentPayload(data []byte) (vorbisCommentMap, error) {
	r := bytes.NewReader(data)
	// A Vorbis comment payload starts with the encoder/vendor string. Its
	// length is little-endian even when the payload lives inside a FLAC metadata
	// block, because this inner structure is still the Vorbis comment format.
	vendorLen, err := readLittleEndianUint32(r)
	if err != nil {
		return nil, err
	}
	if _, err := r.Seek(int64(vendorLen), io.SeekCurrent); err != nil {
		return nil, err
	}

	// After the vendor string comes a little-endian count of user comments.
	// Each comment is another length-prefixed UTF-8 string.
	commentCount, err := readLittleEndianUint32(r)
	if err != nil {
		return nil, err
	}

	comments := make(vorbisCommentMap)
	for i := uint32(0); i < commentCount; i++ {
		// The comment body is normally KEY=value. Multiple entries may use the
		// same key, so the map stores a slice instead of replacing earlier tags.
		commentLen, err := readLittleEndianUint32(r)
		if err != nil {
			return nil, err
		}
		comment := make([]byte, commentLen)
		if _, err := io.ReadFull(r, comment); err != nil {
			return nil, err
		}

		key, value, ok := strings.Cut(string(comment), "=")
		if !ok {
			return nil, fmt.Errorf("invalid Vorbis comment %q", string(comment))
		}
		// Tag field names are case-insensitive in practice; normalize them so
		// later lookup code can ask for "artist", "album", and similar keys.
		key = strings.ToLower(key)
		comments[key] = append(comments[key], value)
	}

	return comments, nil
}

func parseOggVorbisCommentPacket(packet []byte) (vorbisCommentMap, bool, error) {
	switch {
	case bytes.HasPrefix(packet, oggVorbisCommentPrefix):
		comments, err := parseVorbisCommentPayload(packet[len(oggVorbisCommentPrefix):])
		return comments, true, err
	case bytes.HasPrefix(packet, oggOpusTagsPrefix):
		comments, err := parseVorbisCommentPayload(packet[len(oggOpusTagsPrefix):])
		return comments, true, err
	default:
		return nil, false, nil
	}
}

func vorbisArtworkFromComments(vorbisMetadata *vorbisMetadata) {
	for _, value := range vorbisMetadata.Comments.values("metadata_block_picture") {
		data, err := base64.StdEncoding.DecodeString(strings.TrimSpace(value))
		if err != nil {
			continue
		}
		artwork, err := parseVorbisPictureBlock(data)
		if err == nil {
			vorbisMetadata.Artwork = append(vorbisMetadata.Artwork, artwork)
		}
	}
}

func parseVorbisPictureBlock(data []byte) (artwork artwork, err error) {
	r := bytes.NewReader(data)
	// Vorbis METADATA_BLOCK_PICTURE uses the FLAC picture block layout. Unlike
	// Vorbis comments, every integer in this structure is big-endian.
	// The first field is the picture type, such as front cover or artist photo;
	// this scanner keeps the first usable image regardless of type.
	rawPictureType, err := readBigEndianUint32(r)
	if err != nil {
		return
	}
	pictureType := normalizeArtworkPictureType(rawPictureType)

	// The MIME type is a length-prefixed string, usually "image/jpeg" or
	// "image/png", and is kept alongside the raw image bytes.
	mimeLen, err := readBigEndianUint32(r)
	if err != nil {
		return
	}
	if uint64(mimeLen) > uint64(r.Len()) {
		err = fmt.Errorf("Vorbis picture MIME length exceeds block")
		return
	}
	mime := make([]byte, mimeLen)
	if _, err = io.ReadFull(r, mime); err != nil {
		return
	}

	// The next field is a length-prefixed description string. It is useful for
	// humans, but the library model only needs the MIME type and image bytes.
	descriptionLen, err := readBigEndianUint32(r)
	if err != nil {
		return
	}
	if uint64(descriptionLen) > uint64(r.Len()) {
		err = fmt.Errorf("Vorbis picture description length exceeds block")
		return
	}
	if _, err = io.CopyN(io.Discard, r, int64(descriptionLen)); err != nil {
		return
	}
	// Skip width, height, color depth, and indexed color count. They are four
	// big-endian uint32 fields that describe the image but are not needed here.
	if _, err = io.CopyN(io.Discard, r, 16); err != nil {
		return
	}

	// The final length-prefixed field is the encoded image data itself.
	imageLen, err := readBigEndianUint32(r)
	if err != nil {
		return
	}
	if uint64(imageLen) > uint64(r.Len()) {
		err = fmt.Errorf("Vorbis picture data length exceeds block")
		return
	}
	image := make([]byte, imageLen)
	if _, err = io.ReadFull(r, image); err != nil {
		return
	}

	artwork.PictureType = pictureType
	artwork.MIMEType = string(mime)
	artwork.Data = image

	return
}

func readBigEndianUint32(r io.Reader) (uint32, error) {
	var b [4]byte
	if _, err := io.ReadFull(r, b[:]); err != nil {
		return 0, err
	}
	return binary.BigEndian.Uint32(b[:]), nil
}

func readLittleEndianUint32(r io.Reader) (uint32, error) {
	var b [4]byte
	if _, err := io.ReadFull(r, b[:]); err != nil {
		return 0, err
	}
	return binary.LittleEndian.Uint32(b[:]), nil
}
