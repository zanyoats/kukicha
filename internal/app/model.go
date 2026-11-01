package app

import (
	"path/filepath"
	"strings"
	"time"
)

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
	// LibraryGeneratedAt stores the UTC timestamp committed with the rebuilt
	// library. Its presence lets Home display library data while artwork lookup
	// is still running; it does not indicate that the sync job has finished.
	LibraryGeneratedAt AppMetadataKey = iota

	// LibrarySupportedExtensionsJson records the audio, playlist, and sidecar
	// extension maps used for the build, committed with the rebuilt library.
	LibrarySupportedExtensionsJson

	// LibraryGeneratedJobId records the sync job whose work, including artwork
	// lookup, completed. If interrupted before the processor marks it succeeded,
	// the recovered job skips repeating that work when its ID matches this marker.
	// It is also used to get the latest successful sync logs for the roots page.
	LibraryGeneratedJobId

	// InstanceID is a persistent random identifier created at initialization
	// and used to generate this instance's identicon.
	InstanceID

	// InstanceIdenticon stores the generated SVG served as the instance avatar
	// and favicon, preserving its appearance across restarts.
	InstanceIdenticon

	// TaxonomyChecksum identifies the seeded genre/style catalog so initialization
	// can skip rebuilding taxonomy tables when the catalog has not changed.
	TaxonomyChecksum

	// RadioMembershipVersion changes atomically with radio membership rebuilds.
	RadioMembershipVersion
)

// Represents a root abstractly.
// At the moment there are only 2 types: local, remote.
type Root interface {
	// isRoot seals roots to pointer types in this package.
	isRoot()
	ConfigID() string
	Location() string
}

// Path is any folder path local to where this server is running.
type LocalRoot struct {
	ID   string `toml:"id" json:"id"`
	Path string `toml:"path" json:"path"`
}

// RemoteRoot describes an S3-compatible source.
// Credentials come from the AWS credential chain, and never from application config.
type RemoteRoot struct {
	ID              string `toml:"id" json:"id"`
	EndpointURL     string `toml:"endpoint_url" json:"endpoint_url"`
	Bucket          string `toml:"bucket" json:"bucket"`
	Prefix          string `toml:"prefix" json:"prefix"`
	Profile         string `toml:"profile" json:"profile"`
	Region          string `toml:"region" json:"region"`
	AddressingStyle string `toml:"addressing_style" json:"addressing_style"`
}

func (*LocalRoot) isRoot()  {}
func (*RemoteRoot) isRoot() {}

// ConfigID identifies a logical root independently of its storage location.
func (r *LocalRoot) ConfigID() string  { return r.ID }
func (r *RemoteRoot) ConfigID() string { return r.ID }
func (r *LocalRoot) Location() string  { return r.Path }
func (r *RemoteRoot) Location() string { return "s3://" + r.Bucket + "/" + r.Prefix }

// FileIdentity is the source location, independent of any temporary download.
type FileIdentity struct {
	Root Root
	Path string
	Ext  string
	Stem string
}

// Remote directories are exact key prefixes; local directories follow
// filesystem path rules.
func (f FileIdentity) Directory() string {
	if _, remote := f.Root.(*RemoteRoot); remote {
		if i := strings.LastIndexByte(f.Path, '/'); i >= 0 {
			return f.Path[:i+1]
		}
		return ""
	}
	dir := filepath.Dir(f.Path)
	if dir == "." {
		return ""
	}
	return dir + string(filepath.Separator)
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
	ReleaseCountry      string   `json:"release_country"`
	Label               string   `json:"label"`
	Copyright           string   `json:"copyright"`
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
	Genres                    []string `json:"genres"`
}

type EmbeddedArtwork struct {
	PictureType        string
	ArtworkContentType string
	ArtworkContentSize int
	ArtworkBytes       []byte
}

type ScannedFile struct {
	// RootPos is the scan-local root index used for joins and metadata tie-breaking.
	RootPos int
	FileIdentity
	FileSnapshot
	DurationSeconds    int64
	Metadata           *TrackMetadata
	NormalizedMetadata *NormalizedTrackMetadata
	Artwork            []EmbeddedArtwork
}

type LibraryTrack struct {
	Id int64
	ScannedFile
}

// AddJobPayload contains all request context needed by the add executor.
// TODO: Credentials
type AddJobPayload struct {
	Mode         string     `json:"mode"`
	TempPath     string     `json:"temp_path"`
	RootID       string     `json:"root_id"`
	RootLocation string     `json:"root_location"`
	Source       *AddSource `json:"source,omitempty"`
}

// AddSource identifies exactly one input for sync mode.
type AddSource struct {
	Folder string      `json:"folder,omitempty"`
	Remote *RemoteRoot `json:"remote,omitempty"`
}
