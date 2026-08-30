package command

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/zanyoats/kukicha/internal/app"
)

func TestReadLocalArtworkFileLimit(t *testing.T) {
	path := filepath.Join(t.TempDir(), "cover.jpg")
	if err := os.WriteFile(path, []byte("1234"), 0o600); err != nil {
		t.Fatalf("write artwork: %v", err)
	}

	got, err := readLocalArtworkFile(path, 4)
	if err != nil {
		t.Fatalf("read artwork at limit: %v", err)
	}
	if got, want := string(got), "1234"; got != want {
		t.Errorf("artwork = %q, want %q", got, want)
	}

	if err := os.WriteFile(path, []byte("12345"), 0o600); err != nil {
		t.Fatalf("replace artwork: %v", err)
	}
	if _, err := readLocalArtworkFile(path, 4); err == nil {
		t.Fatal("read oversized artwork succeeded, want an error")
	}
}

func TestITunesArtworkLookupSkippedForEmbeddedArtwork(t *testing.T) {
	lookupCalls := 0
	lookup := func(
		context.Context,
		app.ITunesLookupKind,
		string,
	) (app.ITunesLookupResult, bool, error) {
		lookupCalls++
		return app.ITunesLookupResult{ResultKind: app.ITunesResultMissing}, false, nil
	}

	scanned := &app.ScannedFile{
		Metadata: &app.TrackMetadata{
			ItunesStoreAlbumId: 1,
			ItunesStoreTrackId: 2,
		},
		Artwork: []app.EmbeddedArtwork{{PictureType: "front_cover"}},
	}
	if err := cacheScannedFileITunesArtwork(
		context.Background(),
		nil,
		scanned,
		lookup,
	); err != nil {
		t.Fatalf("cache iTunes artwork: %v", err)
	}
	if lookupCalls != 0 {
		t.Errorf("iTunes lookup calls = %d, want 0", lookupCalls)
	}
}

func TestITunesArtworkLookupUsedWithoutEmbeddedArtwork(t *testing.T) {
	lookupCalls := 0
	lookup := func(
		context.Context,
		app.ITunesLookupKind,
		string,
	) (app.ITunesLookupResult, bool, error) {
		lookupCalls++
		return app.ITunesLookupResult{ResultKind: app.ITunesResultMissing}, false, nil
	}

	scanned := &app.ScannedFile{
		Metadata: &app.TrackMetadata{
			ItunesStoreAlbumId: 1,
			ItunesStoreTrackId: 2,
		},
	}
	if err := cacheScannedFileITunesArtwork(
		context.Background(),
		nil,
		scanned,
		lookup,
	); err != nil {
		t.Fatalf("cache iTunes artwork: %v", err)
	}
	if lookupCalls != 2 {
		t.Errorf("iTunes lookup calls = %d, want 2", lookupCalls)
	}
}
