package web

import (
	"context"
	"database/sql"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	_ "github.com/mattn/go-sqlite3"
	"github.com/zanyoats/kukicha/internal/usecase/command"
)

func TestShowHomeRendersLibraryCategories(t *testing.T) {
	ctx := context.Background()
	db := openHomeHandlerTestDB(t, ctx)

	_, err := db.ExecContext(ctx, `
		INSERT INTO library_artist_ids (id, artist) VALUES (11, 'Artist One');
		INSERT INTO taxonomy_genres (id, genre) VALUES (12, 'Genre One');
		INSERT INTO taxonomy_styles (id, style, parent_genre_id)
		VALUES (13, 'Style One', 12);
		INSERT INTO library_albums (id, album, format)
		VALUES (14, 'Album One', 'album');
		INSERT INTO library_album_artists (album_id, artist_id) VALUES (14, 11);
		INSERT INTO library_album_genres (album_id, genre_id) VALUES (14, 12);
		INSERT INTO library_album_styles (album_id, style_id) VALUES (14, 13);
	`)
	if err != nil {
		t.Fatalf("insert home handler fixtures: %v", err)
	}

	renderer, err := NewPageRenderer()
	if err != nil {
		t.Fatalf("create page renderer: %v", err)
	}

	router := appRoutes{
		appBasePath: "/kukicha/app",
		renderer:    renderer,
		db:          db,
	}
	request := httptest.NewRequest(http.MethodGet, "/kukicha/app", nil)
	response := httptest.NewRecorder()

	router.showHome(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d", response.Code, http.StatusOK)
	}

	body := response.Body.String()
	if got := strings.Count(body, `<ul class="item-grid">`); got != 3 {
		t.Errorf("item grid count = %d, want 3", got)
	}
	if got := strings.Count(body, `<li class="radio-card">`); got != 3 {
		t.Errorf("radio card count = %d, want 3", got)
	}
	if got := strings.Count(body, `class="radio-card-name"`); got != 3 {
		t.Errorf("radio card name count = %d, want 3", got)
	}
	if got := strings.Count(
		body,
		`<span class="radio-card-leader" aria-hidden="true"></span>`,
	); got != 3 {
		t.Errorf("radio card leader count = %d, want 3", got)
	}
	if got := strings.Count(
		body,
		`<a class="radio-card-link" href="">(radio)</a>`,
	); got != 3 {
		t.Errorf("radio link count = %d, want 3", got)
	}

	wants := []string{
		"<h1>Browse</h1>",
		"<h2>Artists</h2>",
		`href="/kukicha/app/library/artist?artist_id=11"`,
		`title="Artist One"`,
		"<h2>Genres</h2>",
		`href="/kukicha/app/library/genre?genre_id=12"`,
		`title="Genre One"`,
		"<h2>Styles</h2>",
		`href="/kukicha/app/library/style?style_id=13"`,
		`title="Style One"`,
	}
	for _, want := range wants {
		if !strings.Contains(body, want) {
			t.Errorf("response body does not contain %q", want)
		}
	}
}

func TestShowHomeReturnsInternalServerErrorWhenQueryFails(t *testing.T) {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open test database: %v", err)
	}
	if err := db.Close(); err != nil {
		t.Fatalf("close test database: %v", err)
	}

	router := appRoutes{
		appBasePath: "/kukicha/app",
		db:          db,
	}
	request := httptest.NewRequest(http.MethodGet, "/kukicha/app", nil)
	response := httptest.NewRecorder()

	router.showHome(response, request)

	if response.Code != http.StatusInternalServerError {
		t.Errorf(
			"status = %d, want %d",
			response.Code,
			http.StatusInternalServerError,
		)
	}
}

func openHomeHandlerTestDB(t *testing.T, ctx context.Context) *sql.DB {
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
