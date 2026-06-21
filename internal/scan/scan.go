package scan

import (
	"errors"
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/zanyoats/kukicha/internal/app"
)

type WalkFunc func(scanFile *ScanFile, err error) error

type ScanFile struct {
	Root          *app.Root
	Path          string
	FileType      app.FileType
	FileSize      int64
	FileModTime   time.Time
	TrackMetadata *TrackMetadata
}

type TrackMetadata struct {
	Format              string
	Artists             []string
	AlbumArtists        []string
	Performers          []string
	Composer            string
	Album               string
	Title               string
	Work                string
	Grouping            string
	MovementName        string
	MovementNumber      int
	MovementCount       int
	ShowMovement        bool
	TrackNumber         int
	TrackCount          int
	DiscNumber          int
	DiscCount           int
	ReleaseDate         string
	OriginalReleaseDate string
	RecordedDate        string
	ReleaseYear         int
	OriginalReleaseYear int
	ItunesStoreTrackId  int64
	ItunesStoreAlbumId  int64
	PurchaseDate        string
	Genres              []string
	IsCompilation       bool
	HasEmbeddedArtwork  bool
	ArtworkContentType  string
	ArtworkContentSize  int
	ArtworkBytes        []byte
	ScanError           error
}

func Walk(appConfig *app.AppConfig, fn WalkFunc) error {
	for _, root := range appConfig.Roots {
		log.Printf("Walking root/%d %q", root.Pos, root.Path)

		if root.IsDir {
			if err := walkDir(&root, "", fn); err != nil {
				return err
			}
		} else {
			if info, err := os.Lstat(root.Path); err != nil {
				return app.GiveUp(err)
			} else {
				scanFile := &ScanFile{
					Root:        &root,
					Path:        "",
					FileType:    root.FileType,
					FileSize:    info.Size(),
					FileModTime: info.ModTime(),
				}
				if root.FileType == app.FileTypeAudio {
					scanFile.TrackMetadata = newTrackMetadata(root.Path)
				}
				if err := fn(scanFile, nil); err != nil {
					return err
				}
			}

		}
	}

	return nil
}

func walkDir(root *app.Root, folderPath string, fn WalkFunc) error {
	dir := filepath.Join(root.Path, folderPath)
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
			if fileType, recognizable := app.IsRecognizable(name); recognizable {
				path := filepath.Join(folderPath, name)
				fullPath := filepath.Join(root.Path, path)
				if info, err := os.Lstat(fullPath); err != nil {
					return app.GiveUp(err)
				} else {
					scanFile := &ScanFile{
						Root:        root,
						Path:        path,
						FileType:    fileType,
						FileSize:    info.Size(),
						FileModTime: info.ModTime(),
					}
					if fileType == app.FileTypeAudio {
						scanFile.TrackMetadata = newTrackMetadata(fullPath)
					}
					if err := fn(scanFile, nil); err != nil {
						return err
					}
				}
			}
		case file.Type().IsDir():
			folderPath_ := filepath.Join(folderPath, file.Name())
			if err := walkDir(root, folderPath_, fn); err != nil {
				return err
			}
		}
	}

	return nil
}

func newTrackMetadata(path string) (record *TrackMetadata) {
	record = &TrackMetadata{
		Artists:      []string{},
		AlbumArtists: []string{},
		Performers:   []string{},
		Genres:       []string{},
	}
	base := filepath.Base(path)
	ext := filepath.Ext(path)
	record.Title = strings.TrimSuffix(base, ext)

	f, err := os.Open(path)
	if err != nil {
		record.ScanError = err
		return
	}
	defer f.Close()

	if err := scanTrackMetadata(path, f, record); err != nil {
		record.ScanError = err
		return
	}

	finalizeTrackMetadata(record)
	return record
}

func scanTrackMetadata(path string, f io.ReadSeeker, record *TrackMetadata) error {
	switch strings.ToLower(filepath.Ext(path)) {
	case ".m4a", ".m4p":
		return scanMP4TrackMetadata(f, record)
	case ".flac", ".oga", ".ogg", ".opus":
		return scanVorbisTrackMetadata(f, record)
	case ".mp3":
		return scanID3TrackMetadata(f, record)
	default:
		return fmt.Errorf("unsupported audio file extension %q", filepath.Ext(path))
	}
}

func scanMP4TrackMetadata(f io.ReadSeeker, record *TrackMetadata) error {
	items, err := readMP4ItemList(f)
	if err != nil {
		return fmt.Errorf("read MP4 item list: %w", err)
	}
	record.Format = formatMP4
	applyMP4TrackFields(record, items)
	applyEmbeddedArtwork(record, mp4Artwork(items))
	return nil
}

func scanVorbisTrackMetadata(f io.ReadSeeker, record *TrackMetadata) error {
	metadata, err := readVorbisMetadata(f)
	if err != nil {
		return fmt.Errorf("read Vorbis metadata: %w", err)
	}
	record.Format = formatVorbis
	applyVorbisTrackFields(record, metadata.Comments)
	applyEmbeddedArtwork(record, metadata.Artwork)
	return nil
}

func scanID3TrackMetadata(f io.ReadSeeker, record *TrackMetadata) error {
	metadata, err := readID3Metadata(f)
	if err == nil {
		record.Format = metadata.Format
		applyID3TrackFields(record, metadata.Format, metadata.TextFrames)
		applyEmbeddedArtwork(record, metadata.Artwork)
		return nil
	}
	if !errors.Is(err, errNoID3v2) {
		return fmt.Errorf("read ID3v2 metadata: %w", err)
	}

	id3v1, err := readID3v1Metadata(f)
	if err != nil {
		return fmt.Errorf("read ID3 metadata: %w", err)
	}
	record.Format = formatID3v1
	applyID3v1TrackFields(record, id3v1)
	return nil
}

func finalizeTrackMetadata(record *TrackMetadata) {
	if record.ReleaseYear == 0 {
		record.ReleaseYear = yearFromDate(record.ReleaseDate)
	}
	if record.OriginalReleaseYear == 0 {
		record.OriginalReleaseYear = yearFromDate(record.OriginalReleaseDate)
	}
}

func applyMP4TrackFields(record *TrackMetadata, items []mp4Item) {
	byName := indexMP4Items(items)

	if values := mp4TextValues(items, "\xa9ART", "\xa9art", "artist"); len(values) > 0 {
		record.Artists = values
	}
	if values := mp4TextValues(items, "aART", "albumartist", "album_artist"); len(values) > 0 {
		record.AlbumArtists = values
	}
	if values := mp4TextValues(items, "performer", "performers"); len(values) > 0 {
		record.Performers = values
	}
	setStringIfEmpty(&record.Composer, firstMP4Text(byName, "\xa9wrt", "composer"))
	setStringIfEmpty(&record.Album, firstMP4Text(byName, "\xa9alb", "album"))
	setStringIfEmpty(&record.Title, firstMP4Text(byName, "\xa9nam", "title"))
	setStringIfEmpty(&record.Work, firstMP4Text(byName, "\xa9wrk", "work"))
	setStringIfEmpty(&record.Grouping, firstMP4Text(byName, "\xa9grp", "grouping"))
	setStringIfEmpty(&record.MovementName, firstMP4Text(byName, "\xa9mvn", "movementname"))
	setIntIfZero(&record.MovementNumber, firstMP4Int(byName, "\xa9mvi", "movementnumber"))
	setIntIfZero(&record.MovementCount, firstMP4Int(byName, "\xa9mvc", "movementcount"))
	if value, ok := firstMP4Bool(byName, "shwm", "showmovement"); ok {
		record.ShowMovement = value
	}

	if number, count, ok := firstMP4IntPair(byName, "trkn", "tracknumber", "track_number"); ok {
		setIntIfZero(&record.TrackNumber, number)
		setIntIfZero(&record.TrackCount, count)
	}
	setIntIfZero(&record.TrackNumber, firstMP4Int(byName, "tracknumber", "track_number"))
	setIntIfZero(&record.TrackCount, firstMP4Int(byName, "tracktotal", "track_total", "totaltracks", "total_tracks", "trackcount", "track_count"))
	if number, count, ok := firstMP4IntPair(byName, "disk", "discnumber", "disc_number"); ok {
		setIntIfZero(&record.DiscNumber, number)
		setIntIfZero(&record.DiscCount, count)
	}
	setIntIfZero(&record.DiscNumber, firstMP4Int(byName, "discnumber", "disc_number"))
	setIntIfZero(&record.DiscCount, firstMP4Int(byName, "disctotal", "disc_total", "totaldiscs", "total_discs", "disccount", "disc_count"))

	setStringIfEmpty(&record.ReleaseDate, firstMP4Text(byName, "\xa9day"))
	setStringIfEmpty(&record.OriginalReleaseDate, firstMP4Text(byName, "originalreleasedate", "original_release_date", "originaldate", "original_date", "originalyear", "original_year"))
	setStringIfEmpty(&record.RecordedDate, firstMP4Text(byName, "recordeddate", "recorded_date", "recordingdate", "recording_date"))
	setInt64IfZero(&record.ItunesStoreTrackId, firstMP4Int64(byName, "cnID"))
	setInt64IfZero(&record.ItunesStoreAlbumId, firstMP4Int64(byName, "plID"))
	setStringIfEmpty(&record.PurchaseDate, firstMP4Text(byName, "purd"))
	if value, ok := firstMP4Bool(byName, "cpil"); ok {
		record.IsCompilation = value
	}

	addGenres(record, mp4Genres(items)...)
}

func applyVorbisTrackFields(record *TrackMetadata, comments vorbisCommentMap) {
	if len(comments) == 0 {
		return
	}

	if values := comments.values("artist"); len(values) > 0 {
		record.Artists = cleanStringValues(values...)
	}
	if values := comments.values("albumartist", "album_artist", "album artist"); len(values) > 0 {
		record.AlbumArtists = cleanStringValues(values...)
	}
	if values := comments.values("performer", "performers"); len(values) > 0 {
		record.Performers = cleanStringValues(values...)
	}
	setStringIfEmpty(&record.Composer, comments.first("composer"))
	setStringIfEmpty(&record.Album, comments.first("album"))
	setStringIfEmpty(&record.Title, comments.first("title"))
	setStringIfEmpty(&record.Work, comments.first("work"))
	setStringIfEmpty(&record.Grouping, comments.first("grouping", "contentgroup", "content_group"))
	setStringIfEmpty(&record.MovementName, comments.first("movementname", "movement_name", "movement name"))

	setIntIfZero(&record.MovementNumber, comments.firstInt("movementnumber", "movement_number", "movement number"))
	setIntIfZero(&record.MovementCount, comments.firstInt("movementcount", "movement_count", "movementtotal", "movement_total"))
	if value, ok := comments.firstBool("showmovement", "show_movement", "show movement"); ok {
		record.ShowMovement = value
	}

	setIntIfZero(&record.TrackNumber, comments.firstInt("tracknumber", "track_number"))
	setIntIfZero(&record.TrackCount, comments.firstInt("tracktotal", "track_total", "totaltracks", "total_tracks", "trackcount", "track_count"))
	setIntIfZero(&record.DiscNumber, comments.firstInt("discnumber", "disc_number"))
	setIntIfZero(&record.DiscCount, comments.firstInt("disctotal", "disc_total", "totaldiscs", "total_discs", "disccount", "disc_count"))

	setStringIfEmpty(&record.ReleaseDate, comments.first("releasedate", "release_date", "date", "year"))
	setStringIfEmpty(&record.OriginalReleaseDate, comments.first("originalreleasedate", "original_release_date", "originaldate", "original_date", "originalyear", "original_year"))
	setStringIfEmpty(&record.RecordedDate, comments.first("recordeddate", "recorded_date", "recordingdate", "recording_date"))
	setIntIfZero(&record.OriginalReleaseYear, yearFromDate(record.OriginalReleaseDate))

	setInt64IfZero(&record.ItunesStoreTrackId, comments.firstInt64("itunesstoretrackid", "itunes_store_track_id", "cnid"))
	setInt64IfZero(&record.ItunesStoreAlbumId, comments.firstInt64("itunesstorealbumid", "itunes_store_album_id", "plid"))
	setStringIfEmpty(&record.PurchaseDate, comments.first("purchasedate", "purchase_date"))
	if value, ok := comments.firstBool("compilation", "iscompilation", "is_compilation", "itunescompilation"); ok {
		record.IsCompilation = value
	}

	if genres := comments.values("genre"); len(genres) > 0 {
		record.Genres = []string{}
		addGenres(record, genres...)
	}
}

func applyID3TrackFields(record *TrackMetadata, format string, frames id3TextFrameMap) {
	if len(frames) == 0 {
		return
	}

	if values := frames.values("TP1", "TPE1"); len(values) > 0 {
		record.Artists = cleanStringValues(values...)
	}
	if values := frames.values("TP2", "TPE2"); len(values) > 0 {
		record.AlbumArtists = cleanStringValues(values...)
	}
	if values := id3PerformerValues(frames); len(values) > 0 {
		record.Performers = values
	}
	setStringIfEmpty(&record.Composer, frames.first("TCM", "TCOM"))
	setStringIfEmpty(&record.Album, frames.first("TAL", "TALB"))
	setStringIfEmpty(&record.Title, frames.first("TT2", "TIT2"))
	setStringIfEmpty(&record.Work, frames.firstUserText("WORK"))
	setStringIfEmpty(&record.Grouping, firstNonEmpty(
		frames.first("TT1", "TIT1"),
		frames.firstUserText("GROUPING", "CONTENTGROUP"),
	))
	setStringIfEmpty(&record.MovementName, firstNonEmpty(
		frames.first("MVNM"),
		frames.firstUserText("MOVEMENTNAME", "MOVEMENT NAME"),
	))

	setIntIfZero(&record.MovementNumber, firstNonZero(
		frames.firstInt("MVIN"),
		frames.firstUserTextInt("MOVEMENTNUMBER", "MOVEMENT NUMBER"),
	))
	setIntIfZero(&record.MovementCount, firstNonZero(
		frames.firstSlashCount("MVIN"),
		frames.firstUserTextInt("MOVEMENTCOUNT", "MOVEMENT COUNT", "MOVEMENTTOTAL", "MOVEMENT TOTAL"),
	))
	if value, ok := frames.firstUserTextBool("SHOWMOVEMENT", "SHOW MOVEMENT"); ok {
		record.ShowMovement = value
	}

	if number, count := parseSlashPair(frames.first("TRK", "TRCK")); number != 0 || count != 0 {
		setIntIfZero(&record.TrackNumber, number)
		setIntIfZero(&record.TrackCount, count)
	}
	if number, count := parseSlashPair(frames.first("TPA", "TPOS")); number != 0 || count != 0 {
		setIntIfZero(&record.DiscNumber, number)
		setIntIfZero(&record.DiscCount, count)
	}

	setStringIfEmpty(&record.ReleaseDate, id3ReleaseDate(frames, format))
	setStringIfEmpty(&record.OriginalReleaseDate, id3OriginalReleaseDate(frames, format))
	setStringIfEmpty(&record.RecordedDate, id3RecordedDate(frames, format))
	setIntIfZero(&record.OriginalReleaseYear, yearFromDate(record.OriginalReleaseDate))

	setInt64IfZero(&record.ItunesStoreTrackId, firstNonZeroInt64(
		id3FirstUserTextInt64(frames, "ITUNESSTORETRACKID", "ITUNES STORE TRACK ID", "CNID"),
		id3TextInt64(frames, "UFID"),
	))
	setInt64IfZero(&record.ItunesStoreAlbumId, id3FirstUserTextInt64(frames, "ITUNESSTOREALBUMID", "ITUNES STORE ALBUM ID", "PLID"))
	setStringIfEmpty(&record.PurchaseDate, frames.firstUserText("PURCHASEDATE", "PURCHASE DATE"))
	if value, ok := frames.firstBool("TCP", "TCMP"); ok {
		record.IsCompilation = value
	} else if value, ok := frames.firstUserTextBool("COMPILATION", "ISCOMPILATION", "ITUNESCOMPILATION"); ok {
		record.IsCompilation = value
	}

	if genres := id3GenreValues(frames.values("TCO", "TCON")); len(genres) > 0 {
		record.Genres = []string{}
		addGenres(record, genres...)
	}
}

func applyID3v1TrackFields(record *TrackMetadata, metadata id3v1Metadata) {
	if metadata.Artist != "" {
		record.Artists = cleanStringValues(metadata.Artist)
	}
	setStringIfEmpty(&record.Album, metadata.Album)
	setStringIfEmpty(&record.Title, metadata.Title)
	setIntIfZero(&record.TrackNumber, metadata.Track)
	setIntIfZero(&record.ReleaseYear, metadata.Year)
	if metadata.Genre != "" {
		record.Genres = []string{}
		addGenres(record, metadata.Genre)
	}
}

func id3ReleaseDate(frames id3TextFrameMap, format string) string {
	switch format {
	case formatID3v22:
		return combineID3Date(frames.first("TYE"), frames.first("TDA"))
	case formatID3v23:
		return combineID3Date(frames.first("TYER"), frames.first("TDAT"))
	case formatID3v24:
		return frames.first("TDRL", "TDRC")
	default:
		return ""
	}
}

func id3OriginalReleaseDate(frames id3TextFrameMap, format string) string {
	switch format {
	case formatID3v22:
		return frames.first("TOR")
	case formatID3v23:
		return frames.first("TORY")
	case formatID3v24:
		return frames.first("TDOR")
	default:
		return ""
	}
}

func id3RecordedDate(frames id3TextFrameMap, format string) string {
	switch format {
	case formatID3v22:
		return frames.first("TRD")
	case formatID3v23:
		return frames.first("TRDA")
	case formatID3v24:
		return frames.first("TDRC")
	default:
		return ""
	}
}

func id3FirstUserTextInt64(frames id3TextFrameMap, descriptions ...string) int64 {
	if value, ok := parseInt64Text(frames.firstUserText(descriptions...)); ok {
		return value
	}
	return 0
}

func id3TextInt64(frames id3TextFrameMap, keys ...string) int64 {
	if value, ok := parseInt64Text(frames.first(keys...)); ok {
		return value
	}
	return 0
}

func firstNonZeroInt64(values ...int64) int64 {
	for _, value := range values {
		if value != 0 {
			return value
		}
	}
	return 0
}

func addGenres(record *TrackMetadata, values ...string) {
	seen := make(map[string]bool, len(record.Genres)+len(values))
	var genres []string
	for _, value := range record.Genres {
		for _, genre := range splitFieldList(value) {
			key := strings.ToLower(genre)
			if key == "" || seen[key] {
				continue
			}
			seen[key] = true
			genres = append(genres, genre)
		}
	}
	for _, value := range values {
		for _, genre := range splitFieldList(value) {
			key := strings.ToLower(genre)
			if key == "" || seen[key] {
				continue
			}
			seen[key] = true
			genres = append(genres, genre)
		}
	}
	if genres == nil {
		genres = []string{}
	}
	record.Genres = genres
}
