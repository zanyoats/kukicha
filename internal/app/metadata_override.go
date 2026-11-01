package app

// MetadataSource identifies a provider entity independently of its display URL.
type MetadataSource struct {
	Provider   string `json:"provider"`
	EntityType string `json:"entity_type"`
	EntityID   string `json:"entity_id"`
	SourceURL  string `json:"source_url"`
}

// MetadataOverride contains provider values before the library's normalization rules.
// Empty fields use file metadata or ordinary artwork as their fallback.
type MetadataOverride struct {
	MetadataSource
	Genres             []string `json:"genres"`
	ArtworkURL         string   `json:"artwork_url"`
	ArtworkComplete    bool     `json:"artwork_complete"`
	ThumbnailCacheName string   `json:"thumbnail_cache_name"`
}
