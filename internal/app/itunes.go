package app

import (
	"time"
)

const (
	ItunesLookupEndpoint  = "https://itunes.apple.com/lookup"
	ItunesLookupMaxBytes  = 2 * 1024 * 1024
	ItunesArtworkMaxBytes = 25 * 1024 * 1024
	ItunesRequestTimeout  = 20 * time.Second
	// Apple documents an approximate Search API limit of 20 requests per
	// minute. Leave a small margin below that limit.
	ItunesLookupInterval = 3250 * time.Millisecond
)

type ITunesLookupKind string

const (
	ITunesLookupAlbum ITunesLookupKind = "album"
	ITunesLookupTrack ITunesLookupKind = "track"
)

type ITunesResultKind string

const (
	ITunesResultHit     ITunesResultKind = "hit"
	ITunesResultMissing ITunesResultKind = "missing"
)

type ITunesLookupResult struct {
	LookupKind  ITunesLookupKind
	LookupID    string
	ResultKind  ITunesResultKind
	LookupURL   string
	ArtworkURL  string
	ContentType string
	Bytes       []byte
}

func ITunesLookupCacheKey(
	kind ITunesLookupKind,
	lookupID string,
) string {
	return string(kind) + ":" + lookupID
}
