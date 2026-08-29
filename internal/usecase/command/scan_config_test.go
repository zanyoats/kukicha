package command

import (
	"os"
	"path/filepath"
	"testing"
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
