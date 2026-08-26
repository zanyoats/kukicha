package scan

import (
	"context"
	"database/sql"
	"log"
	"slices"
	"strings"

	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/textkey"
	"github.com/zanyoats/kukicha/internal/usecase/command"
)

func Sync(ctx context.Context, db *sql.DB, conf *app.AppConfig) (result command.BuildLibraryResult, err error) {
	conn, err := db.Conn(ctx)
	if err != nil {
		return
	}
	defer conn.Close()

	if err = command.PrepareScanResults(ctx, conn); err != nil {
		return
	}

	if err = localFolderWalk(ctx, conf, conn); err != nil {
		return
	}

	result, err = command.BuildLibraryFromScan(
		ctx, conn,
		RecognizedAudioExtensions,
		RecognizedPlaylistExtensions,
		RecognizedSidecarExtensions,
		UnknownItem,
		conf.Settings.SecondaryGenreSplitTokens,
	)

	return
}

func localFolderWalk(ctx context.Context, conf *app.AppConfig, conn *sql.Conn) error {
	walker := func(scannedFile *app.ScannedFile) (err error) {
		switch scannedFile.FileType {
		case app.FileTypeAudio:
			err = processFileTypeAudio(ctx, conf, conn, scannedFile)

		case app.FileTypeSidecarCover, app.FileTypeSidecarOther:
			err = processFileTypeSidecar(scannedFile)

		default: // app.FileTypePlaylist
			err = processFileTypePlaylist(scannedFile)
		}

		if err == nil {
			err = command.InsertScannedFile(ctx, conn, scannedFile)
		}

		return err
	}

	if err := walk(conf, walker); err != nil {
		return err
	}

	return nil
}

func processFileTypeAudio(
	ctx context.Context,
	conf *app.AppConfig,
	conn *sql.Conn,
	scannedFile *app.ScannedFile,
) error {
	scannedFile.NormalizedMetadata.TrackDisplay = textkey.FirstNonZeroValue(
		scannedFile.Metadata.Title, scannedFile.Stem, UnknownItem,
	)
	scannedFile.NormalizedMetadata.AlbumKey = albumKey(scannedFile.Metadata)
	scannedFile.NormalizedMetadata.AlbumDisplay = albumDisplay(scannedFile.Metadata)
	scannedFile.NormalizedMetadata.RawAlbumArtistDisplays = rawAlbumArtistDisplays(scannedFile.Metadata)

	// apply genre normalization
	{
		seen := make(map[string]bool, len(scannedFile.Metadata.Genres))

		for _, value := range scannedFile.Metadata.Genres {
			for _, genre := range splitGenreFieldList(conf.Settings.PrimaryGenreSplitTokens, value) {
				key := strings.ToLower(genre)

				// preprocess
				if replacement, ok := conf.Settings.GenreStyleAliases[key]; ok {
					genre = replacement
					key = replacement
				}

				if key == "" || seen[key] {
					continue
				}
				seen[key] = true
				scannedFile.NormalizedMetadata.Genres = append(scannedFile.NormalizedMetadata.Genres, genre)
			}
		}
	}

	// apply *album* artist split rules using all split tokens
	mapping, err := applyAlbumArtistSplitRules(
		ctx, conn,
		scannedFile.NormalizedMetadata.AlbumKey,
		scannedFile.NormalizedMetadata.RawAlbumArtistDisplays,
		conf.Settings.AlbumArtistSplitTokens,
		conf.Settings.AlbumArtistSplitAuto,
	)

	if err != nil {
		return err
	}
	for _, mapped := range mapping {
		scannedFile.NormalizedMetadata.MappedAlbumArtistKeys =
			append(scannedFile.NormalizedMetadata.MappedAlbumArtistKeys, textkey.Slugify(mapped))

		scannedFile.NormalizedMetadata.MappedAlbumArtistDisplays =
			append(scannedFile.NormalizedMetadata.MappedAlbumArtistDisplays, textkey.NormDisplay(mapped))
	}

	// apply *track* artist split rules using auto tokens
	scannedFile.NormalizedMetadata.RawTrackArtistDisplays = rawTrackArtistDisplays(scannedFile.Metadata)
	if apply, err := displaysContainAnyToken(
		scannedFile.NormalizedMetadata.RawTrackArtistDisplays,
		conf.Settings.AlbumArtistSplitAuto,
	); err != nil {
		return err
	} else if apply {
		mapping =
			applyAutoMapping(
				scannedFile.NormalizedMetadata.RawTrackArtistDisplays,
				conf.Settings.AlbumArtistSplitAuto,
			)
	} else {
		mapping =
			slices.Clone(scannedFile.NormalizedMetadata.RawTrackArtistDisplays)
	}
	slices.Sort(mapping)
	mapping = slices.Compact(mapping)
	scannedFile.NormalizedMetadata.MappedTrackArtistDisplays = mapping

	return nil
}

func processFileTypeSidecar(
	_ *app.ScannedFile,
) error {
	return nil
}

func processFileTypePlaylist(scannedFile *app.ScannedFile) error {
	log.Printf("not implement yet: skipping playlist item: %q\n", scannedFile.Path)
	return nil
}

func applyAlbumArtistSplitRules(
	ctx context.Context,
	conn *sql.Conn,
	albumKey string,
	rawAlbumArtistDisplays []string,
	albumArtistSplitTokens map[string]bool,
	albumArtistSplitAuto map[string]bool,
) (mapping []string, err error) {
	var apply bool

	if apply, err = displaysContainAnyToken(rawAlbumArtistDisplays, albumArtistSplitTokens); err != nil {
		return
	} else if apply {
		autoMapping := applyAutoMapping(rawAlbumArtistDisplays, albumArtistSplitAuto)

		if mapping, err = command.FindOrInsertMapping(
			ctx, conn, albumKey, autoMapping,
		); err != nil {
			return
		}
	} else {
		mapping = slices.Clone(rawAlbumArtistDisplays)
	}

	slices.Sort(mapping)
	mapping = slices.Compact(mapping)
	return
}

func applyAutoMapping(displays []string, tokens map[string]bool) []string {
	for token, enabled := range tokens {
		if !enabled {
			continue
		}

		var tmp []string

		for _, display := range displays {
			tmp = append(tmp, strings.Split(display, token)...)
		}

		displays = tmp
	}

	var result []string
	for _, display := range displays {
		result = append(result, strings.TrimSpace(display))
	}

	return result
}

func displaysContainAnyToken(displays []string, tokens map[string]bool) (bool, error) {
	for _, display := range displays {
		for token, enabled := range tokens {
			if !enabled {
				continue
			}

			if textkey.IsWordToken(token) {
				if matches, err := textkey.MatchesWordBoundary(display, token); err != nil {
					return false, err
				} else if matches {
					return true, nil
				}
			} else if strings.Contains(display, token) {
				return true, nil
			}
		}
	}

	return false, nil
}
