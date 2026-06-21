package scan

import (
	"bytes"
	"encoding/base64"
	"encoding/binary"
	"fmt"
	"io"
	"strings"
)

type vorbisCommentMap map[string][]string

type vorbisMetadata struct {
	Comments vorbisCommentMap
	Artwork  *embeddedArtwork
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
		for commentKey, got := range comments {
			if seenKeys[commentKey] || normalizeDescriptor(commentKey) != normalized {
				continue
			}
			values = append(values, got...)
			seenKeys[commentKey] = true
		}
	}
	return values
}

var (
	oggVorbisCommentPrefix = []byte{0x03, 'v', 'o', 'r', 'b', 'i', 's'}
	oggOpusTagsPrefix      = []byte("OpusTags")
)

func readVorbisComments(r io.ReadSeeker) (vorbisCommentMap, error) {
	metadata, err := readVorbisMetadata(r)
	return metadata.Comments, err
}

func readVorbisMetadata(r io.ReadSeeker) (vorbisMetadata, error) {
	if _, err := r.Seek(0, io.SeekStart); err != nil {
		return vorbisMetadata{}, err
	}

	var magic [4]byte
	if _, err := io.ReadFull(r, magic[:]); err != nil {
		return vorbisMetadata{}, err
	}

	switch string(magic[:]) {
	case "fLaC":
		return readFLACVorbisMetadata(r)
	case "OggS":
		if _, err := r.Seek(0, io.SeekStart); err != nil {
			return vorbisMetadata{}, err
		}
		return readOggVorbisMetadata(r)
	default:
		return vorbisMetadata{}, fmt.Errorf("unsupported Vorbis comment container %q", string(magic[:]))
	}
}

func readFLACVorbisComments(r io.Reader) (vorbisCommentMap, error) {
	metadata, err := readFLACVorbisMetadata(r)
	return metadata.Comments, err
}

func readFLACVorbisMetadata(r io.Reader) (vorbisMetadata, error) {
	var metadata vorbisMetadata
	foundComments := false

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
			data := make([]byte, blockLen)
			if _, err := io.ReadFull(r, data); err != nil {
				return vorbisMetadata{}, err
			}
			if metadata.Artwork == nil {
				artwork, err := parseVorbisPictureBlock(data)
				if err != nil {
					return vorbisMetadata{}, err
				}
				metadata.Artwork = artwork
			}
		default:
			if _, err := io.CopyN(io.Discard, r, int64(blockLen)); err != nil {
				return vorbisMetadata{}, err
			}
		}

		if last {
			if !foundComments {
				return vorbisMetadata{}, fmt.Errorf("FLAC Vorbis comment block not found")
			}
			if metadata.Artwork == nil {
				metadata.Artwork = vorbisArtworkFromComments(metadata.Comments)
			}
			return metadata, nil
		}
	}
}

func readOggVorbisComments(r io.Reader) (vorbisCommentMap, error) {
	metadata, err := readOggVorbisMetadata(r)
	return metadata.Comments, err
}

func readOggVorbisMetadata(r io.Reader) (vorbisMetadata, error) {
	var packet []byte
	for {
		var header [27]byte
		if _, err := io.ReadFull(r, header[:]); err != nil {
			if err == io.EOF || err == io.ErrUnexpectedEOF {
				return vorbisMetadata{}, fmt.Errorf("Ogg Vorbis comment packet not found")
			}
			return vorbisMetadata{}, err
		}
		if string(header[:4]) != "OggS" {
			return vorbisMetadata{}, fmt.Errorf("invalid Ogg page capture pattern %q", string(header[:4]))
		}

		segmentCount := int(header[26])
		segments := make([]byte, segmentCount)
		if _, err := io.ReadFull(r, segments); err != nil {
			return vorbisMetadata{}, err
		}

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
			if n == 255 {
				continue
			}

			comments, ok, err := parseOggVorbisCommentPacket(packet)
			if err != nil {
				return vorbisMetadata{}, err
			}
			if ok {
				return vorbisMetadata{
					Comments: comments,
					Artwork:  vorbisArtworkFromComments(comments),
				}, nil
			}
			packet = packet[:0]
		}
	}
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

func parseVorbisCommentPayload(data []byte) (vorbisCommentMap, error) {
	r := bytes.NewReader(data)
	vendorLen, err := readLittleEndianUint32(r)
	if err != nil {
		return nil, err
	}
	if _, err := r.Seek(int64(vendorLen), io.SeekCurrent); err != nil {
		return nil, err
	}

	commentCount, err := readLittleEndianUint32(r)
	if err != nil {
		return nil, err
	}

	comments := make(vorbisCommentMap)
	for i := uint32(0); i < commentCount; i++ {
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
		key = strings.ToLower(key)
		comments[key] = append(comments[key], value)
	}

	return comments, nil
}

func vorbisArtworkFromComments(comments vorbisCommentMap) *embeddedArtwork {
	for _, value := range comments.values("metadata_block_picture") {
		data, err := base64.StdEncoding.DecodeString(strings.TrimSpace(value))
		if err != nil {
			continue
		}
		artwork, err := parseVorbisPictureBlock(data)
		if err == nil && artwork != nil {
			return artwork
		}
	}
	return nil
}

func parseVorbisPictureBlock(data []byte) (*embeddedArtwork, error) {
	r := bytes.NewReader(data)
	if _, err := readBigEndianUint32(r); err != nil {
		return nil, err
	}

	mimeLen, err := readBigEndianUint32(r)
	if err != nil {
		return nil, err
	}
	if uint64(mimeLen) > uint64(r.Len()) {
		return nil, fmt.Errorf("Vorbis picture MIME length exceeds block")
	}
	mime := make([]byte, mimeLen)
	if _, err := io.ReadFull(r, mime); err != nil {
		return nil, err
	}

	descriptionLen, err := readBigEndianUint32(r)
	if err != nil {
		return nil, err
	}
	if uint64(descriptionLen) > uint64(r.Len()) {
		return nil, fmt.Errorf("Vorbis picture description length exceeds block")
	}
	if _, err := io.CopyN(io.Discard, r, int64(descriptionLen)); err != nil {
		return nil, err
	}
	if _, err := io.CopyN(io.Discard, r, 16); err != nil {
		return nil, err
	}

	imageLen, err := readBigEndianUint32(r)
	if err != nil {
		return nil, err
	}
	if uint64(imageLen) > uint64(r.Len()) {
		return nil, fmt.Errorf("Vorbis picture data length exceeds block")
	}
	image := make([]byte, imageLen)
	if _, err := io.ReadFull(r, image); err != nil {
		return nil, err
	}
	return &embeddedArtwork{
		MIMEType: string(mime),
		Data:     image,
	}, nil
}

func readBigEndianUint32(r io.Reader) (uint32, error) {
	var b [4]byte
	if _, err := io.ReadFull(r, b[:]); err != nil {
		return 0, err
	}
	return binary.BigEndian.Uint32(b[:]), nil
}
