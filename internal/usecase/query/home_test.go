package query_test

import (
	"context"
	"database/sql"
	"reflect"
	"testing"

	_ "github.com/mattn/go-sqlite3"
	"github.com/zanyoats/kukicha/internal/usecase/command"
	"github.com/zanyoats/kukicha/internal/usecase/query"
)

func TestHomeLoadsAssociatedLibraryCategories(t *testing.T) {
	ctx := context.Background()
	db := openHomeTestDB(t, ctx)

	_, err := db.ExecContext(ctx, `
		INSERT INTO library_artist_ids (id, artist) VALUES
			(1, 'alpha'),
			(2, 'Zebra'),
			(3, 'Unused Artist');

		INSERT INTO taxonomy_genres (id, genre) VALUES
			(10, 'Ambient'),
			(11, 'rock'),
			(12, 'Unused Genre');

		INSERT INTO taxonomy_styles (id, style, parent_genre_id) VALUES
			(20, 'Alternative', 11),
			(21, 'techno', 11),
			(22, 'Unused Style', 12);

		INSERT INTO library_albums (id, album, format) VALUES
			(100, 'First', 'album'),
			(101, 'Second', 'album');

		INSERT INTO library_album_artists (album_id, artist_id) VALUES
			(100, 2),
			(100, 1),
			(101, 2);

		INSERT INTO library_album_genres (album_id, genre_id) VALUES
			(100, 11),
			(101, 11),
			(101, 10);

		INSERT INTO library_album_styles (album_id, style_id) VALUES
			(100, 21),
			(101, 21),
			(101, 20);
	`)
	if err != nil {
		t.Fatalf("insert home query fixtures: %v", err)
	}

	got, err := query.Home(ctx, db)
	if err != nil {
		t.Fatalf("query home data: %v", err)
	}

	want := query.HomeData{
		Artists: []query.HomeItem{
			{ID: 1, Name: "alpha"},
			{ID: 2, Name: "Zebra"},
		},
		Genres: []query.HomeItem{
			{ID: 10, Name: "Ambient"},
			{ID: 11, Name: "rock"},
		},
		Styles: []query.HomeItem{
			{ID: 20, Name: "Alternative"},
			{ID: 21, Name: "techno"},
		},
	}

	if !reflect.DeepEqual(got, want) {
		t.Errorf("Home() = %#v, want %#v", got, want)
	}
}

func openHomeTestDB(t *testing.T, ctx context.Context) *sql.DB {
	t.Helper()

	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open test database: %v", err)
	}
	db.SetMaxOpenConns(1)
	t.Cleanup(func() {
		if err := db.Close(); err != nil {
			t.Errorf("close test database: %v", err)
		}
	})

	if err := command.Migrate(ctx, db); err != nil {
		t.Fatalf("migrate test database: %v", err)
	}

	return db
}
