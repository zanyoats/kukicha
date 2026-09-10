package scan

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/scan/duration"
	"github.com/zanyoats/kukicha/internal/textkey"
	"github.com/zanyoats/kukicha/internal/usecase/command"
)

type walkFunc func(scannedFile *app.ScannedFile) error

// walkState keeps infrastructure failures separate from recoverable file issues.
type walkState struct {
	ctx  context.Context
	conn *sql.Conn
	err  error
}

func (w *walkState) stopped() bool {
	if w.err == nil {
		w.err = w.ctx.Err()
	}
	return w.err != nil
}

func (w *walkState) log(level, message string, root *app.Root, path string) {
	if w.stopped() {
		return
	}
	if err := command.InsertLibrarySyncLog(w.ctx, w.conn, level, message, root.RootPath, path); err != nil {
		w.err = fmt.Errorf("%s: %w", message, err)
	}
}

func (w *walkState) fail(err error, root *app.Root, path string) {
	if !errors.Is(err, command.ErrSyncLogWrite) && w.ctx.Err() == nil {
		w.log("error", fmt.Sprintf("cannot process file: %v", err), root, path)
	}
	w.err = errors.Join(err, w.err)
}

// Normalized roots are valid dir paths
func walk(conf *app.AppConfig, state *walkState, fn walkFunc) {
	progressLogInterval := conf.Settings.Scan.ProgressLogInterval.Duration

	for _, root := range conf.Roots {
		if state.stopped() {
			return
		}
		processedFiles := 0
		lastProgressLog := time.Now()

		slog.Info(
			"scanning library root",
			"root_position", root.RootPos,
			"path", root.RootPath,
		)

		progressFn := func(scannedFile *app.ScannedFile) error {
			if err := fn(scannedFile); err != nil {
				return err
			}

			processedFiles++
			if progressLogInterval > 0 &&
				time.Since(lastProgressLog) >= progressLogInterval {
				slog.Info(
					"library scan progress",
					"processed_files", processedFiles,
					"root_position", root.RootPos,
					"path", root.RootPath,
				)
				lastProgressLog = time.Now()
			}

			return nil
		}

		walkDir(conf, state, &root, "", progressFn)
		if state.stopped() {
			return
		}

		slog.Info(
			"finished scanning library root",
			"root_position", root.RootPos,
			"path", root.RootPath,
			"processed_files", processedFiles,
		)
	}
}

func walkDir(
	conf *app.AppConfig,
	state *walkState,
	root *app.Root,
	folderPath string,
	fn walkFunc,
) {
	if state.stopped() {
		return
	}
	dir := filepath.Join(root.RootPath, folderPath)
	files, err := os.ReadDir(dir)

	if err != nil {
		state.log("error", fmt.Sprintf("cannot read folder; skipping it: %v", err), root, folderPath)
		return
	}

	for _, file := range files {
		if state.stopped() {
			return
		}
		switch {
		case file.Type().IsRegular():
			name := file.Name()
			if fileType, recognizable := isRecognizable(name, conf.Settings.Scan.RecognizedSidecarArtwork); recognizable {
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
					state.log("error", fmt.Sprintf("cannot stat file; skipping it: %v", err), root, path)
					continue
				} else {
					scannedFile.FileSize = info.Size()
					scannedFile.FileModTime = info.ModTime()

					if fileType == app.FileTypeAudio {
						if err := applyTrackMetadata(fullPath, scannedFile); err != nil {
							state.log("error", fmt.Sprintf("cannot read audio metadata: %v", err), root, path)
						}
						filterEmbeddedArtwork(scannedFile, conf.Settings.Scan.LocalArtworkMaxBytes, state)
					}
				}

				if state.stopped() {
					return
				}
				if err := fn(scannedFile); err != nil {
					state.fail(err, root, path)
					return
				}
			}

		case file.Type().IsDir():
			folderPath_ := filepath.Join(folderPath, file.Name())

			walkDir(conf, state, root, folderPath_, fn)
		}
	}
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

func applyTrackMetadata(
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

	if err := scanTrackMetadata(path, f, scannedFile); err != nil {
		return err
	}

	return nil
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

func filterEmbeddedArtwork(
	scannedFile *app.ScannedFile,
	maxBytes int64,
	state *walkState,
) {
	// Reuse the existing backing array while compacting accepted artwork in place.
	artwork := scannedFile.Artwork[:0]
	for position, candidate := range scannedFile.Artwork {
		if int64(candidate.ArtworkContentSize) > maxBytes {
			state.log("warn", fmt.Sprintf(
				"ignoring oversized embedded artwork: position %d, size %d bytes, limit %d bytes",
				position, candidate.ArtworkContentSize, maxBytes,
			), &scannedFile.Root, scannedFile.Path)
			continue
		}

		artwork = append(artwork, candidate)
	}
	scannedFile.Artwork = artwork
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
