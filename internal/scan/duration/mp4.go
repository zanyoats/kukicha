package duration

import (
	"encoding/binary"
	"fmt"
	"io"
)

type mp4Duration struct {
	Units          uint64
	UnitsPerSecond uint64
	Present        bool
}

// ReadMP4 returns the audio presentation duration in whole seconds.
func ReadMP4(r io.ReadSeeker) (int64, error) {
	end, err := r.Seek(0, io.SeekEnd)
	if err != nil {
		return 0, err
	}
	if _, err := r.Seek(0, io.SeekStart); err != nil {
		return 0, err
	}

	for {
		pos, err := r.Seek(0, io.SeekCurrent)
		if err != nil {
			return 0, err
		}
		if pos >= end || end-pos < 8 {
			break
		}

		box, err := readMP4BoxHeader(r, end)
		if err != nil {
			return 0, err
		}
		if box.Name == "moov" {
			movieDuration, audioDuration, err := readMP4MovieDurations(r, box.End)
			if err != nil {
				return 0, err
			}
			// The movie header describes presentation duration and therefore
			// includes edits. Some fragmented or partially-authored files leave it
			// at zero, in which case the audio media header is the best fallback.
			if movieDuration.Present && movieDuration.Units > 0 {
				return truncateDurationSeconds(
					movieDuration.Units,
					movieDuration.UnitsPerSecond,
				)
			}
			if audioDuration.Present {
				return truncateDurationSeconds(
					audioDuration.Units,
					audioDuration.UnitsPerSecond,
				)
			}
			if movieDuration.Present {
				return 0, nil
			}
		}

		if _, err := r.Seek(box.End, io.SeekStart); err != nil {
			return 0, err
		}
	}

	return 0, fmt.Errorf("MP4 duration box not found")
}

func readMP4MovieDurations(
	r io.ReadSeeker,
	end int64,
) (movieDuration mp4Duration, audioDuration mp4Duration, err error) {
	for {
		var pos int64
		if pos, err = r.Seek(0, io.SeekCurrent); err != nil {
			return
		}
		if pos >= end || end-pos < 8 {
			return
		}

		var box mp4BoxHeader
		if box, err = readMP4BoxHeader(r, end); err != nil {
			return
		}
		switch box.Name {
		case "mvhd":
			movieDuration, err = readMP4HeaderDuration(r, box.End)
		case "trak":
			var candidate mp4Duration
			var audio bool
			candidate, audio, err = readMP4TrackDuration(r, box.End)
			if audio && candidate.Present && !audioDuration.Present {
				audioDuration = candidate
			}
		}
		if err != nil {
			return
		}

		if _, err = r.Seek(box.End, io.SeekStart); err != nil {
			return
		}
	}
}

func readMP4TrackDuration(
	r io.ReadSeeker,
	end int64,
) (duration mp4Duration, audio bool, err error) {
	for {
		var pos int64
		if pos, err = r.Seek(0, io.SeekCurrent); err != nil {
			return
		}
		if pos >= end || end-pos < 8 {
			return
		}

		var box mp4BoxHeader
		if box, err = readMP4BoxHeader(r, end); err != nil {
			return
		}
		if box.Name == "mdia" {
			duration, audio, err = readMP4MediaDuration(r, box.End)
			return
		}
		if _, err = r.Seek(box.End, io.SeekStart); err != nil {
			return
		}
	}
}

func readMP4MediaDuration(
	r io.ReadSeeker,
	end int64,
) (duration mp4Duration, audio bool, err error) {
	for {
		var pos int64
		if pos, err = r.Seek(0, io.SeekCurrent); err != nil {
			return
		}
		if pos >= end || end-pos < 8 {
			return
		}

		var box mp4BoxHeader
		if box, err = readMP4BoxHeader(r, end); err != nil {
			return
		}
		switch box.Name {
		case "mdhd":
			duration, err = readMP4HeaderDuration(r, box.End)
		case "hdlr":
			audio, err = readMP4AudioHandler(r, box.End)
		}
		if err != nil {
			return
		}
		if _, err = r.Seek(box.End, io.SeekStart); err != nil {
			return
		}
	}
}

type mp4BoxHeader struct {
	Name string
	End  int64
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
		return mp4BoxHeader{}, fmt.Errorf(
			"invalid MP4 box %q size %d",
			string(header[4:8]),
			size,
		)
	}
	if size > uint64(parentEnd-start) {
		return mp4BoxHeader{}, fmt.Errorf(
			"MP4 box %q extends past parent",
			string(header[4:8]),
		)
	}

	return mp4BoxHeader{
		Name: string(header[4:8]),
		End:  start + int64(size),
	}, nil
}

// Both mvhd and mdhd place their time scale and duration at the same offsets.
// Version 0 uses 32-bit timestamps/duration; version 1 uses 64-bit values.
func readMP4HeaderDuration(r io.ReadSeeker, end int64) (mp4Duration, error) {
	start, err := r.Seek(0, io.SeekCurrent)
	if err != nil {
		return mp4Duration{}, err
	}
	remaining := end - start
	if remaining < 1 {
		return mp4Duration{}, fmt.Errorf("invalid MP4 duration box")
	}

	var version [1]byte
	if _, err := io.ReadFull(r, version[:]); err != nil {
		return mp4Duration{}, err
	}
	if _, err := r.Seek(start, io.SeekStart); err != nil {
		return mp4Duration{}, err
	}

	switch version[0] {
	case 0:
		if remaining < 20 {
			return mp4Duration{}, fmt.Errorf("short version 0 MP4 duration box")
		}
		var payload [20]byte
		if _, err := io.ReadFull(r, payload[:]); err != nil {
			return mp4Duration{}, err
		}
		timeScale := binary.BigEndian.Uint32(payload[12:16])
		duration := binary.BigEndian.Uint32(payload[16:20])
		if timeScale == 0 {
			return mp4Duration{}, fmt.Errorf("MP4 duration time scale is zero")
		}
		if duration == ^uint32(0) {
			return mp4Duration{}, nil
		}
		return mp4Duration{
			Units:          uint64(duration),
			UnitsPerSecond: uint64(timeScale),
			Present:        true,
		}, nil

	case 1:
		if remaining < 32 {
			return mp4Duration{}, fmt.Errorf("short version 1 MP4 duration box")
		}
		var payload [32]byte
		if _, err := io.ReadFull(r, payload[:]); err != nil {
			return mp4Duration{}, err
		}
		timeScale := binary.BigEndian.Uint32(payload[20:24])
		duration := binary.BigEndian.Uint64(payload[24:32])
		if timeScale == 0 {
			return mp4Duration{}, fmt.Errorf("MP4 duration time scale is zero")
		}
		if duration == ^uint64(0) {
			return mp4Duration{}, nil
		}
		return mp4Duration{
			Units:          duration,
			UnitsPerSecond: uint64(timeScale),
			Present:        true,
		}, nil

	default:
		return mp4Duration{}, fmt.Errorf(
			"unsupported MP4 duration box version %d",
			version[0],
		)
	}
}

func readMP4AudioHandler(r io.ReadSeeker, end int64) (bool, error) {
	start, err := r.Seek(0, io.SeekCurrent)
	if err != nil {
		return false, err
	}
	if end-start < 12 {
		return false, fmt.Errorf("short MP4 handler box")
	}

	var payload [12]byte
	if _, err := io.ReadFull(r, payload[:]); err != nil {
		return false, err
	}
	return string(payload[8:12]) == "soun", nil
}
