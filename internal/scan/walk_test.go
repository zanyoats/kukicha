package scan

import (
	"testing"

	"github.com/zanyoats/kukicha/internal/app"
)

func TestFilterEmbeddedArtwork(t *testing.T) {
	scannedFile := &app.ScannedFile{
		FileIdentity: app.FileIdentity{Path: "album/track.mp3"},
		Artwork: []app.EmbeddedArtwork{
			{ArtworkContentSize: 4, ArtworkBytes: make([]byte, 4)},
			{ArtworkContentSize: 5, ArtworkBytes: make([]byte, 5)},
		},
	}

	filterEmbeddedArtwork(scannedFile, 4)

	if got, want := len(scannedFile.Artwork), 1; got != want {
		t.Fatalf("artwork count = %d, want %d", got, want)
	}
	if got, want := scannedFile.Artwork[0].ArtworkContentSize, 4; got != want {
		t.Errorf("retained artwork size = %d, want %d", got, want)
	}
}
