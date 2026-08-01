package command

import (
	"bytes"
	"context"
	"database/sql"
	"image"
	"image/color"
	"image/png"
	"os"
	"path/filepath"
	"testing"

	_ "github.com/mattn/go-sqlite3"
	"github.com/zanyoats/kukicha/internal/app"
)

func TestResizeToWidthPreservesAspectRatio(t *testing.T) {
	src := image.NewNRGBA(image.Rect(10, 20, 410, 220))

	got := resizeToWidth(src, 300)
	if got.Bounds().Dx() != 300 || got.Bounds().Dy() != 150 {
		t.Fatalf("resizeToWidth() bounds = %v, want width 300 and height 150", got.Bounds())
	}
}

func TestBuildLibraryTrackArtworkPrefersUsableCoverSidecar(t *testing.T) {
	ctx := context.Background()
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	if _, err := db.Exec(`
		CREATE TABLE library_roots (
			id INTEGER PRIMARY KEY,
			path TEXT NOT NULL
		);
		CREATE TABLE library_tracks (
			id INTEGER PRIMARY KEY,
			root_id INTEGER NOT NULL,
			path TEXT NOT NULL
		);
		CREATE TABLE library_track_sidecars (
			id INTEGER PRIMARY KEY,
			track_id INTEGER NOT NULL,
			path TEXT NOT NULL,
			file_type INTEGER NOT NULL
		);
		CREATE TABLE library_track_embedded_artwork (
			id INTEGER PRIMARY KEY,
			track_id INTEGER NOT NULL,
			picture_type TEXT NOT NULL,
			artwork_bytes BLOB NOT NULL
		);
		CREATE TABLE library_track_artwork (
			id INTEGER PRIMARY KEY,
			track_id INTEGER NOT NULL,
			width_px INTEGER NOT NULL,
			artwork_content_type TEXT,
			artwork_content_size INTEGER NOT NULL,
			artwork_bytes BLOB NOT NULL
		);
	`); err != nil {
		t.Fatal(err)
	}

	root := t.TempDir()
	sidecarBytes := solidPNG(t, color.NRGBA{R: 255, A: 255})
	if err := os.WriteFile(filepath.Join(root, "01-cover.png"), sidecarBytes, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "00-invalid.png"), []byte("not an image"), 0o600); err != nil {
		t.Fatal(err)
	}

	embeddedBytes := solidPNG(t, color.NRGBA{B: 255, A: 255})
	if _, err := db.Exec(`
		INSERT INTO library_roots (id, path) VALUES (1, ?);
		INSERT INTO library_tracks (id, root_id, path) VALUES
			(1, 1, 'one.mp3'),
			(2, 1, 'two.mp3'),
			(3, 1, 'three.mp3'),
			(4, 1, 'four.mp3');
		INSERT INTO library_track_sidecars (id, track_id, path, file_type) VALUES
			(1, 1, '00-invalid.png', ?),
			(2, 1, '01-cover.png', ?),
			(3, 2, '00-invalid.png', ?),
			(4, 3, '01-cover.png', ?);
		INSERT INTO library_track_embedded_artwork (id, track_id, picture_type, artwork_bytes) VALUES
			(1, 1, 'front_cover', ?),
			(2, 2, 'front_cover', ?),
			(3, 3, 'front_cover', ?),
			(4, 4, 'back_cover', ?);
	`, root,
		app.FileTypeSidecarCover,
		app.FileTypeSidecarCover,
		app.FileTypeSidecarCover,
		app.FileTypeSidecarOther,
		embeddedBytes,
		embeddedBytes,
		embeddedBytes,
		embeddedBytes,
	); err != nil {
		t.Fatal(err)
	}

	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := buildLibraryTrackArtwork(ctx, tx); err != nil {
		_ = tx.Rollback()
		t.Fatal(err)
	}
	if err := tx.Commit(); err != nil {
		t.Fatal(err)
	}

	rows, err := db.Query(`
		SELECT track_id, width_px, artwork_content_type, artwork_content_size, artwork_bytes
		FROM library_track_artwork
		ORDER BY track_id, width_px;
	`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()

	type result struct {
		trackID int
		width   int
		color   color.NRGBA
	}
	var results []result
	for rows.Next() {
		var trackID, width, contentSize int
		var contentType string
		var artworkBytes []byte
		if err := rows.Scan(&trackID, &width, &contentType, &contentSize, &artworkBytes); err != nil {
			t.Fatal(err)
		}
		if contentType != "image/png" {
			t.Errorf("content type = %q, want image/png", contentType)
		}
		if contentSize != len(artworkBytes) {
			t.Errorf("content size = %d, want %d", contentSize, len(artworkBytes))
		}

		artwork, err := png.Decode(bytes.NewReader(artworkBytes))
		if err != nil {
			t.Fatal(err)
		}
		if artwork.Bounds().Dx() != width || artwork.Bounds().Dy() != width/2 {
			t.Errorf("track %d artwork bounds = %v, want %dx%d", trackID, artwork.Bounds(), width, width/2)
		}
		results = append(results, result{
			trackID: trackID,
			width:   width,
			color:   color.NRGBAModel.Convert(artwork.At(0, 0)).(color.NRGBA),
		})
	}
	if err := rows.Err(); err != nil {
		t.Fatal(err)
	}

	if len(results) != 6 {
		t.Fatalf("got %d artwork rows, want 6", len(results))
	}
	for _, got := range results {
		if got.width != AlbumArtwork64 && got.width != AlbumArtwork256 {
			t.Errorf("track %d width = %d, want %d or %d", got.trackID, got.width, AlbumArtwork64, AlbumArtwork256)
		}

		want := color.NRGBA{B: 255, A: 255}
		if got.trackID == 1 {
			want = color.NRGBA{R: 255, A: 255}
		}
		if got.color != want {
			t.Errorf("track %d color = %#v, want %#v", got.trackID, got.color, want)
		}
	}
}

func TestEncodeLibraryTrackArtworkUsesSourceStorageFormat(t *testing.T) {
	src := image.NewNRGBA(image.Rect(0, 0, 20, 10))

	tests := []struct {
		sourceFormat string
		wantFormat   string
		wantMIME     string
	}{
		{sourceFormat: "jpeg", wantFormat: "jpeg", wantMIME: "image/jpeg"},
		{sourceFormat: "png", wantFormat: "png", wantMIME: "image/png"},
		{sourceFormat: "gif", wantFormat: "png", wantMIME: "image/png"},
		{sourceFormat: "webp", wantFormat: "png", wantMIME: "image/png"},
	}

	for _, tt := range tests {
		t.Run(tt.sourceFormat, func(t *testing.T) {
			artworkBytes, mime, err := encodeLibraryTrackArtwork(src, tt.sourceFormat)
			if err != nil {
				t.Fatal(err)
			}
			if mime != tt.wantMIME {
				t.Errorf("MIME type = %q, want %q", mime, tt.wantMIME)
			}

			_, format, err := image.Decode(bytes.NewReader(artworkBytes))
			if err != nil {
				t.Fatal(err)
			}
			if format != tt.wantFormat {
				t.Errorf("encoded format = %q, want %q", format, tt.wantFormat)
			}
		})
	}
}

func solidPNG(t *testing.T, fill color.NRGBA) []byte {
	t.Helper()

	src := image.NewNRGBA(image.Rect(0, 0, 20, 10))
	for y := range src.Bounds().Dy() {
		for x := range src.Bounds().Dx() {
			src.SetNRGBA(x, y, fill)
		}
	}

	var encoded bytes.Buffer
	if err := png.Encode(&encoded, src); err != nil {
		t.Fatal(err)
	}
	return encoded.Bytes()
}
