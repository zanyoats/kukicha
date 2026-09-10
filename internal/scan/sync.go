package scan

import (
	"context"
	"database/sql"
	"slices"
	"strings"

	"github.com/zanyoats/kukicha/internal/app"
	scanartwork "github.com/zanyoats/kukicha/internal/scan/artwork"
	"github.com/zanyoats/kukicha/internal/textkey"
	"github.com/zanyoats/kukicha/internal/usecase/command"
)

func Sync(ctx context.Context, db *sql.DB, conf *app.AppConfig) (result command.BuildLibraryResult, err error) {
	conn, err := db.Conn(ctx)
	if err != nil {
		return
	}
	defer conn.Close()

	if err = command.PrepareScanResults(
		ctx,
		conn,
		conf.Settings.Server.Database.CacheSizeKiB,
	); err != nil {
		return
	}

	var itunesLookup command.ITunesArtworkLookup
	if conf.Settings.Scan.ITunes.Enabled {
		itunesClient := scanartwork.NewITunesClient(
			nil,
			scanartwork.ITunesClientConfig{
				RequestTimeout: conf.Settings.Scan.ITunes.RequestTimeout.Duration,
				LookupInterval: conf.Settings.Scan.ITunes.LookupInterval.Duration,
				RetryAttempts:  conf.Settings.Scan.ITunes.RetryAttempts,
			},
		)
		itunesLookup = func(
			ctx context.Context,
			kind app.ITunesLookupKind,
			lookupID string,
		) (app.ITunesLookupResult, bool, error) {
			return scanartwork.LookupCachedArtwork(
				ctx,
				conn,
				itunesClient,
				kind,
				lookupID,
			)
		}
	}

	if err = localFolderWalk(ctx, conf, conn, itunesLookup); err != nil {
		return
	}

	result, err = command.BuildLibraryFromScan(
		ctx, conn,
		RecognizedAudioExtensions,
		RecognizedPlaylistExtensions,
		RecognizedSidecarExtensions,
		UnknownItem,
		conf.Settings.Scan.SecondaryGenreSplitTokens,
	)

	return
}

func localFolderWalk(
	ctx context.Context,
	conf *app.AppConfig,
	conn *sql.Conn,
	itunesLookup command.ITunesArtworkLookup,
) error {
	walker := func(scannedFile *app.ScannedFile) (err error) {
		switch scannedFile.FileType {
		case app.FileTypeAudio:
			err = processFileTypeAudio(ctx, conf, conn, scannedFile)

		case app.FileTypeSidecarCover, app.FileTypeSidecarOther:
			err = processFileTypeSidecar(scannedFile)

		default: // app.FileTypePlaylist
			err = command.InsertLibrarySyncLog(ctx, conn, "info",
				"skipping unsupported playlist item", scannedFile.RootPath, scannedFile.Path)
		}

		if err == nil {
			err = command.InsertScannedFile(
				ctx,
				conn,
				scannedFile,
				conf.Settings.Scan.LocalArtworkMaxBytes,
				itunesLookup,
			)
		}

		return err
	}

	state := &walkState{ctx: ctx, conn: conn}
	walk(conf, state, walker)
	return state.err
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
			for _, genre := range splitGenreFieldList(conf.Settings.Scan.PrimaryGenreSplitTokens, value) {
				key := strings.ToLower(genre)

				// preprocess
				if replacement, ok := conf.Settings.Scan.GenreStyleAliases[key]; ok {
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

	mapping, err := applyAlbumArtistSplitRules(
		ctx, conn,
		scannedFile.NormalizedMetadata.AlbumKey,
		scannedFile.NormalizedMetadata.RawAlbumArtistDisplays,
		conf.Settings.Scan.AlbumArtistSplitTokens,
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

	scannedFile.NormalizedMetadata.RawTrackArtistDisplays = rawTrackArtistDisplays(scannedFile.Metadata)

	return nil
}

func processFileTypeSidecar(
	_ *app.ScannedFile,
) error {
	return nil
}

func applyAlbumArtistSplitRules(
	ctx context.Context,
	conn *sql.Conn,
	albumKey string,
	rawAlbumArtistDisplays []string,
	albumArtistSplitTokens map[string]bool,
) (mapping []string, err error) {
	var apply bool

	if apply, err = displaysContainAnyToken(rawAlbumArtistDisplays, albumArtistSplitTokens); err != nil {
		return
	} else if apply {
		if mapping, err = command.FindOrInsertMapping(
			ctx, conn, albumKey, rawAlbumArtistDisplays,
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
