package scan

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/scan/duration"
	"github.com/zanyoats/kukicha/internal/textkey"
)

var errMaxSizeArtwork = errors.New("oversized embedded artwork file")

func IsRecognizable(name string, recognizedSidecarArtwork map[string]bool) (filetype app.FileType, ok bool) {
	ok = false
	base := filepath.Base(name)
	ext := filepath.Ext(name)

	for ext_ := range RecognizedAudioExtensions {
		if strings.EqualFold(ext, ext_) {
			ok = true
			filetype = app.FileTypeAudio
			return
		}
	}

	for ext_ := range RecognizedPlaylistExtensions {
		if strings.EqualFold(ext, ext_) {
			ok = true
			filetype = app.FileTypePlaylist
			return
		}
	}

	sidecar := strings.TrimSuffix(base, ext)

	for ext_ := range RecognizedSidecarExtensions {
		if strings.EqualFold(ext, ext_) {
			if ext_ == ".pdf" {
				ok = true
				filetype = app.FileTypeSidecarOther
				return
			}

			for sidecar_, enabled := range recognizedSidecarArtwork {
				if !enabled {
					continue
				}

				if strings.EqualFold(sidecar, sidecar_) {
					ok = true
					filetype = app.FileTypeSidecarCover
					return
				}
			}

			ok = true
			filetype = app.FileTypeSidecarOther
			return
		}
	}

	return
}

func ApplyTrackMetadata(
	path string,
	conf *app.AppConfig,
	scannedFile *app.ScannedFile,
) error {
	if err := scanTrackMetadata(path, scannedFile); err != nil {
		return fmt.Errorf("scan file: %w", err)
	}
	if err := filterEmbeddedArtwork(
		scannedFile, conf.Settings.Scan.LocalArtworkMaxBytes,
	); err != nil {
		return fmt.Errorf("embedded artwork: %w", err)
	}

	return nil
}

// NOTE: ensure stays synced with `scan.RecognizedAudioExtensions`
func scanTrackMetadata(
	path string,
	scannedFile *app.ScannedFile,
) error {
	defer func() {
		if scannedFile.Metadata.ReleaseYear == 0 {
			scannedFile.Metadata.ReleaseYear = yearFromDate(scannedFile.Metadata.ReleaseDate)
		}
		if scannedFile.Metadata.OriginalReleaseYear == 0 {
			scannedFile.Metadata.OriginalReleaseYear = yearFromDate(scannedFile.Metadata.OriginalReleaseDate)
		}
	}()

	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()

	switch strings.ToLower(filepath.Ext(path)) {
	case ".m4a", ".m4p", ".m4v":
		items, err := readMP4ItemList(f)
		if err != nil {
			return fmt.Errorf("read MP4 item list: %w", err)
		}
		scannedFile.Metadata.Format = formatMP4
		applyMP4TrackFields(scannedFile, items)
		applyEmbeddedArtwork(&scannedFile.Artwork, mp4Artwork(items))
		durationSeconds, err := duration.ReadMP4(f)
		if err != nil {
			return fmt.Errorf("read MP4 duration: %w", err)
		}
		scannedFile.DurationSeconds = durationSeconds

	case ".flac", ".oga", ".ogg", ".opus":
		metadata, err := readVorbisMetadata(f)
		if err != nil {
			return fmt.Errorf("read Vorbis metadata: %w", err)
		}
		scannedFile.Metadata.Format = formatVorbis
		applyVorbisTrackFields(scannedFile, metadata.Comments)
		applyEmbeddedArtwork(&scannedFile.Artwork, metadata.Artwork)
		durationSeconds, err := duration.ReadVorbis(f)
		if err != nil {
			return fmt.Errorf("read Vorbis duration: %w", err)
		}
		scannedFile.DurationSeconds = durationSeconds

	case ".mp3":
		metadata, err := readID3Metadata(f)
		if err == nil {
			scannedFile.Metadata.Format = metadata.Format
			applyID3TrackFields(scannedFile, metadata.Format, metadata.TextFrames)
			applyEmbeddedArtwork(&scannedFile.Artwork, metadata.Artwork)
		} else if !errors.Is(err, errNoID3v2) {
			return fmt.Errorf("read ID3v2 metadata: %w", err)
		} else {
			id3v1, err := readID3v1Metadata(f)
			if err != nil {
				return fmt.Errorf("read ID3 metadata: %w", err)
			}
			scannedFile.Metadata.Format = formatID3v1
			applyID3v1TrackFields(scannedFile, id3v1)
		}

		durationSeconds, err := duration.ReadMP3(f)
		if err != nil {
			return fmt.Errorf("read MP3 duration: %w", err)
		}
		scannedFile.DurationSeconds = durationSeconds

	default:
		panic(fmt.Sprintf(
			"bug: extension %q is a recognizable audio file but not handled by scanner",
			filepath.Ext(path),
		))
	}

	return nil
}

func applyEmbeddedArtwork(embeddedArtwork *[]app.EmbeddedArtwork, artwork []artwork) {
	for _, artwork_ := range artwork {
		if size := len(artwork_.Data); size > 0 {
			*embeddedArtwork = append(*embeddedArtwork, app.EmbeddedArtwork{
				PictureType:        artworkPictureTypeString(artwork_.PictureType),
				ArtworkContentType: textkey.FirstNonZeroValue(artwork_.MIMEType, sniffImageMIMEType(artwork_.Data)),
				ArtworkContentSize: size,
				ArtworkBytes:       artwork_.Data,
			})
		}
	}
}

func filterEmbeddedArtwork(
	scannedFile *app.ScannedFile,
	maxBytes int64,
) error {
	var errs error
	// Reuse the existing backing array while compacting accepted artwork in place.
	artwork := scannedFile.Artwork[:0]
	for _, candidate := range scannedFile.Artwork {
		if int64(candidate.ArtworkContentSize) > maxBytes {
			errs = errors.Join(errs, fmt.Errorf(
				"%w: size %d bytes, picture type %q",
				errMaxSizeArtwork,
				candidate.ArtworkContentSize,
				candidate.PictureType,
			))
			continue
		}

		artwork = append(artwork, candidate)
	}
	scannedFile.Artwork = artwork
	return errs
}
