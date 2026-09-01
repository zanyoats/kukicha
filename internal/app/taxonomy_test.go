package app

import (
	"bytes"
	"crypto/sha256"
	"testing"
)

func TestLoadTaxonomyChecksum(t *testing.T) {
	catalog, err := LoadTaxonomy()
	if err != nil {
		t.Fatalf("load taxonomy: %v", err)
	}

	want := sha256.Sum256(rawData)
	if !bytes.Equal(catalog.Checksum, want[:]) {
		t.Errorf("taxonomy checksum = %x, want %x", catalog.Checksum, want)
	}
}
