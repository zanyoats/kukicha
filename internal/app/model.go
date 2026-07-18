package app

import "time"

type FileType int

const (
	FileTypeAudio FileType = iota
	FileTypeSidecarCover
	FileTypeSidecarOther
	FileTypePlaylist
)

type AppMetadataKey int

// TODO (?) add key for what version library was built on
const (
	LibraryGeneratedAt AppMetadataKey = iota
	LibrarySupportedExtensionsJson
)

type Root struct {
	RootPos  int
	RootPath string
}

type FileIdentity struct {
	Root
	Path string
	Ext  string
	Stem string
}

type FileSnapshot struct {
	FileType    FileType
	FileSize    int64
	FileModTime time.Time
}

type TrackMetadata struct {
	Format              string   `json:"format"`
	Artists             []string `json:"artists"`
	ArtistSort          []string `json:"artist_sort"`
	AlbumArtists        []string `json:"album_artists"`
	AlbumArtistSort     []string `json:"album_artist_sort"`
	Performers          []string `json:"performers"`
	Composer            string   `json:"composer"`
	Album               string   `json:"album"`
	AlbumSort           string   `json:"album_sort"`
	Title               string   `json:"title"`
	Work                string   `json:"work"`
	Grouping            string   `json:"grouping"`
	MovementName        string   `json:"movement_name"`
	MovementNumber      int      `json:"movement_number"`
	MovementCount       int      `json:"movement_count"`
	ShowMovement        bool     `json:"show_movement"`
	TrackNumber         int      `json:"track_number"`
	TrackCount          int      `json:"track_count"`
	DiscNumber          int      `json:"disc_number"`
	DiscCount           int      `json:"disc_count"`
	ReleaseDate         string   `json:"release_date"`
	OriginalReleaseDate string   `json:"original_release_date"`
	RecordedDate        string   `json:"recorded_date"`
	ReleaseYear         int      `json:"release_year"`
	OriginalReleaseYear int      `json:"original_release_year"`
	ItunesStoreTrackId  int64    `json:"itunes_store_track_id"`
	ItunesStoreAlbumId  int64    `json:"itunes_store_album_id"`
	PurchaseDate        string   `json:"purchase_date"`
	Genres              []string `json:"genres"`
	IsCompilation       *bool    `json:"is_compilation"`
}

type NormalizedTrackMetadata struct {
	TrackDisplay              string   `json:"track_display"`
	AlbumKey                  string   `json:"album_key"`
	AlbumDisplay              string   `json:"album_display"`
	RawAlbumArtistDisplays    []string `json:"raw_album_artist_displays"`
	MappedAlbumArtistKeys     []string `json:"mapped_album_artist_keys"`
	MappedAlbumArtistDisplays []string `json:"mapped_album_artist_displays"`
	RawTrackArtistDisplays    []string `json:"raw_track_artist_displays"`
	MappedTrackArtistDisplays []string `json:"mapped_track_artist_displays"`
	Genres                    []string `json:"genres"`
}

type EmbeddedArtwork struct {
	PictureType        string
	ArtworkContentType string
	ArtworkContentSize int
	ArtworkBytes       []byte
}

type ScannedFile struct {
	FileIdentity
	FileSnapshot
	Metadata           *TrackMetadata
	NormalizedMetadata *NormalizedTrackMetadata
	Artwork            []EmbeddedArtwork
	ScanError          string
}

type LibraryTrack struct {
	Id int64
	ScannedFile
}
