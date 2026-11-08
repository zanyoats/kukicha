package duration

import (
	"bufio"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
)

const mp3FrameProbeBytes = 1024 * 1024 // Limit initial frame search to 1 MiB

// ErrIncompleteFinalMP3Frame indicates that duration includes only the complete
// frames preceding an incomplete final frame. The returned duration is usable.
var ErrIncompleteFinalMP3Frame = errors.New("incomplete final MP3 frame ignored")

type mpegAudioFrameHeader struct {
	Version         int  // MPEG-1, MPEG-2, or MPEG-2.5
	Bitrate         int  // bits/sec
	SampleRate      int  // samples/sec
	SamplesPerFrame int  // 1152 for MPEG-1, 576 for MPEG-2/2.5
	FrameSize       int  // total bytes in frame
	Mono            bool // mono has smaller sections
}

// ReadMP3 returns the MPEG audio duration in whole seconds.
// With ErrIncompleteFinalMP3Frame, it returns the duration of complete frames.
func ReadMP3(r io.ReadSeeker) (int64, error) {
	audioStart, audioEnd, err := mp3AudioBounds(r)
	if err != nil {
		return 0, err
	}

	firstFrameOffset, firstHeader, err := findFirstMP3Frame(
		r,
		audioStart,
		audioEnd,
	)
	if err != nil {
		return 0, err
	}

	firstFrame := make([]byte, firstHeader.FrameSize)
	if err := readMP3BytesAt(r, firstFrameOffset, firstFrame); err != nil {
		return 0, err
	}
	if samples, ok := parseMP3VBRDurationSamples(firstFrame, firstHeader); ok {
		return truncateDurationSeconds(samples, uint64(firstHeader.SampleRate))
	}

	totalSamples, sampleRate, err := countMP3FrameSamples(
		r,
		firstFrameOffset,
		audioEnd,
		firstHeader,
	)
	if err != nil && !errors.Is(err, ErrIncompleteFinalMP3Frame) {
		return 0, err
	}
	seconds, durationErr := truncateDurationSeconds(totalSamples, uint64(sampleRate))
	if durationErr != nil {
		return 0, durationErr
	}
	return seconds, err
}

// returns the byte range [start, end) of which contains the MPEG frames.
func mp3AudioBounds(r io.ReadSeeker) (int64, int64, error) {
	end, err := r.Seek(0, io.SeekEnd)
	if err != nil {
		return 0, 0, err
	}
	start := int64(0)

	if end >= 10 {
		var header [10]byte
		if err := readMP3BytesAt(r, 0, header[:]); err != nil {
			return 0, 0, err
		}
		if string(header[:3]) == "ID3" {
			start = 10 + int64(syncsafeUint32(header[6:10]))
			// ID3v2.4 may append a ten-byte footer after the declared tag body.
			if header[3] == 4 && header[5]&0x10 != 0 {
				start += 10
			}
		}
	}

	// ID3v1 is a fixed 128-byte trailer after the MPEG frames. Some files
	// contain consecutive tags; exclude each one from the audio payload.
	for end-start >= 128 {
		var marker [3]byte
		if err := readMP3BytesAt(r, end-128, marker[:]); err != nil {
			return 0, 0, err
		}
		if string(marker[:]) != "TAG" {
			break
		}
		end -= 128
	}

	// An APEv2 footer may appear immediately before ID3v1 or at file end.
	if end >= 32 {
		var footer [32]byte
		if err := readMP3BytesAt(r, end-32, footer[:]); err != nil {
			return 0, 0, err
		}
		if string(footer[:8]) == "APETAGEX" {
			tagSize := int64(binary.LittleEndian.Uint32(footer[12:16]))
			if tagSize < 32 || tagSize > end-start {
				return 0, 0, fmt.Errorf("invalid MP3 APEv2 tag size %d", tagSize)
			}
			end -= tagSize
		}
	}

	if start >= end {
		return 0, 0, fmt.Errorf("MP3 audio payload is empty")
	}
	return start, end, nil
}

func syncsafeUint32(b []byte) uint32 {
	if len(b) < 4 {
		return 0
	}
	return uint32(b[0]&0x7f)<<21 |
		uint32(b[1]&0x7f)<<14 |
		uint32(b[2]&0x7f)<<7 |
		uint32(b[3]&0x7f)
}

func findFirstMP3Frame(
	r io.ReadSeeker,
	start int64,
	end int64,
) (int64, mpegAudioFrameHeader, error) {
	probeLen := end - start
	if probeLen > mp3FrameProbeBytes {
		probeLen = mp3FrameProbeBytes
	}
	probe := make([]byte, int(probeLen))
	if err := readMP3BytesAt(r, start, probe); err != nil {
		return 0, mpegAudioFrameHeader{}, err
	}

	for offset := 0; offset+4 <= len(probe); offset++ {
		header, ok := parseMPEGAudioFrameHeader(probe[offset : offset+4])
		if !ok {
			continue
		}
		absoluteOffset := start + int64(offset)
		frameEnd := absoluteOffset + int64(header.FrameSize)
		if frameEnd > end {
			continue
		}
		if frameEnd == end {
			return absoluteOffset, header, nil
		}

		var nextBytes [4]byte
		if err := readMP3BytesAt(r, frameEnd, nextBytes[:]); err != nil {
			continue
		}
		nextHeader, ok := parseMPEGAudioFrameHeader(nextBytes[:])
		if ok && compatibleMPEGFrameHeaders(header, nextHeader) {
			return absoluteOffset, header, nil
		}
	}

	return 0, mpegAudioFrameHeader{}, fmt.Errorf("MPEG Layer III audio frame not found")
}

func parseMPEGAudioFrameHeader(data []byte) (mpegAudioFrameHeader, bool) {
	if len(data) < 4 {
		return mpegAudioFrameHeader{}, false
	}
	headerBits := binary.BigEndian.Uint32(data[:4])
	if headerBits&0xffe00000 != 0xffe00000 {
		return mpegAudioFrameHeader{}, false
	}

	var version int
	switch (headerBits >> 19) & 0x3 {
	case 3:
		version = 1
	case 2:
		version = 2
	case 0:
		version = 25
	default:
		return mpegAudioFrameHeader{}, false
	}
	// The .mp3 scanner accepts MPEG Layer III, whose layer bits are 01.
	if (headerBits>>17)&0x3 != 1 {
		return mpegAudioFrameHeader{}, false
	}

	bitrateIndex := int((headerBits >> 12) & 0xf)
	if bitrateIndex == 0 || bitrateIndex == 15 {
		return mpegAudioFrameHeader{}, false
	}
	mpeg1Bitrates := [...]int{
		0, 32, 40, 48, 56, 64, 80, 96,
		112, 128, 160, 192, 224, 256, 320,
	}
	mpeg2Bitrates := [...]int{
		0, 8, 16, 24, 32, 40, 48, 56,
		64, 80, 96, 112, 128, 144, 160,
	}
	bitrateKbps := mpeg2Bitrates[bitrateIndex]
	if version == 1 {
		bitrateKbps = mpeg1Bitrates[bitrateIndex]
	}
	bitrate := bitrateKbps * 1000

	sampleRateIndex := int((headerBits >> 10) & 0x3)
	if sampleRateIndex == 3 {
		return mpegAudioFrameHeader{}, false
	}
	sampleRates := [...]int{44100, 48000, 32000}
	sampleRate := sampleRates[sampleRateIndex]
	switch version {
	case 2:
		sampleRate /= 2
	case 25:
		sampleRate /= 4
	}

	coefficient := 72
	samplesPerFrame := 576
	if version == 1 {
		coefficient = 144
		samplesPerFrame = 1152
	}
	padding := int((headerBits >> 9) & 0x1)
	frameSize := coefficient*bitrate/sampleRate + padding
	if frameSize <= 4 {
		return mpegAudioFrameHeader{}, false
	}

	return mpegAudioFrameHeader{
		Version:         version,
		Bitrate:         bitrate,
		SampleRate:      sampleRate,
		SamplesPerFrame: samplesPerFrame,
		FrameSize:       frameSize,
		Mono:            (headerBits>>6)&0x3 == 3,
	}, true
}

func compatibleMPEGFrameHeaders(a, b mpegAudioFrameHeader) bool {
	return a.Version == b.Version &&
		a.SampleRate == b.SampleRate &&
		a.SamplesPerFrame == b.SamplesPerFrame
}

func parseMP3VBRDurationSamples(
	frame []byte,
	header mpegAudioFrameHeader,
) (uint64, bool) {
	sideInfoSize := 17
	if header.Version == 1 {
		sideInfoSize = 32
	}
	if header.Mono {
		if header.Version == 1 {
			sideInfoSize = 17
		} else {
			sideInfoSize = 9
		}
	}

	xingOffset := 4 + sideInfoSize
	if xingOffset+8 <= len(frame) &&
		(string(frame[xingOffset:xingOffset+4]) == "Xing" ||
			string(frame[xingOffset:xingOffset+4]) == "Info") {
		flags := binary.BigEndian.Uint32(frame[xingOffset+4 : xingOffset+8])
		cursor := xingOffset + 8
		var frameCount uint32
		if flags&0x01 != 0 {
			if cursor+4 > len(frame) {
				return 0, false
			}
			frameCount = binary.BigEndian.Uint32(frame[cursor : cursor+4])
			cursor += 4
		}
		if flags&0x02 != 0 {
			cursor += 4
		}
		if flags&0x04 != 0 {
			cursor += 100
		}
		if flags&0x08 != 0 {
			cursor += 4
		}
		if cursor > len(frame) || frameCount == 0 {
			return 0, false
		}

		totalSamples := uint64(frameCount) * uint64(header.SamplesPerFrame)
		// A LAME/Lavf/Lavc extension stores 12-bit encoder delay and padding
		// values 21 bytes after its nine-byte encoder identifier.
		if cursor+24 <= len(frame) {
			encoder := string(frame[cursor : cursor+4])
			if encoder == "LAME" || encoder == "Lavf" || encoder == "Lavc" {
				packed := uint32(frame[cursor+21])<<16 |
					uint32(frame[cursor+22])<<8 |
					uint32(frame[cursor+23])
				delay := uint64(packed >> 12)
				padding := uint64(packed & 0x0fff)
				if delay+padding <= totalSamples {
					totalSamples -= delay + padding
				}
			}
		}
		return totalSamples, true
	}

	// Fraunhofer VBRI stores its frame count at a fixed position in the first
	// MPEG audio frame.
	vbriOffset := 4 + 32
	if vbriOffset+18 <= len(frame) && string(frame[vbriOffset:vbriOffset+4]) == "VBRI" {
		if binary.BigEndian.Uint16(frame[vbriOffset+4:vbriOffset+6]) != 1 {
			return 0, false
		}
		frameCount := binary.BigEndian.Uint32(frame[vbriOffset+14 : vbriOffset+18])
		if frameCount > 0 {
			return uint64(frameCount) * uint64(header.SamplesPerFrame), true
		}
	}

	return 0, false
}

func countMP3FrameSamples(
	r io.ReadSeeker,
	start int64,
	end int64,
	firstHeader mpegAudioFrameHeader,
) (uint64, int, error) {
	if _, err := r.Seek(start, io.SeekStart); err != nil {
		return 0, 0, err
	}
	reader := bufio.NewReaderSize(r, 64*1024)
	position := start
	var totalSamples uint64
	frameCount := 0

	for position < end {
		if end-position < 4 {
			return 0, 0, fmt.Errorf("trailing bytes after final MP3 frame")
		}
		var headerBytes [4]byte
		if _, err := io.ReadFull(reader, headerBytes[:]); err != nil {
			return 0, 0, err
		}
		header, ok := parseMPEGAudioFrameHeader(headerBytes[:])
		if !ok || !compatibleMPEGFrameHeaders(firstHeader, header) {
			return 0, 0, fmt.Errorf("invalid MPEG audio frame at byte %d", position)
		}
		frameEnd := position + int64(header.FrameSize)
		if frameEnd > end {
			// Only tolerate a compatible final header after complete frames;
			// malformed headers and read failures still fail the scan.
			if frameCount > 0 {
				return totalSamples, firstHeader.SampleRate, fmt.Errorf("%w at byte %d: need %d bytes, have %d", ErrIncompleteFinalMP3Frame, position, header.FrameSize, end-position)
			}
			return 0, 0, fmt.Errorf("MPEG audio frame at byte %d exceeds audio payload", position)
		}
		if _, err := io.CopyN(io.Discard, reader, int64(header.FrameSize-4)); err != nil {
			return 0, 0, err
		}
		totalSamples += uint64(header.SamplesPerFrame)
		frameCount++
		position = frameEnd
	}

	if frameCount == 0 {
		return 0, 0, fmt.Errorf("MP3 audio frame not found")
	}
	return totalSamples, firstHeader.SampleRate, nil
}

func readMP3BytesAt(r io.ReadSeeker, offset int64, data []byte) error {
	if _, err := r.Seek(offset, io.SeekStart); err != nil {
		return err
	}
	_, err := io.ReadFull(r, data)
	return err
}
