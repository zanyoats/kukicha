from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


class LibraryQueryError(Exception):
    """Base error for library query lookups."""


class AlbumNotFoundError(LibraryQueryError, KeyError):
    def __init__(self, album_id: str) -> None:
        super().__init__(f"album not found: {album_id}")
        self.album_id = album_id


class ArtistNotFoundError(LibraryQueryError, KeyError):
    def __init__(self, artist: str) -> None:
        super().__init__(f"artist not found: {artist}")
        self.artist = artist


class PlaylistNotFoundError(LibraryQueryError, KeyError):
    def __init__(self, playlist_id: int) -> None:
        super().__init__(f"playlist not found: {playlist_id}")
        self.playlist_id = playlist_id


class PlaylistItemNotFoundError(LibraryQueryError, KeyError):
    def __init__(self, playlist_item_id: int) -> None:
        super().__init__(f"playlist item not found: {playlist_item_id}")
        self.playlist_item_id = playlist_item_id


class TrackNotFoundError(LibraryQueryError, KeyError):
    def __init__(self, track_id: int) -> None:
        super().__init__(f"track not found: {track_id}")
        self.track_id = track_id


ALBUM_LIST_SORT_RECENTLY_ADDED = "recently_added"
ALBUM_LIST_SORT_RECENT = "recent"
ALBUM_LIST_SORT_FREQUENT = "frequent"
ALBUM_LIST_SORT_ARTIST = "artist"
ALBUM_LIST_SORT_ALBUMS = "albums"
ALBUM_LIST_SORT_GENRE = "genre"
ALBUM_LIST_SORT_STARRED = "starred"
DEFAULT_ALBUM_LIST_SORT = ALBUM_LIST_SORT_ARTIST
ALBUM_LIST_SORT_VALUES = frozenset(
    (
        ALBUM_LIST_SORT_RECENTLY_ADDED,
        ALBUM_LIST_SORT_RECENT,
        ALBUM_LIST_SORT_FREQUENT,
        ALBUM_LIST_SORT_ARTIST,
        ALBUM_LIST_SORT_ALBUMS,
        ALBUM_LIST_SORT_GENRE,
        ALBUM_LIST_SORT_STARRED,
    )
)


@dataclass(frozen=True, slots=True)
class GenreStyleFilter:
    genre: str
    styles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "genre", self.genre.strip())
        object.__setattr__(self, "styles", normalized_unique_tuple(self.styles))


@dataclass(frozen=True, slots=True)
class AlbumListQuery:
    artists: tuple[str, ...] = ()
    album: str | None = None
    root_positions: tuple[int, ...] = ()
    genres: tuple[str, ...] = ()
    styles: tuple[str, ...] = ()
    genre_filters: tuple[GenreStyleFilter, ...] = ()
    is_playlist: bool | None = None
    size: int = 200
    offset: int = 0
    search: str | None = None
    sort: str = DEFAULT_ALBUM_LIST_SORT

    def __post_init__(self) -> None:
        object.__setattr__(self, "search", normalized_search(self.search))
        object.__setattr__(self, "artists", normalized_tuple(self.artists))
        object.__setattr__(self, "root_positions", normalized_int_tuple(self.root_positions))
        object.__setattr__(self, "genres", normalized_tuple(self.genres))
        object.__setattr__(self, "styles", normalized_tuple(self.styles))
        object.__setattr__(
            self,
            "genre_filters",
            normalized_genre_style_filters(self.genre_filters),
        )
        object.__setattr__(self, "size", min(200, max(1, int(self.size))))
        object.__setattr__(self, "offset", max(0, int(self.offset)))
        object.__setattr__(self, "sort", normalized_album_list_sort(self.sort))


@dataclass(frozen=True, slots=True)
class PlaylistTrack:
    path: str
    track_id: int | None = None
    album_id: str | None = None
    root_position: int | None = None
    file_size_bytes: int | None = None
    file_type: str | None = None
    scan_error: str | None = None
    artist: str | None = None
    album_artist: str | None = None
    album_artists: tuple[str, ...] = ()
    composer: str | None = None
    album: str | None = None
    title: str | None = None
    work: str | None = None
    grouping: str | None = None
    movement_name: str | None = None
    track_number: str | None = None
    disc_number: str | None = None
    date: str | None = None
    genres: tuple[str, ...] = ()
    styles: tuple[str, ...] = ()
    has_cover: bool = False
    art_track_id: int | None = None
    starred_at: str | None = None
    is_compilation: bool = False
    duration_seconds: float | None = None
    bitrate: int | None = None
    has_playlist_membership: bool = False

    @property
    def is_work(self) -> bool:
        return bool(self.work or self.grouping)


@dataclass(frozen=True, slots=True)
class AlbumSummary:
    album_id: str
    artist: str
    album: str
    year: int | None
    track_count: int
    album_artists: tuple[str, ...] = ()
    file_created_at: str | None = None
    added_at: str | None = None
    starred_at: str | None = None
    art_track_id: int | None = None
    is_playlist: bool = False
    playlist_id: int | None = None
    cover_svg: str = ""
    cover_mime_type: str = ""
    playlist_kind: str = "local"
    playlist_source: str = "manual"
    sort_genre: str | None = None


@dataclass(frozen=True, slots=True)
class AlbumDetails(AlbumSummary):
    genres: tuple[str, ...] = ()
    styles: tuple[str, ...] = ()
    has_cover: bool = False
    is_compilation: bool = False
    is_work: bool = False
    track_ids: tuple[int, ...] = ()
    paths: tuple[str, ...] = ()
    tracks: tuple[PlaylistTrack, ...] = ()


@dataclass(frozen=True, slots=True)
class LibraryArtistSummary:
    artist: str
    album_count: int
    cover_album_id: str | None = None


@dataclass(frozen=True, slots=True)
class LibraryArtistAlbum(AlbumSummary):
    duration_seconds: int = 0
    genre: str | None = None
    has_cover: bool = False


@dataclass(frozen=True, slots=True)
class LibraryArtistDetails(LibraryArtistSummary):
    albums: tuple[LibraryArtistAlbum, ...] = ()


@dataclass(frozen=True, slots=True)
class PlaylistItem:
    playlist_item_id: int
    playlist_id: int
    position: int
    path: str
    playlist_name: str = ""
    track_id: int | None = None
    track: PlaylistTrack | None = None
    title: str | None = None
    duration_seconds: float | None = None
    duration_is_indeterminate: bool = False
    genre: str | None = None
    cover_url: str | None = None
    playlist_cover_svg: str = ""
    playlist_cover_mime_type: str = ""

    @property
    def playback_id(self) -> int:
        return -self.playlist_item_id

    @property
    def is_external(self) -> bool:
        return self.track_id is None


@dataclass(frozen=True, slots=True)
class PlaylistDetails:
    playlist_id: int
    name: str
    root_position: int | None = None
    cover_svg: str = ""
    cover_mime_type: str = ""
    kind: str = "local"
    source: str = "manual"
    created_at: str = ""
    updated_at: str = ""
    items: tuple[PlaylistItem, ...] = ()

    @property
    def track_count(self) -> int:
        return len(self.items)


@dataclass(frozen=True, slots=True)
class AlbumPage:
    items: tuple[AlbumSummary, ...]
    size: int
    offset: int
    has_next: bool = False
    has_previous: bool = False


@dataclass(frozen=True, slots=True)
class LibrarySearchQuery:
    query: str = ""
    artist_count: int = 20
    artist_offset: int = 0
    album_count: int = 20
    album_offset: int = 0
    song_count: int = 20
    song_offset: int = 0
    music_folder_id: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "query", str(self.query or "").strip())
        object.__setattr__(self, "artist_count", normalized_count(self.artist_count))
        object.__setattr__(self, "artist_offset", max(0, int(self.artist_offset)))
        object.__setattr__(self, "album_count", normalized_count(self.album_count))
        object.__setattr__(self, "album_offset", max(0, int(self.album_offset)))
        object.__setattr__(self, "song_count", normalized_count(self.song_count))
        object.__setattr__(self, "song_offset", max(0, int(self.song_offset)))
        if self.music_folder_id is not None:
            object.__setattr__(self, "music_folder_id", max(0, int(self.music_folder_id)))


@dataclass(frozen=True, slots=True)
class SearchArtistPage:
    items: tuple[LibraryArtistSummary, ...]
    count: int
    offset: int
    has_next: bool = False
    has_previous: bool = False


@dataclass(frozen=True, slots=True)
class SearchAlbumPage:
    items: tuple[AlbumSummary, ...]
    count: int
    offset: int
    has_next: bool = False
    has_previous: bool = False


@dataclass(frozen=True, slots=True)
class SearchSongPage:
    items: tuple[PlaylistTrack, ...]
    count: int
    offset: int
    has_next: bool = False
    has_previous: bool = False


@dataclass(frozen=True, slots=True)
class LibrarySearchResults:
    query: LibrarySearchQuery
    artists: SearchArtistPage
    albums: SearchAlbumPage
    songs: SearchSongPage


@dataclass(frozen=True, slots=True)
class GenreFilterGroup:
    genre: str
    styles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LibraryRootFilterOption:
    position: int
    path: str
    label: str
    kind: str = "local"
    source_json: str = "{}"


@dataclass(frozen=True, slots=True)
class LibraryGenre:
    value: str
    song_count: int
    album_count: int


@dataclass(frozen=True, slots=True)
class AlbumArtistSplitMapping:
    album_artist: str
    mapped_artists: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AlbumMetadataOverride:
    album_id: str
    album: str
    artist: str
    year: int | None
    provider: str
    entity_type: str
    entity_id: str
    related_entity_type: str | None = None
    related_entity_id: str | None = None
    is_current_album: bool = False

    @property
    def release_mbid(self) -> str | None:
        if self.provider != "musicbrainz" or self.entity_type != "release":
            return None
        return self.entity_id

    @property
    def release_group_mbid(self) -> str | None:
        if self.provider != "musicbrainz":
            return None
        if self.entity_type == "release-group":
            return self.entity_id
        if self.related_entity_type == "release-group":
            return self.related_entity_id
        return None


AlbumMusicBrainzOverride = AlbumMetadataOverride


@dataclass(frozen=True, slots=True)
class CacheStat:
    key: str
    section: str
    label: str
    count: int


@dataclass(frozen=True, slots=True)
class LibraryRootAlbumArtistStats:
    root_position: int
    album_artist: str
    tracks_scanned: int
    albums_scanned: int


@dataclass(frozen=True, slots=True)
class LibraryAlbumArtistStats:
    album_artist: str
    tracks_scanned: int
    albums_scanned: int


@dataclass(frozen=True, slots=True)
class LibraryStats:
    tracks_scanned: int
    albums_scanned: int
    album_artists: tuple[LibraryAlbumArtistStats, ...] = ()


@dataclass(frozen=True, slots=True)
class LibraryRootStats:
    root_position: int
    tracks_scanned: int
    albums_scanned: int
    album_artists: tuple[LibraryRootAlbumArtistStats, ...] = ()


@dataclass(frozen=True, slots=True)
class LibraryFilterOptions:
    roots: tuple[LibraryRootFilterOption, ...] = ()
    artists: tuple[str, ...] = ()
    genre_groups: tuple[GenreFilterGroup, ...] = ()
    loose_styles: tuple[str, ...] = ()


def normalized_tuple(values: Iterable[str | None]) -> tuple[str, ...]:
    return tuple(value.strip() for value in values if value and value.strip())


def normalized_unique_tuple(values: Iterable[str | None]) -> tuple[str, ...]:
    normalized: dict[str, str] = {}
    for value in values:
        if not value:
            continue
        text = value.strip()
        if text:
            normalized.setdefault(normalize_match(text), text)
    return tuple(normalized.values())


def normalized_genre_style_filters(
    filters: Iterable[GenreStyleFilter],
) -> tuple[GenreStyleFilter, ...]:
    grouped: dict[str, GenreStyleFilter] = {}
    for item in filters:
        genre = item.genre.strip()
        if not genre:
            continue
        styles = normalized_unique_tuple(item.styles)
        key = normalize_match(genre)
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = GenreStyleFilter(
                genre=genre,
                styles=styles,
            )
            continue
        merged_styles = (
            ()
            if not existing.styles or not styles
            else (*existing.styles, *styles)
        )
        grouped[key] = GenreStyleFilter(
            genre=existing.genre,
            styles=merged_styles,
        )
    return tuple(
        GenreStyleFilter(
            genre=item.genre,
            styles=normalized_unique_tuple(item.styles),
        )
        for item in grouped.values()
    )


def normalized_search(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def normalized_count(value: object) -> int:
    return min(500, max(0, int(value)))


def normalized_int_tuple(values: Iterable[object]) -> tuple[int, ...]:
    normalized: dict[int, int] = {}
    for value in values:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed < 0:
            continue
        normalized.setdefault(parsed, parsed)
    return tuple(normalized)


def normalized_album_list_sort(value: str | None) -> str:
    if value in ALBUM_LIST_SORT_VALUES:
        return value
    return DEFAULT_ALBUM_LIST_SORT


def normalize_match(value: str) -> str:
    return " ".join(value.casefold().strip().split())
