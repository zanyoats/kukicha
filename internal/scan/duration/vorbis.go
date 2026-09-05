package duration

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"io"
)

const (
	flacMagic = "fLaC"
	oggMagic  = "OggS"
)

var (
	oggVorbisIdentificationPrefix = []byte{0x01, 'v', 'o', 'r', 'b', 'i', 's'}
	oggOpusHeadPrefix             = []byte("OpusHead")
)

type oggAudioCodec int

const (
	oggAudioCodecUnknown oggAudioCodec = iota
	oggAudioCodecVorbis
	oggAudioCodecOpus
)

type oggAudioIdentification struct {
	Codec      oggAudioCodec
	SampleRate uint32
	PreSkip    uint16
}

// ReadVorbis returns the duration in whole seconds for a FLAC or Ogg
// Vorbis/Opus stream.
func ReadVorbis(r io.ReadSeeker) (int64, error) {
	if _, err := r.Seek(0, io.SeekStart); err != nil {
		return 0, err
	}

	var magic [4]byte
	if _, err := io.ReadFull(r, magic[:]); err != nil {
		return 0, err
	}

	switch string(magic[:]) {
	case flacMagic:
		return readFLAC(r)
	case oggMagic:
		return readOgg(r)
	default:
		return 0, fmt.Errorf(
			"unsupported Vorbis duration container %q",
			string(magic[:]),
		)
	}
}

func readFLAC(r io.Reader) (int64, error) {
	for {
		var header [4]byte
		if _, err := io.ReadFull(r, header[:]); err != nil {
			return 0, err
		}

		last := header[0]&0x80 != 0
		blockType := header[0] & 0x7f
		blockLen := int(header[1])<<16 | int(header[2])<<8 | int(header[3])
		if blockType == 0 {
			data := make([]byte, blockLen)
			if _, err := io.ReadFull(r, data); err != nil {
				return 0, err
			}
			return parseFLACStreamInfo(data)
		}

		if _, err := io.CopyN(io.Discard, r, int64(blockLen)); err != nil {
			return 0, err
		}
		if last {
			return 0, fmt.Errorf("FLAC STREAMINFO block not found")
		}
	}
}

func parseFLACStreamInfo(data []byte) (int64, error) {
	if len(data) != 34 {
		return 0, fmt.Errorf("invalid FLAC STREAMINFO length %d", len(data))
	}

	// STREAMINFO packs sample rate (20 bits), channel count minus one (3),
	// bits per sample minus one (5), and total samples (36) into eight bytes.
	packed := binary.BigEndian.Uint64(data[10:18])
	sampleRate := packed >> 44
	totalSamples := packed & ((uint64(1) << 36) - 1)
	return truncateDurationSeconds(totalSamples, sampleRate)
}

func readOgg(r io.ReadSeeker) (int64, error) {
	end, err := r.Seek(0, io.SeekEnd)
	if err != nil {
		return 0, err
	}
	if _, err := r.Seek(0, io.SeekStart); err != nil {
		return 0, err
	}

	packets := make(map[uint32][]byte)
	var identification oggAudioIdentification
	var selectedSerial uint32
	selected := false
	var lastGranule uint64
	foundGranule := false

	for {
		pageStart, err := r.Seek(0, io.SeekCurrent)
		if err != nil {
			return 0, err
		}
		if pageStart == end {
			break
		}
		if end-pageStart < 27 {
			return 0, fmt.Errorf("short Ogg page header")
		}

		var header [27]byte
		if _, err := io.ReadFull(r, header[:]); err != nil {
			return 0, err
		}
		if string(header[:4]) != oggMagic {
			return 0, fmt.Errorf(
				"invalid Ogg page capture pattern %q",
				string(header[:4]),
			)
		}
		if header[4] != 0 {
			return 0, fmt.Errorf("unsupported Ogg stream version %d", header[4])
		}

		serial := binary.LittleEndian.Uint32(header[14:18])
		granule := binary.LittleEndian.Uint64(header[6:14])
		segmentCount := int(header[26])
		segments := make([]byte, segmentCount)
		if _, err := io.ReadFull(r, segments); err != nil {
			return 0, err
		}

		pageLen := 0
		for _, segmentLen := range segments {
			pageLen += int(segmentLen)
		}
		payloadStart, err := r.Seek(0, io.SeekCurrent)
		if err != nil {
			return 0, err
		}
		if int64(pageLen) > end-payloadStart {
			return 0, fmt.Errorf("Ogg page payload exceeds file")
		}

		if !selected {
			pageData := make([]byte, pageLen)
			if _, err := io.ReadFull(r, pageData); err != nil {
				return 0, err
			}

			packet := packets[serial]
			offset := 0
			for _, segmentLen := range segments {
				n := int(segmentLen)
				packet = append(packet, pageData[offset:offset+n]...)
				offset += n
				if n == 255 {
					continue
				}

				if !selected {
					candidate, ok, err := parseOggAudioIdentification(packet)
					if err != nil {
						return 0, err
					}
					if ok {
						identification = candidate
						selectedSerial = serial
						selected = true
					}
				}
				packet = packet[:0]
			}
			packets[serial] = packet
		} else if _, err := r.Seek(int64(pageLen), io.SeekCurrent); err != nil {
			return 0, err
		}

		if selected && serial == selectedSerial && granule != ^uint64(0) {
			lastGranule = granule
			foundGranule = true
		}
		if selected && serial == selectedSerial && header[5]&0x04 != 0 {
			break
		}
	}

	if !selected {
		return 0, fmt.Errorf("Ogg Vorbis/Opus identification packet not found")
	}
	if !foundGranule {
		return 0, fmt.Errorf("Ogg audio granule position not found")
	}

	switch identification.Codec {
	case oggAudioCodecVorbis:
		return truncateDurationSeconds(
			lastGranule,
			uint64(identification.SampleRate),
		)
	case oggAudioCodecOpus:
		if lastGranule < uint64(identification.PreSkip) {
			return 0, fmt.Errorf("Ogg Opus granule position precedes pre-skip")
		}
		return truncateDurationSeconds(
			lastGranule-uint64(identification.PreSkip),
			48000,
		)
	default:
		return 0, fmt.Errorf("unsupported Ogg audio codec")
	}
}

func parseOggAudioIdentification(
	packet []byte,
) (oggAudioIdentification, bool, error) {
	switch {
	case bytes.HasPrefix(packet, oggVorbisIdentificationPrefix):
		if len(packet) < 16 {
			return oggAudioIdentification{}, false, fmt.Errorf(
				"short Ogg Vorbis identification packet",
			)
		}
		sampleRate := binary.LittleEndian.Uint32(packet[12:16])
		if sampleRate == 0 {
			return oggAudioIdentification{}, false, fmt.Errorf(
				"Ogg Vorbis sample rate is zero",
			)
		}
		return oggAudioIdentification{
			Codec:      oggAudioCodecVorbis,
			SampleRate: sampleRate,
		}, true, nil

	case bytes.HasPrefix(packet, oggOpusHeadPrefix):
		if len(packet) < 19 {
			return oggAudioIdentification{}, false, fmt.Errorf(
				"short Ogg Opus identification packet",
			)
		}
		return oggAudioIdentification{
			Codec:   oggAudioCodecOpus,
			PreSkip: binary.LittleEndian.Uint16(packet[10:12]),
		}, true, nil

	default:
		return oggAudioIdentification{}, false, nil
	}
}
