package scan

import (
	"errors"
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"strings"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/textkey"
)

type walkFunc func(scannedFile *app.ScannedFile) error

// Normalized roots are valid dir paths
func walk(appConfig *app.AppConfig, fn walkFunc) error {
	for _, root := range appConfig.Roots {
		log.Printf("Walking root/%d %q", root.RootPos, root.RootPath)

		if err := walkDir(appConfig, &root, "", fn); err != nil {
			return err
		}
	}

	return nil
}

func walkDir(
	appConfig *app.AppConfig,
	root *app.Root,
	folderPath string,
	fn walkFunc,
) error {
	dir := filepath.Join(root.RootPath, folderPath)
	files, err := os.ReadDir(dir)

	if err != nil {
		// TODO log level warning or error
		log.Printf("I cannot read folder %q for below reason", dir)
		log.Printf("%v", err)
		return nil
	}

	for _, file := range files {
		switch {
		case file.Type().IsRegular():
			name := file.Name()
			if fileType, recognizable := isRecognizable(name, appConfig.Settings.RecognizedSidecarArtwork); recognizable {
				path := filepath.Join(folderPath, name)
				base := filepath.Base(path)
				ext := filepath.Ext(path)
				fullPath := filepath.Join(root.RootPath, path)
				fileIdentity := app.FileIdentity{
					Root: *root,
					Path: path,
					Ext:  ext,
					Stem: strings.TrimSpace(strings.TrimSuffix(base, ext)),
				}
				fileSnapshot := app.FileSnapshot{
					FileType: fileType,
				}
				scannedFile := &app.ScannedFile{
					FileIdentity: fileIdentity,
					FileSnapshot: fileSnapshot,
					Metadata: &app.TrackMetadata{
						Artists:         []string{},
						ArtistSort:      []string{},
						AlbumArtists:    []string{},
						AlbumArtistSort: []string{},
						Performers:      []string{},
						Genres:          []string{},
					},
					NormalizedMetadata: &app.NormalizedTrackMetadata{},
				}

				if info, err := os.Lstat(fullPath); err != nil {
					scannedFile.ScanError = err.Error()
				} else {
					scannedFile.FileSize = info.Size()
					scannedFile.FileModTime = info.ModTime()

					if fileType == app.FileTypeAudio {
						applyTrackMetadata(fullPath, scannedFile)
					}
				}

				if err := fn(scannedFile); err != nil {
					return err
				}
			}

		case file.Type().IsDir():
			folderPath_ := filepath.Join(folderPath, file.Name())

			if err := walkDir(appConfig, root, folderPath_, fn); err != nil {
				return err
			}
		}
	}

	return nil
}

func isRecognizable(name string, recognizedSidecarArtwork map[string]bool) (filetype app.FileType, ok bool) {
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
			for sidecar_, enabled := range recognizedSidecarArtwork {
				if !enabled {
					continue
				}

				if strings.EqualFold(sidecar, sidecar_) {
					ok = true
					filetype = app.FileTypeSidecar
					return
				}
			}
		}
	}

	return
}

func applyTrackMetadata(path string, scannedFile *app.ScannedFile) {
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
		scannedFile.ScanError = err.Error()
		return
	}
	defer f.Close()

	if err := scanTrackMetadata(path, f, scannedFile); err != nil {
		scannedFile.ScanError = err.Error()
		return
	}
}

// NOTE: ensure stays synced with `scan.RecognizedAudioExtensions`
func scanTrackMetadata(
	path string,
	f io.ReadSeeker,
	scannedFile *app.ScannedFile,
) error {
	switch strings.ToLower(filepath.Ext(path)) {
	case ".m4a", ".m4p", ".m4v":
		items, err := readMP4ItemList(f)
		if err != nil {
			return fmt.Errorf("read MP4 item list: %w", err)
		}
		scannedFile.Metadata.Format = formatMP4
		applyMP4TrackFields(scannedFile, items)
		applyEmbeddedArtwork(&scannedFile.Artwork, mp4Artwork(items))

	case ".flac", ".oga", ".ogg", ".opus":
		metadata, err := readVorbisMetadata(f)
		if err != nil {
			return fmt.Errorf("read Vorbis metadata: %w", err)
		}
		scannedFile.Metadata.Format = formatVorbis
		applyVorbisTrackFields(scannedFile, metadata.Comments)
		applyEmbeddedArtwork(&scannedFile.Artwork, metadata.Artwork)

	case ".mp3":
		metadata, err := readID3Metadata(f)
		if err == nil {
			scannedFile.Metadata.Format = metadata.Format
			applyID3TrackFields(scannedFile, metadata.Format, metadata.TextFrames)
			applyEmbeddedArtwork(&scannedFile.Artwork, metadata.Artwork)
			return nil
		}
		if !errors.Is(err, errNoID3v2) {
			return fmt.Errorf("read ID3v2 metadata: %w", err)
		}

		id3v1, err := readID3v1Metadata(f)
		if err != nil {
			return fmt.Errorf("read ID3 metadata: %w", err)
		}
		scannedFile.Metadata.Format = formatID3v1
		applyID3v1TrackFields(scannedFile, id3v1)

	default:
		panic(fmt.Sprintf("bug: extension %q is a recognizable audio file but not handled by scanner", filepath.Ext(path)))
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

func rawAlbumArtistDisplays(metadata *app.TrackMetadata) []string {
	return textkey.FirstNonEmptySlice(metadata.AlbumArtists, metadata.Artists, []string{UnknownItem})
}

func rawTrackArtistDisplays(metadata *app.TrackMetadata) []string {
	return textkey.FirstNonEmptySlice(metadata.Artists, metadata.AlbumArtists, []string{UnknownItem})
}

func rawAlbumArtistKeys(metadata *app.TrackMetadata) []string {
	albumArtists := rawAlbumArtistDisplays(metadata)
	result := make([]string, 0, len(albumArtists))

	for _, albumArtist := range albumArtists {
		result = append(result, textkey.Slugify(albumArtist))
	}
	return result
}

func albumDisplay(metadata *app.TrackMetadata) string {
	return textkey.FirstNonZeroValue(metadata.Album, UnknownItem)
}

func albumKey(metadata *app.TrackMetadata) string {
	artistKeys := rawAlbumArtistKeys(metadata)
	album := albumDisplay(metadata)

	return fmt.Sprintf("%s::%s",
		strings.Join(artistKeys, "-and-"),
		textkey.Slugify(album))
}
