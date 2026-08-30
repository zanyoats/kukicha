package command

import (
	"context"
	"database/sql"
	"path/filepath"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
	"github.com/zanyoats/kukicha/internal/app"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

func TestLibraryIDsSurviveRebuildsAndRootReordering(t *testing.T) {
	ctx := context.Background()
	db := openLibraryIDsTestDB(t)
	if err := Migrate(ctx, db); err != nil {
		t.Fatalf("migrate test database: %v", err)
	}
	seedLibraryIDsTestTaxonomy(t, ctx, db)

	conn, err := db.Conn(ctx)
	if err != nil {
		t.Fatalf("open scan connection: %v", err)
	}
	defer conn.Close()

	first := []libraryIDsTestTrack{
		{
			rootPos:   0,
			rootPath:  "/music/alpha",
			path:      "Album A/01 Track A.mp3",
			albumKey:  "artist-a::album-a",
			artistKey: "artist-a",
			artist:    "Artist A",
			album:     "Album A",
			title:     "Track A",
		},
		{
			rootPos:   1,
			rootPath:  "/music/beta",
			path:      "Album B/01 Track B.mp3",
			albumKey:  "artist-b::album-b",
			artistKey: "artist-b",
			artist:    "Artist B",
			album:     "Album B",
			title:     "Track B",
		},
	}
	buildLibraryIDsTestLibrary(t, ctx, conn, first)
	if got := countRows(t, db, `SELECT COUNT(*) FROM pragma_table_info('library_albums') WHERE name = 'album_key'`); got != 0 {
		t.Errorf("library_albums retains album_key column")
	}
	if got := countRows(t, db, `SELECT COUNT(*) FROM pragma_table_info('library_album_artists') WHERE name = 'artist_key'`); got != 0 {
		t.Errorf("library_album_artists retains artist_key column")
	}
	if got := countRows(t, db, `SELECT COUNT(*) FROM pragma_table_info('library_album_artists') WHERE name = 'artist'`); got != 0 {
		t.Errorf("library_album_artists retains artist column")
	}
	if got := countRows(t, db, `SELECT COUNT(*) FROM pragma_table_info('library_roots') WHERE name = 'pos'`); got != 0 {
		t.Errorf("library_roots retains pos column")
	}
	for _, table := range []string{
		"library_album_artist_ids",
		"library_album_genre_ids",
		"library_album_style_ids",
	} {
		if got := countRows(
			t,
			db,
			`SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = ?`,
			table,
		); got != 0 {
			t.Errorf("obsolete mapping table %s still exists", table)
		}
	}
	for _, table := range []string{
		"library_roots",
		"library_albums",
		"library_album_artists",
		"library_album_genres",
		"library_album_styles",
		"library_track_genres",
		"library_track_styles",
		"library_tracks",
	} {
		if got := countRows(
			t,
			db,
			`SELECT COUNT(*) FROM sqlite_sequence WHERE name = ?`,
			table,
		); got != 0 {
			t.Errorf("%s owns an unexpected AUTOINCREMENT sequence", table)
		}
	}

	rootAlphaID := mappedID(t, db, `SELECT id FROM library_root_ids WHERE root_path = '/music/alpha'`)
	rootBetaID := mappedID(t, db, `SELECT id FROM library_root_ids WHERE root_path = '/music/beta'`)
	albumAID := mappedID(t, db, `SELECT id FROM library_album_ids WHERE album_key = 'artist-a::album-a'`)
	artistAID := mappedID(t, db, `SELECT id FROM library_artist_ids WHERE artist = 'Artist A'`)
	genreID := mappedID(t, db, `SELECT id FROM taxonomy_genres WHERE genre = 'Rock'`)
	styleID := mappedID(t, db, `SELECT id FROM taxonomy_styles WHERE style = 'Alternative'`)
	trackAID := mappedID(t, db, `SELECT id FROM library_track_ids WHERE root_path = '/music/alpha' AND path = 'Album A/01 Track A.mp3'`)
	trackBID := mappedID(t, db, `SELECT id FROM library_track_ids WHERE root_path = '/music/beta' AND path = 'Album B/01 Track B.mp3'`)
	trackSequence := mappedID(t, db, `SELECT seq FROM sqlite_sequence WHERE name = 'library_track_ids'`)

	reordered := []libraryIDsTestTrack{
		first[1],
		first[0],
	}
	reordered[0].rootPos = 0
	reordered[1].rootPos = 1
	reordered[1].title = "Retagged Track A"
	buildLibraryIDsTestLibrary(t, ctx, conn, reordered)

	assertMappedID(t, db, `SELECT id FROM library_roots WHERE path = '/music/alpha'`, rootAlphaID)
	assertMappedID(t, db, `SELECT id FROM library_roots WHERE path = '/music/beta'`, rootBetaID)
	assertMappedID(t, db, `SELECT albums.id FROM library_albums AS albums JOIN library_album_ids AS ids ON ids.id = albums.id WHERE ids.album_key = 'artist-a::album-a'`, albumAID)
	assertMappedID(t, db, `SELECT artist_id FROM library_album_artists WHERE album_id = (SELECT id FROM library_album_ids WHERE album_key = 'artist-a::album-a')`, artistAID)
	assertMappedID(t, db, `SELECT genre_id FROM library_album_genres WHERE album_id = (SELECT id FROM library_album_ids WHERE album_key = 'artist-a::album-a')`, genreID)
	assertMappedID(t, db, `SELECT style_id FROM library_album_styles WHERE album_id = (SELECT id FROM library_album_ids WHERE album_key = 'artist-a::album-a')`, styleID)
	assertMappedID(t, db, `SELECT tracks.id FROM library_tracks AS tracks JOIN library_roots AS roots ON roots.id = tracks.root_id WHERE roots.path = '/music/alpha' AND tracks.path = 'Album A/01 Track A.mp3'`, trackAID)
	assertMappedID(t, db, `SELECT tracks.id FROM library_tracks AS tracks JOIN library_roots AS roots ON roots.id = tracks.root_id WHERE roots.path = '/music/beta' AND tracks.path = 'Album B/01 Track B.mp3'`, trackBID)
	assertMappedID(t, db, `SELECT seq FROM sqlite_sequence WHERE name = 'library_track_ids'`, trackSequence)

	buildLibraryIDsTestLibrary(t, ctx, conn, reordered[:1])
	if got := countRows(t, db, `SELECT COUNT(*) FROM library_tracks WHERE id = ?`, trackAID); got != 0 {
		t.Errorf("removed track remains in library: count = %d", got)
	}
	assertMappedID(t, db, `SELECT id FROM library_track_ids WHERE root_path = '/music/alpha' AND path = 'Album A/01 Track A.mp3'`, trackAID)
	assertMappedID(t, db, `SELECT id FROM library_artist_ids WHERE artist = 'Artist A'`, artistAID)

	renamed := reordered[1]
	renamed.path = "Album A/01 Track A Renamed.mp3"
	buildLibraryIDsTestLibrary(t, ctx, conn, []libraryIDsTestTrack{reordered[0], renamed})
	renamedTrackID := mappedID(t, db, `SELECT id FROM library_track_ids WHERE root_path = '/music/alpha' AND path = 'Album A/01 Track A Renamed.mp3'`)
	if got, want := renamedTrackID, trackSequence+1; got != want {
		t.Errorf("renamed track ID = %d, want next unused ID %d", got, want)
	}

	buildLibraryIDsTestLibrary(t, ctx, conn, reordered)
	assertMappedID(t, db, `SELECT tracks.id FROM library_tracks AS tracks JOIN library_roots AS roots ON roots.id = tracks.root_id WHERE roots.path = '/music/alpha' AND tracks.path = 'Album A/01 Track A.mp3'`, trackAID)
	assertMappedID(t, db, `SELECT id FROM library_track_ids WHERE root_path = '/music/alpha' AND path = 'Album A/01 Track A Renamed.mp3'`, renamedTrackID)

	changedAlbum := reordered[1]
	changedAlbum.albumKey = "artist-a::album-a-remastered"
	changedAlbum.album = "Album A Remastered"
	buildLibraryIDsTestLibrary(t, ctx, conn, []libraryIDsTestTrack{changedAlbum})
	assertMappedID(t, db, `SELECT tracks.id FROM library_tracks AS tracks JOIN library_roots AS roots ON roots.id = tracks.root_id WHERE roots.path = '/music/alpha' AND tracks.path = 'Album A/01 Track A.mp3'`, trackAID)
	changedAlbumID := mappedID(t, db, `SELECT id FROM library_album_ids WHERE album_key = 'artist-a::album-a-remastered'`)
	if changedAlbumID == albumAID {
		t.Errorf("changed album key reused album ID %d", albumAID)
	}
	assertMappedID(t, db, `SELECT artist_id FROM library_album_artists WHERE album_id = (SELECT id FROM library_album_ids WHERE album_key = 'artist-a::album-a-remastered')`, artistAID)
	assertMappedID(t, db, `SELECT genre_id FROM library_album_genres WHERE album_id = (SELECT id FROM library_album_ids WHERE album_key = 'artist-a::album-a-remastered')`, genreID)
	assertMappedID(t, db, `SELECT style_id FROM library_album_styles WHERE album_id = (SELECT id FROM library_album_ids WHERE album_key = 'artist-a::album-a-remastered')`, styleID)

	movedRoot := reordered[1]
	movedRoot.rootPath = "/mounted/music/alpha"
	buildLibraryIDsTestLibrary(t, ctx, conn, []libraryIDsTestTrack{movedRoot})
	movedRootID := mappedID(t, db, `SELECT id FROM library_root_ids WHERE root_path = '/mounted/music/alpha'`)
	if movedRootID == rootAlphaID {
		t.Errorf("moved root reused root ID %d", rootAlphaID)
	}
	movedTrackID := mappedID(t, db, `SELECT id FROM library_track_ids WHERE root_path = '/mounted/music/alpha' AND path = 'Album A/01 Track A.mp3'`)
	if movedTrackID == trackAID {
		t.Errorf("track in moved root reused track ID %d", trackAID)
	}
}

func TestAlbumGenresAndStylesRollUpDistinctTrackValues(t *testing.T) {
	ctx := context.Background()
	db := openLibraryIDsTestDB(t)
	if err := Migrate(ctx, db); err != nil {
		t.Fatalf("migrate test database: %v", err)
	}
	seedLibraryIDsTestTaxonomy(t, ctx, db)

	conn, err := db.Conn(ctx)
	if err != nil {
		t.Fatalf("open scan connection: %v", err)
	}
	defer conn.Close()

	tracks := []libraryIDsTestTrack{
		{
			rootPos:   0,
			rootPath:  "/music",
			path:      "Album/01 First.mp3",
			albumKey:  "artist::album",
			artistKey: "artist",
			artist:    "Artist",
			album:     "Album",
			title:     "First",
		},
		{
			rootPos:   0,
			rootPath:  "/music",
			path:      "Album/02 Second.mp3",
			albumKey:  "artist::album",
			artistKey: "artist",
			artist:    "Artist",
			album:     "Album",
			title:     "Second",
		},
	}
	buildLibraryIDsTestLibrary(t, ctx, conn, tracks)

	albumID := mappedID(t, db, `SELECT id FROM library_album_ids WHERE album_key = 'artist::album'`)
	genreID := mappedID(t, db, `SELECT id FROM taxonomy_genres WHERE genre = 'Rock'`)
	styleID := mappedID(t, db, `SELECT id FROM taxonomy_styles WHERE style = 'Alternative'`)
	if got := countRows(t, db, `SELECT COUNT(*) FROM library_track_genres WHERE genre_id = ?`, genreID); got != 2 {
		t.Errorf("track genre count = %d, want 2", got)
	}
	if got := countRows(t, db, `SELECT COUNT(*) FROM library_track_styles WHERE style_id = ?`, styleID); got != 2 {
		t.Errorf("track style count = %d, want 2", got)
	}
	if got := countRows(t, db, `SELECT COUNT(*) FROM library_album_genres WHERE album_id = ? AND genre_id = ?`, albumID, genreID); got != 1 {
		t.Errorf("album genre count = %d, want 1", got)
	}
	if got := countRows(t, db, `SELECT COUNT(*) FROM library_album_styles WHERE album_id = ? AND style_id = ?`, albumID, styleID); got != 1 {
		t.Errorf("album style count = %d, want 1", got)
	}
}

func TestAlbumArtistLookupUsesGlobalArtistID(t *testing.T) {
	ctx := context.Background()
	db := openLibraryIDsTestDB(t)
	if err := Migrate(ctx, db); err != nil {
		t.Fatalf("migrate test database: %v", err)
	}
	seedLibraryIDsTestTaxonomy(t, ctx, db)

	conn, err := db.Conn(ctx)
	if err != nil {
		t.Fatalf("open scan connection: %v", err)
	}
	defer conn.Close()

	tracks := []libraryIDsTestTrack{
		{
			rootPos:   0,
			rootPath:  "/music",
			path:      "First/Track.mp3",
			albumKey:  "artist::first",
			artistKey: "artist",
			artist:    "Artist",
			album:     "First",
			title:     "First Track",
		},
		{
			rootPos:   0,
			rootPath:  "/music",
			path:      "Second/Track.mp3",
			albumKey:  "artist::second",
			artistKey: "artist",
			artist:    "artist",
			album:     "Second",
			title:     "Second Track",
		},
	}
	buildLibraryIDsTestLibrary(t, ctx, conn, tracks)

	artistID := mappedID(t, db, `SELECT id FROM library_artist_ids WHERE artist = 'ARTIST'`)
	if got := countRows(t, db, `SELECT COUNT(*) FROM library_artist_ids WHERE artist = 'artist'`); got != 1 {
		t.Errorf("case-insensitive artist ID count = %d, want 1", got)
	}
	if got := countRows(
		t,
		db,
		`SELECT COUNT(*)
		 FROM library_albums AS albums
		 JOIN library_album_artists AS album_artists
		     ON album_artists.album_id = albums.id
		 WHERE album_artists.artist_id = ?`,
		artistID,
	); got != 2 {
		t.Errorf("albums for artist count = %d, want 2", got)
	}

	albums, err := query.Albums(ctx, db)
	if err != nil {
		t.Fatalf("query albums with global artists: %v", err)
	}
	if len(albums) != 2 {
		t.Fatalf("queried album count = %d, want 2", len(albums))
	}
	for _, album := range albums {
		if len(album.Artists) != 1 || album.Artists[0] != "Artist" {
			t.Errorf("album %q artists = %v, want [Artist]", album.Title, album.Artists)
		}
	}
}

func TestSeedTaxonomyUsesStableIntegerRelationships(t *testing.T) {
	ctx := context.Background()
	db := openLibraryIDsTestDB(t)
	if err := Migrate(ctx, db); err != nil {
		t.Fatalf("migrate test database: %v", err)
	}

	catalog, err := app.LoadTaxonomy()
	if err != nil {
		t.Fatalf("load taxonomy: %v", err)
	}
	if err := SeedTaxonomy(ctx, db, catalog, "Unknown"); err != nil {
		t.Fatalf("seed taxonomy: %v", err)
	}

	unknownID := mappedID(t, db, `SELECT id FROM taxonomy_genres WHERE genre = 'Unknown'`)
	if got := countRows(
		t,
		db,
		`SELECT COUNT(*)
		 FROM taxonomy_styles AS styles
		 LEFT JOIN taxonomy_genres AS genres
		     ON genres.id = styles.parent_genre_id
		 WHERE genres.id IS NULL`,
	); got != 0 {
		t.Errorf("styles with missing parent genre = %d, want 0", got)
	}
	if got := countRows(t, db, `SELECT COUNT(*) FROM taxonomy_styles`); got == 0 {
		t.Fatal("seeded taxonomy contains no styles")
	}

	if err := SeedTaxonomy(ctx, db, catalog, "Unknown"); err != nil {
		t.Fatalf("reseed taxonomy: %v", err)
	}
	assertMappedID(t, db, `SELECT id FROM taxonomy_genres WHERE genre = 'Unknown'`, unknownID)
}

type libraryIDsTestTrack struct {
	rootPos   int
	rootPath  string
	path      string
	albumKey  string
	artistKey string
	artist    string
	album     string
	title     string
}

func buildLibraryIDsTestLibrary(
	t *testing.T,
	ctx context.Context,
	conn *sql.Conn,
	tracks []libraryIDsTestTrack,
) {
	t.Helper()

	if err := PrepareScanResults(ctx, conn, 1024); err != nil {
		t.Fatalf("prepare scan results: %v", err)
	}

	for _, track := range tracks {
		scanned := &app.ScannedFile{
			FileIdentity: app.FileIdentity{
				Root: app.Root{
					RootPos:  track.rootPos,
					RootPath: track.rootPath,
				},
				Path: track.path,
				Ext:  ".mp3",
				Stem: track.title,
			},
			FileSnapshot: app.FileSnapshot{
				FileType:    app.FileTypeAudio,
				FileSize:    1,
				FileModTime: time.Unix(1, 0),
			},
			Metadata: &app.TrackMetadata{
				Format:       "MP3",
				Artists:      []string{track.artist},
				AlbumArtists: []string{track.artist},
				Album:        track.album,
				Title:        track.title,
				Genres:       []string{"Rock", "Alternative"},
			},
			NormalizedMetadata: &app.NormalizedTrackMetadata{
				TrackDisplay:              track.title,
				AlbumKey:                  track.albumKey,
				AlbumDisplay:              track.album,
				RawAlbumArtistDisplays:    []string{track.artist},
				MappedAlbumArtistKeys:     []string{track.artistKey},
				MappedAlbumArtistDisplays: []string{track.artist},
				RawTrackArtistDisplays:    []string{track.artist},
				MappedTrackArtistDisplays: []string{track.artist},
				Genres:                    []string{"Rock", "Alternative"},
			},
		}
		if err := InsertScannedFile(ctx, conn, scanned, 1024, nil); err != nil {
			t.Fatalf("insert scanned track %q: %v", track.path, err)
		}
	}

	if _, err := BuildLibraryFromScan(
		ctx,
		conn,
		map[string]bool{".mp3": true},
		map[string]bool{},
		map[string]bool{},
		"Unknown",
		map[string]bool{},
	); err != nil {
		t.Fatalf("build library: %v", err)
	}
}

func openLibraryIDsTestDB(t *testing.T) *sql.DB {
	t.Helper()

	path := filepath.Join(t.TempDir(), "library-ids.sqlite")
	db, err := sql.Open("sqlite3", path+"?_foreign_keys=on")
	if err != nil {
		t.Fatalf("open test database: %v", err)
	}
	db.SetMaxOpenConns(2)
	t.Cleanup(func() { _ = db.Close() })
	return db
}

func seedLibraryIDsTestTaxonomy(
	t *testing.T,
	ctx context.Context,
	db *sql.DB,
) {
	t.Helper()

	for _, statement := range []string{
		`INSERT INTO taxonomy_genres (genre) VALUES ('Unknown'), ('Rock')`,
		`INSERT INTO taxonomy_styles (style, parent_genre_id) VALUES ('Alternative', (SELECT id FROM taxonomy_genres WHERE genre = 'Rock'))`,
		`INSERT INTO taxonomy (id, kind, name, parent, source, alias, status) VALUES (1, 'style', 'Alternative', 'Rock', 'mb_genres_v0', 'Alternative', 'canonical')`,
	} {
		if _, err := db.ExecContext(ctx, statement); err != nil {
			t.Fatalf("seed test taxonomy: %v", err)
		}
	}
}

func mappedID(t *testing.T, db *sql.DB, query string) int64 {
	t.Helper()

	var id int64
	if err := db.QueryRow(query).Scan(&id); err != nil {
		t.Fatalf("query mapped ID: %v", err)
	}
	return id
}

func assertMappedID(t *testing.T, db *sql.DB, query string, want int64) {
	t.Helper()

	if got := mappedID(t, db, query); got != want {
		t.Errorf("mapped ID = %d, want %d", got, want)
	}
}

func countRows(t *testing.T, db *sql.DB, query string, args ...any) int {
	t.Helper()

	var count int
	if err := db.QueryRow(query, args...).Scan(&count); err != nil {
		t.Fatalf("count rows: %v", err)
	}
	return count
}
