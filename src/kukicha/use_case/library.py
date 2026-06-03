from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterable
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from ..library_sources import LibraryRootSource, SOURCE_KIND_LOCAL, local_root_source
from .coverartarchive import (
    CoverArtArchiveClient,
    CoverArtArchiveStats,
    front_image_url,
    get_cover_art_archive_entity,
    get_cover_art_archive_image,
)
from .database import (
    UNKNOWN_GENRE_TAG,
    canonicalize_library_album_artists,
    clear_library,
    connect_database,
    connect_existing_database,
    get_metadata,
    library_root_position_for_path,
    rebuild_album_rollups,
    rebuild_album_search_index,
    rebuild_library_search_index,
    rebuild_root_scan_stats,
    set_metadata,
    utc_now_iso,
)
from ..discogs import (
    LocalAlbum,
    file_album_id_from_album_id,
    group_library_albums,
    local_album_id,
    normalize_release_variant,
    track_raw_album_artist_id_text,
)
from ..album_artists import (
    DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
    album_artist_id_text,
    album_artist_has_mapping_pattern,
    default_album_artist_mapping,
    mapped_album_artist_text,
    mapped_album_artists_from_text,
    normalize_album_artist_split_patterns,
    track_album_artist_source,
    track_album_artist_values,
)
from .itunes import (
    ItunesLookupCandidate,
    ItunesLookupClient,
    ItunesLookupStats,
    get_itunes_lookup_image,
)
from .discogs import (
    DISCOGS_USER_AGENT,
    DiscogsClient,
    DiscogsLookupStats,
    discogs_master_id,
    discogs_primary_image_url,
    discogs_release_fingerprint,
    get_discogs_entity,
)
from .metadata import (
    AlbumMetadataLink,
    AlbumMetadataTrackLink,
    METADATA_ENTITY_MASTER,
    METADATA_ENTITY_RELEASE,
    METADATA_PROVIDER_DISCOGS,
    load_album_metadata_links,
    load_album_metadata_track_links,
    store_album_metadata_track_link,
)
from ..models import (
    ALBUM_ARTWORK_HEIGHT,
    TRACK_ARTWORK_HEIGHT,
    MusicLibrary,
    PlaylistRecord,
    TrackArtwork,
    TrackRecord,
    TrackSourceRecord,
    normalize_genre_values,
)
from .musicbrainz import (
    MusicBrainzAlbumLink,
    MusicBrainzClient,
    MusicBrainzLookupStats,
    album_musicbrainz_link_for_album_id,
    clean_mbid,
    get_musicbrainz_entity,
    load_album_musicbrainz_track_links,
    load_album_musicbrainz_links,
    musicbrainz_genres,
    musicbrainz_release_fingerprint,
    musicbrainz_release_group_mbid,
    normalize_musicbrainz_mbid,
    store_album_musicbrainz_link,
    store_album_musicbrainz_track_link,
    store_album_musicbrainz_release_group_if_missing,
)
from ..playlist_art import playlist_cover_svg
from ..scanner import is_url_resource, thumbnail_artworks
from ..text import normalize_slug_text, normalize_text

FUZZY_TOKEN_EXPANSIONS = {
    "alt": "alternative",
    "electro": "electronic",
    "electronica": "electronic",
    "prog": "progressive",
    "psych": "psychedelic",
}
DIRECT_TERM_ALIASES = {
    "alternative": "Alternative Rock",
    "dance": "Electronic",
    "dance and dj": "Electronic",
}
FUZZY_MATCH_THRESHOLD = 0.84
FUZZY_MATCH_MARGIN = 0.05
ALBUM_ARTIST_MAPPING_FINGERPRINT_METADATA_KEY = "album_artist_mapping_fingerprint"


@dataclass(slots=True)
class GenreResolutionStats:
    exact_genre_matches: int = 0
    exact_style_matches: int = 0
    fuzzy_genre_matches: int = 0
    fuzzy_style_matches: int = 0
    unmatched: int = 0
    unknown_albums: int = 0
    unknown_tracks: int = 0
    musicbrainz_api_calls: int = 0
    musicbrainz_cached_calls: int = 0
    musicbrainz_rate_limit_retries: int = 0
    musicbrainz_fetch_failures: int = 0
    musicbrainz_album_overrides: int = 0
    musicbrainz_unmatched_genres: int = 0


@dataclass(slots=True)
class CoverArtResolutionStats:
    itunes_lookup_api_calls: int = 0
    itunes_lookup_cached_calls: int = 0
    metadata_api_calls: int = 0
    metadata_cached_calls: int = 0
    image_downloads: int = 0
    image_cached_calls: int = 0
    fetch_failures: int = 0
    missing_art: int = 0
    album_cover_overrides: int = 0
    tracks_updated: int = 0


@dataclass(slots=True)
class ResolvedGenreMatch:
    genres: list[str]
    styles: list[str]
    resolution: str


@dataclass(slots=True)
class ResolvedAlbumGenres:
    genres: list[str]
    styles: list[str]


@dataclass(slots=True)
class TaxonomyTermCandidate:
    name: str
    kind: str
    normalized: str
    expanded: str
    compact: str
    tokens: frozenset[str]


@dataclass(slots=True)
class AlbumPersistenceState:
    starred_at_by_album_id: dict[str, str]
    added_at_by_album_id: dict[str, str]
    migrated_starred_album_ids: set[str]


@dataclass(slots=True)
class TaxonomyGenreMatcher:
    exact_genres: dict[str, str]
    exact_styles: dict[str, str]
    style_parents: dict[str, str]
    candidates: list[TaxonomyTermCandidate]

    def resolve(self, value: str) -> ResolvedGenreMatch:
        alias = DIRECT_TERM_ALIASES.get(normalize_text(value))
        if alias:
            if alias in self.exact_genres.values():
                return ResolvedGenreMatch(
                    genres=[alias],
                    styles=[],
                    resolution="fuzzy_genre",
                )
            if alias in self.exact_styles.values():
                return self.expand_style_match(alias, resolution="fuzzy_style")
            return ResolvedGenreMatch(
                genres=[alias],
                styles=[],
                resolution="fuzzy_genre",
            )
        keys = exact_lookup_keys(value)
        for key in keys:
            match = self.exact_genres.get(key)
            if match:
                return ResolvedGenreMatch(
                    genres=[match],
                    styles=[],
                    resolution="exact_genre",
                )
        for key in keys:
            match = self.exact_styles.get(key)
            if match:
                return self.expand_style_match(match, resolution="exact_style")

        query = build_term_candidate(value, kind="input")
        if not query.normalized:
            return ResolvedGenreMatch(genres=[], styles=[], resolution="unmatched")

        ranked = sorted(
            (
                (fuzzy_term_score(query, candidate), candidate)
                for candidate in self.candidates
            ),
            key=lambda item: (
                -item[0],
                0 if item[1].kind == "genre" else 1,
                item[1].name.casefold(),
            ),
        )
        if not ranked:
            return ResolvedGenreMatch(genres=[], styles=[], resolution="unmatched")

        best_score, best_candidate = ranked[0]
        if best_score < FUZZY_MATCH_THRESHOLD:
            return ResolvedGenreMatch(genres=[], styles=[], resolution="unmatched")

        if len(ranked) > 1:
            second_score = ranked[1][0]
            if best_score < 0.97 and best_score - second_score < FUZZY_MATCH_MARGIN:
                return ResolvedGenreMatch(genres=[], styles=[], resolution="unmatched")

        if best_candidate.kind == "style":
            return self.expand_style_match(best_candidate.name, resolution="fuzzy_style")
        return ResolvedGenreMatch(
            genres=[best_candidate.name],
            styles=[],
            resolution=f"fuzzy_{best_candidate.kind}",
        )

    def expand_style_match(self, style: str, *, resolution: str) -> ResolvedGenreMatch:
        parent = self.style_parents.get(style) or UNKNOWN_GENRE_TAG
        return ResolvedGenreMatch(
            genres=[parent],
            styles=[style],
            resolution=resolution,
        )


class AlbumArtistMappingResolver:
    def __init__(
        self,
        connection: sqlite3.Connection,
        split_patterns: Iterable[str | None] = DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
    ) -> None:
        self.connection = connection
        self.split_patterns = normalize_album_artist_split_patterns(split_patterns)
        self.cache: dict[str, tuple[str, ...]] = {}

    def resolve(self, value: str | None) -> tuple[str, ...]:
        album_artist = (value or "").strip()
        if not album_artist:
            return ()

        key = album_artist.casefold()
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        row = self.connection.execute(
            """
            SELECT mapped_artists
            FROM album_artist_split_mappings
            WHERE album_artist = ?
            """,
            (album_artist,),
        ).fetchone()
        if row is not None:
            artists = mapped_album_artists_from_text(row["mapped_artists"]) or (
                album_artist,
            )
        elif album_artist_has_mapping_pattern(album_artist, self.split_patterns):
            artists = default_album_artist_mapping(album_artist) or (album_artist,)
            self.connection.execute(
                """
                INSERT OR IGNORE INTO album_artist_split_mappings (
                    album_artist,
                    mapped_artists
                ) VALUES (?, ?)
                """,
                (album_artist, mapped_album_artist_text(artists)),
            )
        else:
            artists = (album_artist,)

        self.cache[key] = artists
        return artists


def apply_album_artist_mappings(
    connection: sqlite3.Connection,
    tracks: Iterable[TrackRecord],
    split_patterns: Iterable[str | None] = DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
) -> None:
    resolver = AlbumArtistMappingResolver(connection, split_patterns)
    for track in tracks:
        track.album_artists = resolver.resolve(track_album_artist_source(track))


def album_ids_by_lookup_key(albums: Iterable[LocalAlbum]) -> dict[tuple[str, str, str], str]:
    return {
        album_lookup_key(
            album.artist_id_text,
            album.album,
            album.release_variant,
        ): album.album_id
        for album in albums
    }


def album_artist_mapping_fingerprint(
    connection: sqlite3.Connection,
    split_patterns: Iterable[str | None] = DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
) -> str:
    rows = [
        (
            str(row["album_artist"]),
            mapped_album_artist_text(
                mapped_album_artists_from_text(row["mapped_artists"])
            ),
        )
        for row in connection.execute(
            """
            SELECT album_artist, mapped_artists
            FROM album_artist_split_mappings
            ORDER BY album_artist COLLATE NOCASE
            """
        )
    ]
    payload = {
        "patterns": normalize_album_artist_split_patterns(split_patterns),
        "mappings": rows,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def set_album_artist_mapping_fingerprint_metadata(
    connection: sqlite3.Connection,
    split_patterns: Iterable[str | None] = DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
) -> None:
    set_metadata(
        connection,
        ALBUM_ARTIST_MAPPING_FINGERPRINT_METADATA_KEY,
        album_artist_mapping_fingerprint(connection, split_patterns),
    )


def album_artist_mapping_fingerprint_changed(
    connection: sqlite3.Connection,
    split_patterns: Iterable[str | None] = DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
) -> bool:
    return get_metadata(connection, ALBUM_ARTIST_MAPPING_FINGERPRINT_METADATA_KEY) != (
        album_artist_mapping_fingerprint(connection, split_patterns)
    )


def save_library(
    library: MusicLibrary,
    destination: Path,
    *,
    album_artist_split_patterns: Iterable[str | None] = DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
) -> None:
    save_library_with_options(
        library,
        destination,
        album_artist_split_patterns=album_artist_split_patterns,
    )


def normalized_library_root_rows(
    root_rows: Iterable[tuple[int, str] | tuple[int, str, str, str] | LibraryRootSource]
    | None,
    library: MusicLibrary,
) -> list[LibraryRootSource]:
    if root_rows is None:
        return [
            local_root_source(position, root_path)
            for position, root_path in enumerate(library.roots)
        ]

    normalized: list[LibraryRootSource] = []
    for row in root_rows:
        if isinstance(row, LibraryRootSource):
            normalized.append(row)
            continue
        if len(row) == 2:
            position, root_path = row
            normalized.append(local_root_source(int(position), str(root_path)))
            continue
        position, root_path, kind, source_json = row
        normalized.append(
            LibraryRootSource(
                position=int(position),
                path=str(root_path),
                kind=str(kind or SOURCE_KIND_LOCAL),
                source_json=str(source_json or "{}"),
            )
        )
    return normalized


def save_library_with_options(
    library: MusicLibrary,
    destination: Path,
    *,
    connection: sqlite3.Connection | None = None,
    root_rows: Iterable[tuple[int, str] | tuple[int, str, str, str] | LibraryRootSource]
    | None = None,
    album_artist_split_patterns: Iterable[str | None] = DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
) -> None:
    library_roots = normalized_library_root_rows(root_rows, library)
    valid_root_positions = {root.position for root in library_roots}
    root_positions_by_index = [root.position for root in library_roots]
    owns_connection = connection is None
    if connection is None:
        connection = connect_database(destination)
    try:
        apply_album_artist_mappings(
            connection,
            library.tracks,
            split_patterns=album_artist_split_patterns,
        )
        copy_track_musicbrainz_links_from_existing_album_ids(connection)
        apply_musicbrainz_release_variants(connection, library.tracks)
        albums = group_library_albums(library)
        album_ids_by_key = album_ids_by_lookup_key(albums)
        path_legacy_album_ids = legacy_album_ids_by_current_album_id_from_track_paths(
            connection,
            library.tracks,
            album_ids_by_key,
        )
        copy_album_musicbrainz_links_from_legacy_album_ids(
            connection,
            albums,
            legacy_album_ids_by_album_id=path_legacy_album_ids,
        )
        store_scanned_album_musicbrainz_links(connection, albums)
        persistence_state = album_persistence_state(
            connection,
            albums,
            legacy_album_ids_by_album_id=path_legacy_album_ids,
        )
        new_album_added_at = utc_now_iso()
        existing_track_ids_by_path = {
            str(row["path"]): int(row["track_id"])
            for row in connection.execute(
                "SELECT path, track_id FROM library_tracks"
            )
        }
        track_ids_by_path: dict[str, int] = {}
        for track in library.tracks:
            if track.track_id is None:
                track.track_id = existing_track_ids_by_path.get(track.path)

        clear_library(connection)

        for root in library_roots:
            connection.execute(
                """
                INSERT INTO library_roots (
                    position,
                    root_path,
                    kind,
                    source_json
                ) VALUES (?, ?, ?, ?)
                """,
                (root.position, root.path, root.kind, root.source_json),
            )

        for album in albums:
            connection.execute(
                """
                INSERT INTO library_albums (
                    album_id,
                    album,
                    year,
                    track_count,
                    file_created_at,
                    added_at,
                    starred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    album.album_id,
                    album.album,
                    album.year,
                    album.track_count,
                    album.file_created_at or "",
                    persistence_state.added_at_by_album_id.get(
                        album.album_id,
                        new_album_added_at,
                    ),
                    persistence_state.starred_at_by_album_id.get(album.album_id),
                ),
            )
            for position, artist in enumerate(album.artists):
                connection.execute(
                    """
                    INSERT INTO library_album_artists (album_id, position, artist)
                    VALUES (?, ?, ?)
                    """,
                    (album.album_id, position, artist),
                )
        persist_album_user_state_migrations(
            connection,
            persistence_state,
            (album.album_id for album in albums),
        )
        canonicalize_library_album_artists(connection)

        track_ids_by_path: dict[str, int] = {}
        for track in library.tracks:
            album_id = track_album_id(track, album_ids_by_key)
            root_position = track.root_position
            if (
                root_rows is not None
                and root_position is not None
                and 0 <= root_position < len(root_positions_by_index)
            ):
                root_position = root_positions_by_index[root_position]
            elif root_position not in valid_root_positions:
                root_position = library_root_position_for_path(
                    track.path,
                    [(root.position, root.path, root.kind) for root in library_roots],
                )
            params = (
                album_id,
                root_position,
                track.path,
                track.file_created_at,
                track.file_modified_at_ns,
                track.file_size_bytes,
                track.sidecar_artwork_path,
                track.sidecar_artwork_modified_at_ns,
                track.sidecar_artwork_size_bytes,
                track.file_type,
                track.scan_error,
                track.artist,
                track.album_artist,
                track.composer,
                track.album,
                track.title,
                track.work,
                track.grouping,
                track.movement_name,
                1 if track.is_compilation else 0,
                track.track_number,
                track.disc_number,
                track.date,
                track.duration_seconds,
                track.bitrate,
            )
            if track.track_id is None:
                cursor = connection.execute(
                    """
                    INSERT INTO library_tracks (
                        album_id,
                        root_position,
                        path,
                        file_created_at,
                        file_modified_at_ns,
                        file_size_bytes,
                        sidecar_artwork_path,
                        sidecar_artwork_modified_at_ns,
                        sidecar_artwork_size_bytes,
                        file_type,
                        scan_error,
                        artist,
                        album_artist,
                        composer,
                        album,
                        title,
                        work,
                        grouping,
                        movement_name,
                        is_compilation,
                        track_number,
                        disc_number,
                        date,
                        duration_seconds,
                        bitrate
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    params,
                )
                track_id = int(cursor.lastrowid)
                track.track_id = track_id
            else:
                connection.execute(
                    """
                    INSERT INTO library_tracks (
                        track_id,
                        album_id,
                        root_position,
                        path,
                        file_created_at,
                        file_modified_at_ns,
                        file_size_bytes,
                        sidecar_artwork_path,
                        sidecar_artwork_modified_at_ns,
                        sidecar_artwork_size_bytes,
                        file_type,
                        scan_error,
                        artist,
                        album_artist,
                        composer,
                        album,
                        title,
                        work,
                        grouping,
                        movement_name,
                        is_compilation,
                        track_number,
                        disc_number,
                        date,
                        duration_seconds,
                        bitrate
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (track.track_id, *params),
                )
            track_id = int(track.track_id)
            track_ids_by_path[track.path] = track_id
            replace_library_track_source(connection, track_id, track, root_position=root_position)
            for position, genre in enumerate(normalize_genre_values(track.genres)):
                connection.execute(
                    """
                    INSERT INTO library_track_genres (track_id, position, genre)
                    VALUES (?, ?, ?)
                    """,
                    (track_id, position, genre),
                )
            for position, style in enumerate(normalize_genre_values(track.styles)):
                connection.execute(
                    """
                    INSERT INTO library_track_styles (track_id, position, style)
                    VALUES (?, ?, ?)
                    """,
                    (track_id, position, style),
                )
            for height_px, artwork in (
                (TRACK_ARTWORK_HEIGHT, track.artwork),
                (ALBUM_ARTWORK_HEIGHT, track.album_artwork),
            ):
                if artwork is not None and artwork.data:
                    connection.execute(
                        """
                        INSERT INTO library_track_artwork (
                            track_id,
                            height_px,
                            mime_type,
                            data
                        )
                        VALUES (?, ?, ?, ?)
                        """,
                        (track_id, height_px, artwork.mime_type, artwork.data),
                    )

        if library.playlists:
            save_explicit_library_playlists(connection, library.playlists, track_ids_by_path)
        reconcile_playlist_item_track_ids(connection)

        set_metadata(connection, "library_generated_at", library.generated_at)
        set_metadata(
            connection,
            "library_supported_extensions_json",
            json.dumps(library.supported_extensions),
        )
        rebuild_album_rollups(connection)
        rebuild_root_scan_stats(connection)
        rebuild_album_search_index(connection)
        rebuild_library_search_index(connection)
        set_album_artist_mapping_fingerprint_metadata(
            connection,
            album_artist_split_patterns,
        )
        if owns_connection:
            connection.commit()
    finally:
        if owns_connection:
            connection.close()


def album_starred_at_by_album_id(connection: sqlite3.Connection) -> dict[str, str]:
    starred_at_by_album_id = {
        str(row["album_id"]): str(row["starred_at"])
        for row in connection.execute(
            """
            SELECT album_id, starred_at
            FROM library_albums
            WHERE starred_at IS NOT NULL
            """
        )
    }
    starred_at_by_album_id.update(
        {
            str(row["album_id"]): str(row["starred_at"])
            for row in connection.execute(
                """
                SELECT album_id, starred_at
                FROM album_user_state
                WHERE starred_at IS NOT NULL
                """
            )
        }
    )
    return starred_at_by_album_id


def album_added_at_by_album_id(connection: sqlite3.Connection) -> dict[str, str]:
    return {
        str(row["album_id"]): str(row["added_at"])
        for row in connection.execute(
            """
            SELECT album_id, added_at
            FROM library_albums
            WHERE added_at IS NOT NULL
                AND added_at != ''
            """
        )
    }


def album_persistence_state(
    connection: sqlite3.Connection,
    albums: Iterable[LocalAlbum],
    *,
    legacy_album_ids_by_album_id: dict[str, tuple[str, ...]] | None = None,
) -> AlbumPersistenceState:
    starred_at_by_album_id = album_starred_at_by_album_id(connection)
    added_at_by_album_id = album_added_at_by_album_id(connection)
    migrated_starred_album_ids: set[str] = set()
    legacy_album_ids_by_album_id = legacy_album_ids_by_album_id or {}

    for album in albums:
        for legacy_album_id in album_legacy_state_album_ids(
            album,
            extra_album_ids=legacy_album_ids_by_album_id.get(album.album_id, ()),
        ):
            if (
                album.album_id not in starred_at_by_album_id
                and legacy_album_id in starred_at_by_album_id
            ):
                starred_at_by_album_id[album.album_id] = starred_at_by_album_id[
                    legacy_album_id
                ]
                migrated_starred_album_ids.add(legacy_album_id)
            if (
                album.album_id not in added_at_by_album_id
                and legacy_album_id in added_at_by_album_id
            ):
                added_at_by_album_id[album.album_id] = added_at_by_album_id[
                    legacy_album_id
                ]

    return AlbumPersistenceState(
        starred_at_by_album_id=starred_at_by_album_id,
        added_at_by_album_id=added_at_by_album_id,
        migrated_starred_album_ids=migrated_starred_album_ids,
    )


def persist_album_user_state_migrations(
    connection: sqlite3.Connection,
    persistence_state: AlbumPersistenceState,
    album_ids: Iterable[str],
) -> None:
    current_album_ids = tuple(
        dict.fromkeys(album_id for album_id in album_ids if album_id)
    )
    if not current_album_ids and not persistence_state.migrated_starred_album_ids:
        return
    for legacy_album_id in sorted(persistence_state.migrated_starred_album_ids):
        connection.execute(
            "DELETE FROM album_user_state WHERE album_id = ?",
            (legacy_album_id,),
        )
    for album_id in current_album_ids:
        starred_at = persistence_state.starred_at_by_album_id.get(album_id)
        if not starred_at:
            continue
        connection.execute(
            """
            INSERT INTO album_user_state (album_id, starred_at)
            VALUES (?, ?)
            ON CONFLICT(album_id) DO UPDATE SET
                starred_at = excluded.starred_at
            """,
            (album_id, starred_at),
        )


def legacy_album_ids_by_current_album_id_from_track_paths(
    connection: sqlite3.Connection,
    tracks: Iterable[TrackRecord],
    album_ids_by_key: dict[tuple[str, str, str], str],
) -> dict[str, tuple[str, ...]]:
    rows_by_path = library_track_rows_by_path(connection)
    legacy_ids: dict[str, list[str]] = {}
    for track in tracks:
        current_album_id = track_album_id(track, album_ids_by_key)
        if not current_album_id:
            continue
        row = rows_by_path.get(track.path)
        if row is None:
            continue
        legacy_album_id = nullable_text(row["album_id"])
        if (
            not legacy_album_id
            or legacy_album_id == current_album_id
            or not track_raw_album_identity_matches_row(track, row)
        ):
            continue
        legacy_ids.setdefault(current_album_id, []).append(legacy_album_id)
    return {
        album_id: tuple(dict.fromkeys(values))
        for album_id, values in legacy_ids.items()
    }


def track_raw_album_identity_matches_row(
    track: TrackRecord,
    row: sqlite3.Row,
) -> bool:
    row_artist = str(row["album_artist"] or row["artist"] or "").strip()
    row_album = str(row["album"] or "").strip()
    track_album = str(track.album or "").strip()
    return (
        bool(row_artist and row_album and track_album)
        and normalize_text(row_artist) == normalize_text(track_album_artist_source(track))
        and normalize_text(row_album) == normalize_text(track_album)
    )


def save_rescanned_library_incremental(
    library: MusicLibrary,
    destination: Path,
    *,
    connection: sqlite3.Connection | None = None,
    root_rows: Iterable[tuple[int, str] | tuple[int, str, str, str] | LibraryRootSource],
    scanned_paths: Iterable[str],
    album_artist_split_patterns: Iterable[str | None] = DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
) -> None:
    library_roots = normalized_library_root_rows(root_rows, library)
    valid_root_positions = {root.position for root in library_roots}
    root_positions_by_index = [root.position for root in library_roots]
    scanned_path_set = set(scanned_paths)
    owns_connection = connection is None
    if connection is None:
        connection = connect_existing_database(destination)
    try:
        existing_tracks_by_path = library_track_rows_by_path(connection)
        current_paths = {track.path for track in library.tracks}
        stale_paths = sorted(set(existing_tracks_by_path) - current_paths)
        track_ids_by_path = {
            path: int(row["track_id"])
            for path, row in existing_tracks_by_path.items()
            if path not in stale_paths
        }
        mapping_fingerprint_changed = album_artist_mapping_fingerprint_changed(
            connection,
            album_artist_split_patterns,
        )
        if not scanned_path_set and not stale_paths and not mapping_fingerprint_changed:
            set_metadata(connection, "library_generated_at", library.generated_at)
            set_metadata(
                connection,
                "library_supported_extensions_json",
                json.dumps(library.supported_extensions),
            )
            rebuild_root_scan_stats(connection)
            if owns_connection:
                connection.commit()
            return

        apply_album_artist_mappings(
            connection,
            library.tracks,
            split_patterns=album_artist_split_patterns,
        )
        copy_track_musicbrainz_links_from_existing_album_ids(connection)
        apply_musicbrainz_release_variants(connection, library.tracks)
        albums = group_library_albums(library)
        album_ids_by_key = album_ids_by_lookup_key(albums)
        path_legacy_album_ids = legacy_album_ids_by_current_album_id_from_track_paths(
            connection,
            library.tracks,
            album_ids_by_key,
        )
        copy_album_musicbrainz_links_from_legacy_album_ids(
            connection,
            albums,
            legacy_album_ids_by_album_id=path_legacy_album_ids,
        )
        store_scanned_album_musicbrainz_links(connection, albums)
        persistence_state = album_persistence_state(
            connection,
            albums,
            legacy_album_ids_by_album_id=path_legacy_album_ids,
        )
        current_albums_by_id = {album.album_id: album for album in albums}

        affected_album_ids: set[str] = set()
        if mapping_fingerprint_changed:
            affected_album_ids.update(current_albums_by_id)
        for path in stale_paths:
            album_id = existing_tracks_by_path[path]["album_id"]
            if album_id:
                affected_album_ids.add(str(album_id))

        track_writes: list[tuple[TrackRecord, str | None, int | None]] = []
        for track in library.tracks:
            existing_row = existing_tracks_by_path.get(track.path)
            if existing_row is not None:
                track.track_id = int(existing_row["track_id"])
            album_id = track_album_id(track, album_ids_by_key)
            root_position = resolved_library_item_root_position(
                track.root_position,
                track.path,
                library_roots,
                valid_root_positions=valid_root_positions,
                root_positions_by_index=root_positions_by_index,
            )
            if (
                existing_row is None
                or track.path in scanned_path_set
                or nullable_text(existing_row["album_id"]) != album_id
                or nullable_int(existing_row["root_position"]) != root_position
                or not track_snapshot_matches_row(track, existing_row)
            ):
                if existing_row is not None and existing_row["album_id"]:
                    affected_album_ids.add(str(existing_row["album_id"]))
                if album_id:
                    affected_album_ids.add(album_id)
                track_writes.append((track, album_id, root_position))

        upsert_library_album_rows(
            connection,
            (
                current_albums_by_id[album_id]
                for album_id in affected_album_ids
                if album_id in current_albums_by_id
            ),
            persistence_state=persistence_state,
        )
        persist_album_user_state_migrations(
            connection,
            persistence_state,
            (
                album_id
                for album_id in affected_album_ids
                if album_id in current_albums_by_id
            ),
        )
        delete_library_tracks_by_path(connection, stale_paths)
        for track, album_id, root_position in track_writes:
            track_id = upsert_library_track_row(
                connection,
                track,
                album_id=album_id,
                root_position=root_position,
            )
            track_ids_by_path[track.path] = track_id
            replace_library_track_children(connection, track_id, track)
            replace_library_track_source(connection, track_id, track, root_position=root_position)

        delete_missing_library_album_rows(
            connection,
            (
                album_id
                for album_id in affected_album_ids
                if album_id not in current_albums_by_id
            ),
        )
        if affected_album_ids:
            rebuild_album_rollups(connection, affected_album_ids)
            rebuild_album_search_index(connection, affected_album_ids)
            rebuild_library_search_index(connection)

        reconcile_playlist_item_track_ids(connection)
        set_metadata(connection, "library_generated_at", library.generated_at)
        set_metadata(
            connection,
            "library_supported_extensions_json",
            json.dumps(library.supported_extensions),
        )
        rebuild_root_scan_stats(connection)
        set_album_artist_mapping_fingerprint_metadata(
            connection,
            album_artist_split_patterns,
        )
        if owns_connection:
            connection.commit()
    finally:
        if owns_connection:
            connection.close()


def library_track_rows_by_path(
    connection: sqlite3.Connection,
) -> dict[str, sqlite3.Row]:
    return {
        str(row["path"]): row
        for row in connection.execute(
            """
            SELECT
                track_id,
                album_id,
                root_position,
                path,
                artist,
                album_artist,
                album,
                file_modified_at_ns,
                file_size_bytes,
                sidecar_artwork_path,
                sidecar_artwork_modified_at_ns,
                sidecar_artwork_size_bytes
            FROM library_tracks
            """
        )
    }


def track_snapshot_matches_row(track: TrackRecord, row: sqlite3.Row) -> bool:
    return (
        nullable_int(row["file_modified_at_ns"]) == track.file_modified_at_ns
        and nullable_int(row["file_size_bytes"]) == track.file_size_bytes
        and nullable_text(row["sidecar_artwork_path"]) == track.sidecar_artwork_path
        and nullable_int(row["sidecar_artwork_modified_at_ns"])
        == track.sidecar_artwork_modified_at_ns
        and nullable_int(row["sidecar_artwork_size_bytes"])
        == track.sidecar_artwork_size_bytes
    )


def nullable_int(value: object) -> int | None:
    return int(value) if value is not None else None


def nullable_text(value: object) -> str | None:
    return str(value) if value is not None else None


def resolved_library_item_root_position(
    root_position: int | None,
    path: str,
    library_roots: list[LibraryRootSource],
    *,
    valid_root_positions: set[int],
    root_positions_by_index: list[int],
) -> int | None:
    if (
        root_position is not None
        and 0 <= root_position < len(root_positions_by_index)
    ):
        return root_positions_by_index[root_position]
    if root_position in valid_root_positions:
        return root_position
    return library_root_position_for_path(
        path,
        [(root.position, root.path, root.kind) for root in library_roots],
    )


def upsert_library_album_rows(
    connection: sqlite3.Connection,
    albums: Iterable[LocalAlbum],
    *,
    persistence_state: AlbumPersistenceState | None = None,
) -> None:
    if persistence_state is None:
        added_at_by_album_id = album_added_at_by_album_id(connection)
        starred_at_by_album_id = album_starred_at_by_album_id(connection)
    else:
        added_at_by_album_id = persistence_state.added_at_by_album_id
        starred_at_by_album_id = persistence_state.starred_at_by_album_id
    new_album_added_at = utc_now_iso()
    for album in albums:
        connection.execute(
            """
            INSERT INTO library_albums (
                album_id,
                album,
                year,
                track_count,
                file_created_at,
                added_at,
                starred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(album_id) DO UPDATE SET
                album = excluded.album,
                year = excluded.year,
                track_count = excluded.track_count,
                file_created_at = excluded.file_created_at,
                added_at = COALESCE(NULLIF(library_albums.added_at, ''), excluded.added_at),
                starred_at = COALESCE(library_albums.starred_at, excluded.starred_at)
            """,
            (
                album.album_id,
                album.album,
                album.year,
                album.track_count,
                album.file_created_at or "",
                added_at_by_album_id.get(album.album_id, new_album_added_at),
                starred_at_by_album_id.get(album.album_id),
            ),
        )
        connection.execute(
            "DELETE FROM library_album_artists WHERE album_id = ?",
            (album.album_id,),
        )
        for position, artist in enumerate(album.artists):
            connection.execute(
                """
                INSERT INTO library_album_artists (album_id, position, artist)
                VALUES (?, ?, ?)
                """,
                (album.album_id, position, artist),
            )


def delete_missing_library_album_rows(
    connection: sqlite3.Connection,
    album_ids: Iterable[str],
) -> None:
    scoped_album_ids = list(dict.fromkeys(album_ids))
    if not scoped_album_ids:
        return
    for batch in batched(scoped_album_ids, size=500):
        placeholders = ", ".join("?" for _value in batch)
        connection.execute(
            f"DELETE FROM library_albums WHERE album_id IN ({placeholders})",
            batch,
        )


def delete_library_tracks_by_path(
    connection: sqlite3.Connection,
    paths: Iterable[str],
) -> None:
    scoped_paths = list(dict.fromkeys(paths))
    if not scoped_paths:
        return
    for batch in batched(scoped_paths, size=500):
        placeholders = ", ".join("?" for _value in batch)
        connection.execute(
            f"DELETE FROM library_tracks WHERE path IN ({placeholders})",
            batch,
        )


def upsert_library_track_row(
    connection: sqlite3.Connection,
    track: TrackRecord,
    *,
    album_id: str | None,
    root_position: int | None,
) -> int:
    params = library_track_row_params(track, album_id=album_id, root_position=root_position)
    if track.track_id is None:
        cursor = connection.execute(
            """
            INSERT INTO library_tracks (
                album_id,
                root_position,
                path,
                file_created_at,
                file_modified_at_ns,
                file_size_bytes,
                sidecar_artwork_path,
                sidecar_artwork_modified_at_ns,
                sidecar_artwork_size_bytes,
                file_type,
                scan_error,
                artist,
                album_artist,
                composer,
                album,
                title,
                work,
                grouping,
                movement_name,
                is_compilation,
                track_number,
                disc_number,
                date,
                duration_seconds,
                bitrate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )
        track.track_id = int(cursor.lastrowid)
    else:
        connection.execute(
            """
            UPDATE library_tracks
            SET album_id = ?,
                root_position = ?,
                path = ?,
                file_created_at = ?,
                file_modified_at_ns = ?,
                file_size_bytes = ?,
                sidecar_artwork_path = ?,
                sidecar_artwork_modified_at_ns = ?,
                sidecar_artwork_size_bytes = ?,
                file_type = ?,
                scan_error = ?,
                artist = ?,
                album_artist = ?,
                composer = ?,
                album = ?,
                title = ?,
                work = ?,
                grouping = ?,
                movement_name = ?,
                is_compilation = ?,
                track_number = ?,
                disc_number = ?,
                date = ?,
                duration_seconds = ?,
                bitrate = ?
            WHERE track_id = ?
            """,
            (*params, track.track_id),
        )
    return int(track.track_id)


def library_track_row_params(
    track: TrackRecord,
    *,
    album_id: str | None,
    root_position: int | None,
) -> tuple[object, ...]:
    return (
        album_id,
        root_position,
        track.path,
        track.file_created_at,
        track.file_modified_at_ns,
        track.file_size_bytes,
        track.sidecar_artwork_path,
        track.sidecar_artwork_modified_at_ns,
        track.sidecar_artwork_size_bytes,
        track.file_type,
        track.scan_error,
        track.artist,
        track.album_artist,
        track.composer,
        track.album,
        track.title,
        track.work,
        track.grouping,
        track.movement_name,
        1 if track.is_compilation else 0,
        track.track_number,
        track.disc_number,
        track.date,
        track.duration_seconds,
        track.bitrate,
    )


def replace_library_track_children(
    connection: sqlite3.Connection,
    track_id: int,
    track: TrackRecord,
) -> None:
    connection.execute("DELETE FROM library_track_genres WHERE track_id = ?", (track_id,))
    connection.execute("DELETE FROM library_track_styles WHERE track_id = ?", (track_id,))
    connection.execute("DELETE FROM library_track_artwork WHERE track_id = ?", (track_id,))
    for position, genre in enumerate(normalize_genre_values(track.genres)):
        connection.execute(
            """
            INSERT INTO library_track_genres (track_id, position, genre)
            VALUES (?, ?, ?)
            """,
            (track_id, position, genre),
        )
    for position, style in enumerate(normalize_genre_values(track.styles)):
        connection.execute(
            """
            INSERT INTO library_track_styles (track_id, position, style)
            VALUES (?, ?, ?)
            """,
            (track_id, position, style),
        )
    for height_px, artwork in (
        (TRACK_ARTWORK_HEIGHT, track.artwork),
        (ALBUM_ARTWORK_HEIGHT, track.album_artwork),
    ):
        if artwork is not None and artwork.data:
            connection.execute(
                """
                INSERT INTO library_track_artwork (
                    track_id,
                    height_px,
                    mime_type,
                    data
                )
                VALUES (?, ?, ?, ?)
                """,
                (track_id, height_px, artwork.mime_type, artwork.data),
            )


def replace_library_track_source(
    connection: sqlite3.Connection,
    track_id: int,
    track: TrackRecord,
    *,
    root_position: int | None,
) -> None:
    source = track.source or TrackSourceRecord(
        source_kind=SOURCE_KIND_LOCAL,
        root_position=root_position,
        canonical_path=track.path,
        size_bytes=track.file_size_bytes,
    )
    connection.execute(
        """
        INSERT INTO library_track_sources (
            track_id,
            source_kind,
            root_position,
            canonical_path,
            object_key,
            etag,
            version_id,
            last_modified,
            content_type,
            size_bytes,
            sidecar_object_key,
            sidecar_etag,
            sidecar_version_id,
            sidecar_last_modified,
            sidecar_content_type,
            sidecar_size_bytes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(track_id) DO UPDATE SET
            source_kind = excluded.source_kind,
            root_position = excluded.root_position,
            canonical_path = excluded.canonical_path,
            object_key = excluded.object_key,
            etag = excluded.etag,
            version_id = excluded.version_id,
            last_modified = excluded.last_modified,
            content_type = excluded.content_type,
            size_bytes = excluded.size_bytes,
            sidecar_object_key = excluded.sidecar_object_key,
            sidecar_etag = excluded.sidecar_etag,
            sidecar_version_id = excluded.sidecar_version_id,
            sidecar_last_modified = excluded.sidecar_last_modified,
            sidecar_content_type = excluded.sidecar_content_type,
            sidecar_size_bytes = excluded.sidecar_size_bytes
        """,
        (
            track_id,
            source.source_kind,
            root_position,
            source.canonical_path or track.path,
            source.object_key,
            source.etag,
            source.version_id,
            source.last_modified,
            source.content_type,
            source.size_bytes,
            source.sidecar_object_key,
            source.sidecar_etag,
            source.sidecar_version_id,
            source.sidecar_last_modified,
            source.sidecar_content_type,
            source.sidecar_size_bytes,
        ),
    )


def reconcile_playlist_item_track_ids(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        UPDATE library_playlist_items
        SET track_id = (
            SELECT tracks.track_id
            FROM library_tracks AS tracks
            WHERE tracks.path = library_playlist_items.path
        )
        WHERE EXISTS (
            SELECT 1
            FROM library_tracks AS tracks
            WHERE tracks.path = library_playlist_items.path
        )
        """
    )
    connection.execute(
        """
        UPDATE library_playlist_items
        SET track_id = NULL
        WHERE track_id IS NOT NULL
            AND NOT EXISTS (
                SELECT 1
                FROM library_tracks AS tracks
                WHERE tracks.track_id = library_playlist_items.track_id
                    AND tracks.path = library_playlist_items.path
            )
        """
    )


def save_explicit_library_playlists(
    connection: sqlite3.Connection,
    playlists: Iterable[PlaylistRecord],
    track_ids_by_path: dict[str, int],
) -> None:
    connection.execute("DELETE FROM library_playlist_items")
    connection.execute("DELETE FROM library_playlists")
    now = utc_now_iso()
    for playlist in playlists:
        name = playlist.name or "Playlist"
        items = list(playlist.items)
        kind = "remote" if any(is_url_resource(item.path) for item in items) else "local"
        source = playlist.source or "file_import"
        cursor = connection.execute(
            """
            INSERT INTO library_playlists (
                playlist_id,
                name,
                kind,
                source,
                cover_svg,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                playlist.playlist_id,
                name,
                kind if kind in {"local", "remote"} else "local",
                source if source in {"manual", "file_import"} else "file_import",
                playlist.cover_svg or playlist_cover_svg(name),
                playlist.created_at or now,
                playlist.updated_at or playlist.created_at or now,
            ),
        )
        playlist_id = (
            int(playlist.playlist_id)
            if playlist.playlist_id is not None
            else int(cursor.lastrowid)
        )
        playlist.playlist_id = playlist_id
        for position, item in enumerate(items):
            track_id = item.track_id or track_ids_by_path.get(item.path)
            is_tracked = track_id is not None
            connection.execute(
                """
                INSERT INTO library_playlist_items (
                    playlist_id,
                    position,
                    path,
                    track_id,
                    title,
                    duration_seconds,
                    duration_is_indeterminate,
                    genre,
                    cover_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    playlist_id,
                    position,
                    item.path,
                    track_id,
                    None if is_tracked else item.title or item.path,
                    None if is_tracked or item.duration_is_indeterminate else item.duration_seconds,
                    0 if is_tracked else 1 if item.duration_is_indeterminate else 0,
                    None if is_tracked else item.genre,
                    None if is_tracked else item.cover_url,
                ),
            )


def copy_album_musicbrainz_links_from_legacy_album_ids(
    connection: sqlite3.Connection,
    albums: Iterable[LocalAlbum],
    *,
    legacy_album_ids_by_album_id: dict[str, tuple[str, ...]] | None = None,
) -> None:
    legacy_album_ids_by_album_id = legacy_album_ids_by_album_id or {}
    for album in albums:
        for legacy_album_id in album_legacy_musicbrainz_album_ids(
            album,
            extra_album_ids=legacy_album_ids_by_album_id.get(album.album_id, ()),
        ):
            row = connection.execute(
                """
                SELECT release_mbid, release_group_mbid
                FROM album_musicbrainz_links
                WHERE file_album_id = ?
                """,
                (legacy_album_id,),
            ).fetchone()
            if row is None:
                continue

            release_mbid = clean_mbid(row["release_mbid"])
            release_group_mbid = clean_mbid(row["release_group_mbid"])
            if not release_mbid and not release_group_mbid:
                continue

            store_album_musicbrainz_link(
                connection,
                album.file_album_id,
                release_mbid=release_mbid,
                release_group_mbid=release_group_mbid,
            )


def copy_track_musicbrainz_links_from_existing_album_ids(
    connection: sqlite3.Connection,
) -> None:
    rows = list(
        connection.execute(
            """
            SELECT path, album_id
            FROM library_tracks
            WHERE COALESCE(path, '') != ''
                AND COALESCE(album_id, '') != ''
            """
        )
    )
    existing_track_links = load_album_musicbrainz_track_links(
        connection,
        (str(row["path"]) for row in rows),
    )
    for row in rows:
        path = str(row["path"])
        if path in existing_track_links:
            continue
        album_id = str(row["album_id"])
        file_album_id = file_album_id_from_album_id(album_id)
        if file_album_id == album_id:
            continue

        link = album_musicbrainz_link_for_album_id(connection, album_id)
        if link is None or not link.has_identifier:
            continue

        store_album_musicbrainz_track_link(
            connection,
            path,
            file_album_id,
            release_mbid=link.release_mbid,
            release_group_mbid=link.release_group_mbid,
        )


def album_legacy_musicbrainz_album_ids(
    album: LocalAlbum,
    *,
    extra_album_ids: Iterable[str] = (),
) -> tuple[str, ...]:
    return album_legacy_album_ids(
        album,
        extra_album_ids=extra_album_ids,
        include_current_release_id=True,
    )


def album_legacy_state_album_ids(
    album: LocalAlbum,
    *,
    extra_album_ids: Iterable[str] = (),
) -> tuple[str, ...]:
    return album_legacy_album_ids(
        album,
        extra_album_ids=extra_album_ids,
        include_current_release_id=False,
    )


def album_legacy_album_ids(
    album: LocalAlbum,
    *,
    extra_album_ids: Iterable[str] = (),
    include_current_release_id: bool,
) -> tuple[str, ...]:
    artists = tuple(artist for artist in album.artists if artist)
    album_slug = normalize_slug_text(album.album)
    if not album_slug:
        return ()

    legacy_ids: list[str] = []
    seen: set[str] = set()
    for album_id in extra_album_ids:
        normalized_album_id = str(album_id or "").strip()
        if not normalized_album_id or normalized_album_id == album.album_id:
            continue
        if normalized_album_id in seen:
            continue
        seen.add(normalized_album_id)
        legacy_ids.append(normalized_album_id)

    if include_current_release_id and album.release_variant:
        seen.add(album.album_id)
        legacy_ids.append(album.album_id)

    for artist_text in legacy_album_artist_id_texts(artists):
        album_id = local_album_id(artist_text, album.album)
        if not album_id.split("::", 1)[0]:
            continue
        for candidate in (
            album_id,
            local_album_id(
                artist_text,
                album.album,
                release_variant=album.release_variant,
            ),
        ):
            if candidate == album.album_id or candidate in seen:
                continue
            seen.add(candidate)
            legacy_ids.append(candidate)
    return tuple(legacy_ids)


def legacy_album_artist_id_texts(artists: tuple[str, ...]) -> tuple[str, ...]:
    if not artists:
        return ()
    joined = (
        album_artist_id_text(artists),
        " and ".join(artists),
        " with ".join(artists),
    )
    return tuple(value for value in dict.fromkeys(joined) if value)


def apply_musicbrainz_release_variants(
    connection: sqlite3.Connection,
    tracks: Iterable[TrackRecord],
    *,
    stats: MusicBrainzLookupStats | None = None,
    log: Callable[[str], None] | None = None,
) -> None:
    lookup_stats = stats or MusicBrainzLookupStats()
    client = MusicBrainzClient(stats=lookup_stats, log=log)
    track_list = list(tracks)
    tracks_by_release: dict[str, list[TrackRecord]] = defaultdict(list)
    for track in track_list:
        release_mbid = normalized_track_musicbrainz_mbid(track, entity_type="release")
        release_group_mbid = normalized_track_musicbrainz_mbid(
            track,
            entity_type="release-group",
        )
        track.musicbrainz_release_mbid = release_mbid
        track.musicbrainz_release_group_mbid = release_group_mbid
        if release_mbid and not normalize_release_variant(track.musicbrainz_release_variant):
            tracks_by_release[release_mbid].append(track)

    apply_musicbrainz_track_links_to_tracks(
        connection,
        track_list,
        tracks_by_release,
    )
    apply_discogs_track_links_to_tracks(connection, track_list)
    apply_musicbrainz_link_variants_to_tracks(
        connection,
        track_list,
        tracks_by_release,
    )

    for release_mbid, release_tracks in tracks_by_release.items():
        payload = get_musicbrainz_entity(
            connection,
            client,
            entity_type="release",
            mbid=release_mbid,
        )
        if payload is None:
            continue
        release_variant = musicbrainz_release_fingerprint(
            payload,
            fallback_release_mbid=release_mbid,
        )
        release_group_mbid = musicbrainz_release_group_mbid(payload)
        for track in release_tracks:
            track.musicbrainz_release_variant = release_variant
            if release_group_mbid and not track.musicbrainz_release_group_mbid:
                track.musicbrainz_release_group_mbid = release_group_mbid


def apply_musicbrainz_track_links_to_tracks(
    connection: sqlite3.Connection,
    tracks: Iterable[TrackRecord],
    tracks_by_release: dict[str, list[TrackRecord]],
) -> None:
    track_list = list(tracks)
    links_by_path = load_album_musicbrainz_track_links(
        connection,
        (track.path for track in track_list),
    )
    if not links_by_path:
        return

    for track in track_list:
        if normalize_release_variant(track.musicbrainz_release_variant):
            continue
        link = links_by_path.get(track.path)
        if link is None:
            continue
        base_album_id = track_base_album_id(track)
        if not base_album_id:
            continue

        if not track.musicbrainz_release_mbid and link.release_mbid:
            track.musicbrainz_release_mbid = link.release_mbid
        if not track.musicbrainz_release_group_mbid and link.release_group_mbid:
            track.musicbrainz_release_group_mbid = link.release_group_mbid
        if track.musicbrainz_release_mbid:
            release_tracks = tracks_by_release[track.musicbrainz_release_mbid]
            if not any(existing is track for existing in release_tracks):
                release_tracks.append(track)
        if link.file_album_id != base_album_id:
            store_album_musicbrainz_track_link(
                connection,
                track.path,
                base_album_id,
                release_mbid=link.release_mbid,
                release_group_mbid=link.release_group_mbid,
            )


def apply_discogs_track_links_to_tracks(
    connection: sqlite3.Connection,
    tracks: Iterable[TrackRecord],
) -> None:
    track_list = list(tracks)
    links_by_path = load_album_metadata_track_links(
        connection,
        (track.path for track in track_list),
        provider=METADATA_PROVIDER_DISCOGS,
    )
    if not links_by_path:
        return

    for track in track_list:
        if normalize_release_variant(track.musicbrainz_release_variant):
            continue
        link = links_by_path.get(track.path)
        if link is None:
            continue
        release_variant = discogs_release_variant_from_track_link(link)
        if not release_variant:
            continue
        base_album_id = track_base_album_id(track)
        if not base_album_id:
            continue

        track.musicbrainz_release_variant = release_variant
        if link.file_album_id != base_album_id:
            store_album_metadata_track_link(
                connection,
                track.path,
                base_album_id,
                provider=link.provider,
                entity_type=link.entity_type,
                entity_id=link.entity_id,
                related_entity_type=link.related_entity_type,
                related_entity_id=link.related_entity_id,
            )


def discogs_release_variant_from_track_link(
    link: AlbumMetadataTrackLink,
) -> str | None:
    if link.provider != METADATA_PROVIDER_DISCOGS:
        return None
    if link.entity_type != METADATA_ENTITY_RELEASE:
        return None
    return discogs_release_fingerprint(link.entity_id)


def apply_musicbrainz_link_variants_to_tracks(
    connection: sqlite3.Connection,
    tracks: Iterable[TrackRecord],
    tracks_by_release: dict[str, list[TrackRecord]],
) -> None:
    tracks_by_base_album_id: dict[str, list[TrackRecord]] = defaultdict(list)
    candidate_ids_by_base_album_id: dict[str, set[str]] = defaultdict(set)
    existing_rows_by_path = library_track_rows_by_path(connection)
    for track in tracks:
        if normalize_release_variant(track.musicbrainz_release_variant):
            continue
        base_album_id = track_base_album_id(track)
        if base_album_id:
            tracks_by_base_album_id[base_album_id].append(track)
            candidate_ids_by_base_album_id[base_album_id].update(
                track_base_album_id_candidates(
                    track,
                    existing_row=existing_rows_by_path.get(track.path),
                )
            )

    if not tracks_by_base_album_id:
        return

    musicbrainz_links = load_album_musicbrainz_links(connection)
    for base_album_id, album_tracks in tracks_by_base_album_id.items():
        candidates: dict[tuple[str | None, str | None], tuple[str | None, str | None, str | None]] = {}
        candidate_album_ids = candidate_ids_by_base_album_id.get(
            base_album_id,
            {base_album_id},
        )
        for link_group in musicbrainz_links.values():
            for link in link_group:
                release_variant = release_variant_for_link_album_id_candidates(
                    link.file_album_id,
                    candidate_album_ids,
                )
                if link.file_album_id not in candidate_album_ids and release_variant is None:
                    continue
                if release_variant is not None and not link.release_mbid:
                    continue
                key = (link.release_mbid, link.release_group_mbid)
                existing = candidates.get(key)
                if existing is None or (existing[0] is None and release_variant is not None):
                    candidates[key] = (
                        release_variant,
                        link.release_mbid,
                        link.release_group_mbid,
                    )

        if len(candidates) != 1:
            continue

        release_variant, release_mbid, release_group_mbid = next(iter(candidates.values()))
        for track in album_tracks:
            if track.musicbrainz_release_mbid or track.musicbrainz_release_group_mbid:
                continue
            if release_mbid:
                track.musicbrainz_release_mbid = release_mbid
            if release_group_mbid:
                track.musicbrainz_release_group_mbid = release_group_mbid
            if release_variant:
                track.musicbrainz_release_variant = release_variant
            elif release_mbid:
                tracks_by_release[release_mbid].append(track)


def release_variant_from_link_album_id(
    link_album_id: str,
    base_album_id: str,
) -> str | None:
    prefix = f"{base_album_id}::"
    if not link_album_id.startswith(prefix):
        return None
    release_variant = link_album_id[len(prefix) :]
    if not release_variant or "::" in release_variant:
        return None
    return normalize_release_variant(release_variant)


def release_variant_for_link_album_id_candidates(
    link_album_id: str,
    album_id_candidates: Iterable[str],
) -> str | None:
    for candidate in album_id_candidates:
        release_variant = release_variant_from_link_album_id(link_album_id, candidate)
        if release_variant is not None:
            return release_variant
    return None


def track_base_album_id(track: TrackRecord) -> str | None:
    artist = track_raw_album_artist_id_text(track)
    album = track.album
    if not artist or not album:
        return None
    if not normalize_text(artist) or not normalize_text(album):
        return None
    return local_album_id(artist, album)


def track_base_album_id_candidates(
    track: TrackRecord,
    *,
    existing_row: sqlite3.Row | None = None,
) -> tuple[str, ...]:
    album = track.album
    if not album:
        return ()

    candidates: list[str] = []
    raw_album_id = track_base_album_id(track)
    if raw_album_id:
        candidates.append(raw_album_id)

    mapped_artist = album_artist_id_text(track_album_artist_values(track))
    if mapped_artist and normalize_text(mapped_artist) and normalize_text(album):
        candidates.append(local_album_id(mapped_artist, album))

    if (
        existing_row is not None
        and existing_row["album_id"]
        and track_raw_album_identity_matches_row(track, existing_row)
    ):
        existing_album_id = str(existing_row["album_id"])
        candidates.append(file_album_id_from_album_id(existing_album_id))
        candidates.append(existing_album_id)

    return tuple(dict.fromkeys(candidate for candidate in candidates if candidate))


def normalized_track_musicbrainz_mbid(
    track: TrackRecord,
    *,
    entity_type: str,
) -> str | None:
    value = (
        track.musicbrainz_release_mbid
        if entity_type == "release"
        else track.musicbrainz_release_group_mbid
    )
    if not value:
        return None
    try:
        return normalize_musicbrainz_mbid(str(value), entity_type=entity_type)
    except ValueError:
        return None


def store_scanned_album_musicbrainz_links(
    connection: sqlite3.Connection,
    albums: Iterable[LocalAlbum],
) -> None:
    for album in albums:
        release_mbid = clean_mbid(album.musicbrainz_release_mbid)
        release_group_mbid = clean_mbid(album.musicbrainz_release_group_mbid)
        if not release_mbid and not release_group_mbid:
            continue
        store_album_musicbrainz_link(
            connection,
            album.file_album_id,
            release_mbid=release_mbid,
            release_group_mbid=release_group_mbid,
        )


def musicbrainz_link_for_track(
    musicbrainz_links: dict[str, tuple[MusicBrainzAlbumLink, ...]],
    track: TrackRecord,
) -> MusicBrainzAlbumLink | None:
    base_album_ids = track_base_album_id_candidates(track)
    if not base_album_ids:
        return None

    candidates: list[MusicBrainzAlbumLink] = []
    seen_candidates: set[tuple[str, str | None, str | None]] = set()
    for base_album_id in base_album_ids:
        for candidate in musicbrainz_links.get(base_album_id, ()):
            key = (
                candidate.file_album_id,
                candidate.release_mbid,
                candidate.release_group_mbid,
            )
            if key in seen_candidates:
                continue
            seen_candidates.add(key)
            candidates.append(candidate)
    release_variant = normalize_release_variant(track.musicbrainz_release_variant)
    if release_variant:
        for base_album_id in base_album_ids:
            for candidate in musicbrainz_links.get(
                f"{base_album_id}::{release_variant}",
                (),
            ):
                key = (
                    candidate.file_album_id,
                    candidate.release_mbid,
                    candidate.release_group_mbid,
                )
                if key in seen_candidates:
                    continue
                seen_candidates.add(key)
                candidates.append(candidate)

    release_mbid = clean_mbid(track.musicbrainz_release_mbid)
    if release_mbid:
        for candidate in candidates:
            if candidate.release_mbid == release_mbid:
                return candidate

    release_group_mbid = clean_mbid(track.musicbrainz_release_group_mbid)
    if release_group_mbid:
        for candidate in candidates:
            if candidate.release_group_mbid == release_group_mbid:
                return candidate

    return candidates[0] if len(candidates) == 1 else None


def resolve_library_genres(
    library: MusicLibrary,
    source: Path,
    *,
    ignore_musicbrainz: bool = False,
    log: Callable[[str], None] | None = None,
    connection: sqlite3.Connection | None = None,
    album_artist_split_patterns: Iterable[str | None] = DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
) -> GenreResolutionStats:
    owns_connection = connection is None
    if connection is None:
        connection = connect_existing_database(source)
    try:
        matcher = load_taxonomy_genre_matcher_from_connection(connection)
        stats = GenreResolutionStats()
        if not matcher.candidates:
            return stats

        mb_lookup_stats = MusicBrainzLookupStats()
        apply_album_artist_mappings(
            connection,
            library.tracks,
            split_patterns=album_artist_split_patterns,
        )
        apply_musicbrainz_release_variants(
            connection,
            library.tracks,
            stats=mb_lookup_stats,
            log=log,
        )
        albums = group_library_albums(library)
        album_ids_by_key = album_ids_by_lookup_key(albums)
        path_legacy_album_ids = legacy_album_ids_by_current_album_id_from_track_paths(
            connection,
            library.tracks,
            album_ids_by_key,
        )
        copy_album_musicbrainz_links_from_legacy_album_ids(
            connection,
            albums,
            legacy_album_ids_by_album_id=path_legacy_album_ids,
        )
        store_scanned_album_musicbrainz_links(
            connection,
            albums,
        )
        musicbrainz_links = load_album_musicbrainz_links(connection)
        musicbrainz_client = MusicBrainzClient(stats=mb_lookup_stats, log=log)

        for tracks in genre_resolution_groups(library.tracks):
            musicbrainz_link = musicbrainz_link_for_track(musicbrainz_links, tracks[0])
            album_match = None
            if (
                not ignore_musicbrainz
                and musicbrainz_link is not None
                and musicbrainz_link.has_identifier
            ):
                album_match = resolve_musicbrainz_album_genres(
                    connection,
                    tracks,
                    musicbrainz_link,
                    matcher,
                    stats,
                    musicbrainz_client,
                    log=log,
                )
            if album_match is None:
                album_match = resolve_album_genres(tracks, matcher, stats)

            if album_match.genres == [UNKNOWN_GENRE_TAG]:
                stats.unknown_albums += 1
                stats.unknown_tracks += len(tracks)
            for track in tracks:
                track.genres = list(album_match.genres)
                track.styles = list(album_match.styles)

        if owns_connection:
            connection.commit()
    finally:
        if owns_connection:
            connection.close()

    stats.musicbrainz_api_calls = mb_lookup_stats.api_calls
    stats.musicbrainz_cached_calls = mb_lookup_stats.cached_calls
    stats.musicbrainz_rate_limit_retries = mb_lookup_stats.rate_limit_retries
    stats.musicbrainz_fetch_failures = mb_lookup_stats.fetch_failures
    return stats


def genre_resolution_groups(tracks: list[TrackRecord]) -> list[list[TrackRecord]]:
    grouped_tracks: dict[str, list[TrackRecord]] = defaultdict(list)
    ungrouped_tracks: list[list[TrackRecord]] = []
    for track in tracks:
        album_id = track_album_id(track)
        if not album_id:
            ungrouped_tracks.append([track])
            continue
        grouped_tracks[album_id].append(track)
    return [*grouped_tracks.values(), *ungrouped_tracks]


def resolve_album_genres(
    tracks: list[TrackRecord],
    matcher: TaxonomyGenreMatcher,
    stats: GenreResolutionStats,
) -> ResolvedAlbumGenres:
    resolved_genres: list[str] = []
    resolved_styles: list[str] = []
    for track in tracks:
        for genre in normalize_genre_values(track.genres):
            if is_unknown_genre_marker(genre):
                continue
            match = matcher.resolve(genre)
            update_genre_resolution_stats(stats, match.resolution)
            resolved_genres.extend(match.genres)
            resolved_styles.extend(match.styles)

    album_genres = normalize_genre_values(resolved_genres)
    album_styles = normalize_genre_values(resolved_styles)
    if album_genres:
        return ResolvedAlbumGenres(genres=album_genres, styles=album_styles)
    return ResolvedAlbumGenres(genres=[UNKNOWN_GENRE_TAG], styles=album_styles)


def is_unknown_genre_marker(value: str) -> bool:
    return value.strip().casefold() == UNKNOWN_GENRE_TAG.casefold()


def resolve_musicbrainz_album_genres(
    connection: sqlite3.Connection,
    tracks: list[TrackRecord],
    musicbrainz_link: MusicBrainzAlbumLink,
    matcher: TaxonomyGenreMatcher,
    stats: GenreResolutionStats,
    client: MusicBrainzClient,
    *,
    log: Callable[[str], None] | None,
) -> ResolvedAlbumGenres | None:
    payloads: list[tuple[str, str, dict[str, object]]] = []

    if musicbrainz_link.release_mbid:
        payload = get_musicbrainz_entity(
            connection,
            client,
            entity_type="release",
            mbid=musicbrainz_link.release_mbid,
        )
        if payload is not None:
            payloads.append(("release", musicbrainz_link.release_mbid, payload))
            maybe_set_release_group_from_release_payload(
                connection,
                tracks,
                musicbrainz_link,
                payload,
                log=log,
            )

    if musicbrainz_link.release_group_mbid:
        payload = get_musicbrainz_entity(
            connection,
            client,
            entity_type="release-group",
            mbid=musicbrainz_link.release_group_mbid,
        )
        if payload is not None:
            payloads.append(("release-group", musicbrainz_link.release_group_mbid, payload))

    if not payloads:
        emit_musicbrainz_log(
            log,
            f"no MusicBrainz data available for {album_log_label(tracks)}; using audio tags",
        )
        return None

    source_genres: dict[str, tuple[str, str, str]] = {}
    for entity_type, mbid, payload in payloads:
        for genre in musicbrainz_genres(payload):
            source_genres.setdefault(genre.casefold(), (genre, entity_type, mbid))

    if not source_genres:
        emit_musicbrainz_log(
            log,
            f"MusicBrainz returned no genres for {album_log_label(tracks)}; using audio tags",
        )
        return None

    stats.musicbrainz_album_overrides += 1

    resolved_genres: list[str] = []
    resolved_styles: list[str] = []
    for genre, entity_type, mbid in source_genres.values():
        match = matcher.resolve(genre)
        update_genre_resolution_stats(stats, match.resolution)
        if match.resolution == "unmatched":
            stats.musicbrainz_unmatched_genres += 1
            emit_musicbrainz_log(
                log,
                f"MusicBrainz genre {genre!r} from {entity_type} {mbid} "
                f"did not match taxonomy for {album_log_label(tracks)}",
            )
            continue
        resolved_genres.extend(match.genres)
        resolved_styles.extend(match.styles)

    album_genres = normalize_genre_values(resolved_genres)
    album_styles = normalize_genre_values(resolved_styles)
    if album_genres:
        return ResolvedAlbumGenres(genres=album_genres, styles=album_styles)

    emit_musicbrainz_log(
        log,
        f"no MusicBrainz genres matched taxonomy for {album_log_label(tracks)}; "
        f"setting to {UNKNOWN_GENRE_TAG}",
    )
    return ResolvedAlbumGenres(genres=[UNKNOWN_GENRE_TAG], styles=album_styles)


def maybe_set_release_group_from_release_payload(
    connection: sqlite3.Connection,
    tracks: list[TrackRecord],
    musicbrainz_link: MusicBrainzAlbumLink,
    payload: dict[str, object],
    *,
    log: Callable[[str], None] | None,
) -> None:
    if musicbrainz_link.release_group_mbid:
        return

    release_group_mbid = musicbrainz_release_group_mbid(payload)
    if not release_group_mbid:
        return

    if not store_album_musicbrainz_release_group_if_missing(
        connection,
        musicbrainz_link.album_id,
        release_mbid=musicbrainz_link.release_mbid,
        release_group_mbid=release_group_mbid,
    ):
        return

    musicbrainz_link.release_group_mbid = release_group_mbid
    emit_musicbrainz_log(
        log,
        f"set release group {release_group_mbid} from release payload for "
        f"{album_log_label(tracks)}",
    )


def update_genre_resolution_stats(stats: GenreResolutionStats, resolution: str) -> None:
    if resolution == "exact_genre":
        stats.exact_genre_matches += 1
    elif resolution == "exact_style":
        stats.exact_style_matches += 1
    elif resolution == "fuzzy_genre":
        stats.fuzzy_genre_matches += 1
    elif resolution == "fuzzy_style":
        stats.fuzzy_style_matches += 1
    else:
        stats.unmatched += 1


def album_log_label(tracks: list[TrackRecord]) -> str:
    if not tracks:
        return "<unknown album>"
    track = tracks[0]
    artist = track.album_artist or track.artist or "<unknown artist>"
    album = track.album or "<unknown album>"
    return f"{artist} - {album}"


def emit_musicbrainz_log(log: Callable[[str], None] | None, message: str) -> None:
    if log is not None:
        log(f"musicbrainz: {message}")


def resolve_library_cover_art(
    library: MusicLibrary,
    source: Path,
    *,
    log: Callable[[str], None] | None = None,
    connection: sqlite3.Connection | None = None,
    album_artist_split_patterns: Iterable[str | None] = DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
) -> CoverArtResolutionStats:
    stats = CoverArtResolutionStats()
    caa_stats = CoverArtArchiveStats()
    itunes_stats = ItunesLookupStats()
    owns_connection = connection is None
    if connection is None:
        connection = connect_existing_database(source)
    try:
        apply_album_artist_mappings(
            connection,
            library.tracks,
            split_patterns=album_artist_split_patterns,
        )
        apply_musicbrainz_release_variants(connection, library.tracks, log=log)
        albums = group_library_albums(library)
        album_ids_by_key = album_ids_by_lookup_key(albums)
        path_legacy_album_ids = legacy_album_ids_by_current_album_id_from_track_paths(
            connection,
            library.tracks,
            album_ids_by_key,
        )
        copy_album_musicbrainz_links_from_legacy_album_ids(
            connection,
            albums,
            legacy_album_ids_by_album_id=path_legacy_album_ids,
        )
        store_scanned_album_musicbrainz_links(
            connection,
            albums,
        )
        musicbrainz_links = load_album_musicbrainz_links(connection)
        discogs_links = load_album_metadata_links(
            connection,
            provider=METADATA_PROVIDER_DISCOGS,
        )
        discogs_track_links = load_album_metadata_track_links(
            connection,
            (track.path for track in library.tracks),
            provider=METADATA_PROVIDER_DISCOGS,
        )
        caa_client = CoverArtArchiveClient(stats=caa_stats, log=log)
        itunes_client = ItunesLookupClient(stats=itunes_stats, log=log)
        discogs_stats = DiscogsLookupStats()
        discogs_client = DiscogsClient(stats=discogs_stats, log=log)

        for tracks in genre_resolution_groups(library.tracks):
            musicbrainz_link = musicbrainz_link_for_track(musicbrainz_links, tracks[0])
            discogs_link = discogs_link_for_tracks(
                discogs_links,
                discogs_track_links,
                tracks,
            )
            chosen_artwork = itunes_artwork_for_album(
                connection,
                tracks,
                itunes_client,
            )
            if chosen_artwork is None and discogs_link is not None:
                artwork_by_source = discogs_artworks_for_album(
                    connection,
                    discogs_link,
                    discogs_client,
                    caa_client,
                )
                chosen_artwork = artwork_by_source.get("release") or artwork_by_source.get(
                    "master"
                )
            if chosen_artwork is None and musicbrainz_link is not None and musicbrainz_link.has_identifier:
                artwork_by_source = cover_art_archive_artworks_for_album(
                    connection,
                    musicbrainz_link,
                    caa_client,
                )
                chosen_artwork = artwork_by_source.get("release") or artwork_by_source.get(
                    "release-group"
                )
            if chosen_artwork is None:
                continue

            artwork_by_height = thumbnail_artworks(
                chosen_artwork,
                heights=(TRACK_ARTWORK_HEIGHT, ALBUM_ARTWORK_HEIGHT),
            )
            if not artwork_by_height:
                continue

            updated_tracks = 0
            for track in tracks:
                if TRACK_ARTWORK_HEIGHT in artwork_by_height:
                    track.artwork = artwork_by_height[TRACK_ARTWORK_HEIGHT]
                if ALBUM_ARTWORK_HEIGHT in artwork_by_height:
                    track.album_artwork = artwork_by_height[ALBUM_ARTWORK_HEIGHT]
                if track.artwork is not None or track.album_artwork is not None:
                    updated_tracks += 1

            if updated_tracks:
                stats.album_cover_overrides += 1
                stats.tracks_updated += updated_tracks

        if owns_connection:
            connection.commit()
    finally:
        if owns_connection:
            connection.close()

    stats.itunes_lookup_api_calls = itunes_stats.lookup_api_calls
    stats.itunes_lookup_cached_calls = itunes_stats.lookup_cached_calls
    stats.metadata_api_calls = caa_stats.metadata_api_calls + discogs_stats.api_calls
    stats.metadata_cached_calls = caa_stats.metadata_cached_calls + discogs_stats.cached_calls
    stats.image_downloads = caa_stats.image_downloads
    stats.image_cached_calls = caa_stats.image_cached_calls
    stats.fetch_failures = (
        itunes_stats.fetch_failures
        + caa_stats.fetch_failures
        + discogs_stats.fetch_failures
    )
    stats.missing_art = itunes_stats.missing_art + caa_stats.missing_art
    return stats


def discogs_link_for_tracks(
    discogs_links: dict[str, tuple[AlbumMetadataLink, ...]],
    discogs_track_links: dict[str, AlbumMetadataTrackLink],
    tracks: list[TrackRecord],
) -> AlbumMetadataLink | AlbumMetadataTrackLink | None:
    track_candidates = [
        link
        for link in unique_metadata_links(
            discogs_track_links.get(track.path)
            for track in tracks
        )
        if link.provider == METADATA_PROVIDER_DISCOGS
    ]
    if track_candidates:
        return track_candidates[0] if len(track_candidates) == 1 else None

    album_candidates: list[AlbumMetadataLink] = []
    for track in tracks:
        for album_id in metadata_album_id_candidates_for_track(track):
            album_candidates.extend(discogs_links.get(album_id, ()))

    unique_candidates = [
        link
        for link in unique_metadata_links(album_candidates)
        if link.provider == METADATA_PROVIDER_DISCOGS
    ]
    return unique_candidates[0] if len(unique_candidates) == 1 else None


def metadata_album_id_candidates_for_track(track: TrackRecord) -> tuple[str, ...]:
    candidates: list[str] = []
    release_variant = normalize_release_variant(track.musicbrainz_release_variant)
    for base_album_id in track_base_album_id_candidates(track):
        candidates.append(base_album_id)
        if release_variant:
            candidates.append(f"{base_album_id}::{release_variant}")
    return tuple(dict.fromkeys(candidates))


def unique_metadata_links(
    links: Iterable[AlbumMetadataLink | AlbumMetadataTrackLink | None],
) -> tuple[AlbumMetadataLink | AlbumMetadataTrackLink, ...]:
    candidates: list[AlbumMetadataLink | AlbumMetadataTrackLink] = []
    seen: set[tuple[str, str, str, str, str, str]] = set()
    for link in links:
        if link is None:
            continue
        key = metadata_link_identity(link)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(link)
    return tuple(candidates)


def metadata_link_identity(
    link: AlbumMetadataLink | AlbumMetadataTrackLink,
) -> tuple[str, str, str, str, str, str]:
    return (
        link.file_album_id,
        link.provider,
        link.entity_type,
        link.entity_id,
        link.related_entity_type or "",
        link.related_entity_id or "",
    )


def discogs_artworks_for_album(
    connection: sqlite3.Connection,
    metadata_link: AlbumMetadataLink | AlbumMetadataTrackLink,
    discogs_client: DiscogsClient,
    image_client: CoverArtArchiveClient,
) -> dict[str, TrackArtwork]:
    image_urls_by_source: dict[str, str] = {}
    master_id = (
        metadata_link.related_entity_id
        if metadata_link.related_entity_type == METADATA_ENTITY_MASTER
        else None
    )

    if metadata_link.entity_type == METADATA_ENTITY_RELEASE:
        payload = get_discogs_entity(
            connection,
            discogs_client,
            entity_type=METADATA_ENTITY_RELEASE,
            entity_id=metadata_link.entity_id,
        )
        if payload is not None:
            image_url = discogs_primary_image_url(payload)
            if image_url is not None:
                image_urls_by_source[METADATA_ENTITY_RELEASE] = image_url
            if master_id is None:
                master_id = discogs_master_id(payload)
    elif metadata_link.entity_type == METADATA_ENTITY_MASTER:
        master_id = metadata_link.entity_id

    if master_id and METADATA_ENTITY_RELEASE not in image_urls_by_source:
        payload = get_discogs_entity(
            connection,
            discogs_client,
            entity_type=METADATA_ENTITY_MASTER,
            entity_id=master_id,
        )
        if payload is not None:
            image_url = discogs_primary_image_url(payload)
            if image_url is not None:
                image_urls_by_source[METADATA_ENTITY_MASTER] = image_url

    for entity_type in (METADATA_ENTITY_RELEASE, METADATA_ENTITY_MASTER):
        image_url = image_urls_by_source.get(entity_type)
        if image_url is None:
            continue
        artwork = get_cover_art_archive_image(
            connection,
            image_client,
            image_url=image_url,
            user_agent=DISCOGS_USER_AGENT,
        )
        if artwork is not None:
            return {entity_type: artwork}
    return {}


def itunes_artwork_for_album(
    connection: sqlite3.Connection,
    tracks: list[TrackRecord],
    client: ItunesLookupClient,
) -> TrackArtwork | None:
    for candidate in itunes_lookup_candidates_for_album(tracks):
        artwork = get_itunes_lookup_image(
            connection,
            client,
            candidate=candidate,
        )
        if artwork is not None:
            return artwork
    return None


def itunes_lookup_candidates_for_album(
    tracks: list[TrackRecord],
) -> list[ItunesLookupCandidate]:
    seen_cache_keys: set[str] = set()
    candidates: list[ItunesLookupCandidate] = []
    for track in tracks:
        for lookup_kind, lookup_id in (
            ("album", track.itunes_store_album_id),
            ("track", track.itunes_store_track_id),
        ):
            if not lookup_id:
                continue
            candidate = ItunesLookupCandidate(
                lookup_kind=lookup_kind,
                lookup_id=lookup_id,
            )
            if candidate.cache_key in seen_cache_keys:
                continue
            seen_cache_keys.add(candidate.cache_key)
            candidates.append(candidate)
    return candidates


def cover_art_archive_artworks_for_album(
    connection: sqlite3.Connection,
    musicbrainz_link: MusicBrainzAlbumLink,
    client: CoverArtArchiveClient,
) -> dict[str, TrackArtwork]:
    image_urls_by_source: dict[str, str] = {}
    for entity_type, mbid in (
        ("release", musicbrainz_link.release_mbid),
        ("release-group", musicbrainz_link.release_group_mbid),
    ):
        if not mbid:
            continue
        payload = get_cover_art_archive_entity(
            connection,
            client,
            entity_type=entity_type,
            mbid=mbid,
        )
        if payload is None:
            continue
        image_url = front_image_url(payload)
        if image_url is None:
            continue
        image_urls_by_source[entity_type] = image_url

    for entity_type in ("release", "release-group"):
        image_url = image_urls_by_source.get(entity_type)
        if image_url is None:
            continue
        artwork = get_cover_art_archive_image(connection, client, image_url=image_url)
        if artwork is not None:
            return {entity_type: artwork}
    return {}


def load_rescan_tracks_by_path(source: Path) -> dict[str, TrackRecord]:
    with connect_existing_database(source) as connection:
        return {track.path: track for track in _load_rescan_track_records(connection)}


def _load_rescan_track_records(connection: sqlite3.Connection) -> list[TrackRecord]:
    genres_by_track: dict[int, list[str]] = defaultdict(list)
    for row in connection.execute(
        "SELECT track_id, genre FROM library_track_genres ORDER BY track_id, position"
    ):
        genres_by_track[int(row["track_id"])].append(str(row["genre"]))

    styles_by_track: dict[int, list[str]] = defaultdict(list)
    for row in connection.execute(
        "SELECT track_id, style FROM library_track_styles ORDER BY track_id, position"
    ):
        styles_by_track[int(row["track_id"])].append(str(row["style"]))

    taxonomy_genres = {
        str(row["genre"]).casefold()
        for row in connection.execute("SELECT genre FROM taxonomy_genres")
    }
    taxonomy_styles = {
        str(row["style"]).casefold()
        for row in connection.execute("SELECT style FROM taxonomy_styles")
    }

    sources_by_track: dict[int, TrackSourceRecord] = {}
    for row in connection.execute(
        """
        SELECT
            track_id,
            source_kind,
            root_position,
            canonical_path,
            object_key,
            etag,
            version_id,
            last_modified,
            content_type,
            size_bytes,
            sidecar_object_key,
            sidecar_etag,
            sidecar_version_id,
            sidecar_last_modified,
            sidecar_content_type,
            sidecar_size_bytes
        FROM library_track_sources
        """
    ):
        track_id = int(row["track_id"])
        sources_by_track[track_id] = TrackSourceRecord(
            source_kind=str(row["source_kind"] or SOURCE_KIND_LOCAL),
            root_position=(
                int(row["root_position"])
                if row["root_position"] is not None
                else None
            ),
            canonical_path=str(row["canonical_path"] or ""),
            object_key=row["object_key"],
            etag=row["etag"],
            version_id=row["version_id"],
            last_modified=row["last_modified"],
            content_type=row["content_type"],
            size_bytes=(
                int(row["size_bytes"]) if row["size_bytes"] is not None else None
            ),
            sidecar_object_key=row["sidecar_object_key"],
            sidecar_etag=row["sidecar_etag"],
            sidecar_version_id=row["sidecar_version_id"],
            sidecar_last_modified=row["sidecar_last_modified"],
            sidecar_content_type=row["sidecar_content_type"],
            sidecar_size_bytes=(
                int(row["sidecar_size_bytes"])
                if row["sidecar_size_bytes"] is not None
                else None
            ),
        )

    tracks: list[TrackRecord] = []
    for row in connection.execute(
        """
        SELECT
            track_id,
            root_position,
            path,
            file_created_at,
            file_modified_at_ns,
            file_size_bytes,
            sidecar_artwork_path,
            sidecar_artwork_modified_at_ns,
            sidecar_artwork_size_bytes,
            file_type,
            scan_error,
            artist,
            album_artist,
            composer,
            album,
            title,
            work,
            grouping,
            movement_name,
            is_compilation,
            track_number,
            disc_number,
            date,
            duration_seconds,
            bitrate
        FROM library_tracks
        ORDER BY track_id
        """
    ):
        track_id = int(row["track_id"])
        genres, styles = split_genres_and_styles(
            normalize_genre_values(genres_by_track.get(track_id, [])),
            normalize_genre_values(styles_by_track.get(track_id, [])),
            taxonomy_genres=taxonomy_genres,
            taxonomy_styles=taxonomy_styles,
        )
        tracks.append(
            TrackRecord(
                path=str(row["path"]),
                track_id=track_id,
                root_position=(
                    int(row["root_position"])
                    if row["root_position"] is not None
                    else None
                ),
                file_created_at=row["file_created_at"],
                file_modified_at_ns=row["file_modified_at_ns"],
                file_size_bytes=row["file_size_bytes"],
                sidecar_artwork_path=row["sidecar_artwork_path"],
                sidecar_artwork_modified_at_ns=row["sidecar_artwork_modified_at_ns"],
                sidecar_artwork_size_bytes=row["sidecar_artwork_size_bytes"],
                file_type=row["file_type"],
                scan_error=row["scan_error"],
                artist=row["artist"],
                album_artist=row["album_artist"],
                composer=row["composer"],
                album=row["album"],
                title=row["title"],
                work=row["work"],
                grouping=row["grouping"],
                movement_name=row["movement_name"],
                is_compilation=bool(row["is_compilation"]),
                track_number=row["track_number"],
                disc_number=row["disc_number"],
                date=row["date"],
                genres=genres,
                styles=styles,
                duration_seconds=row["duration_seconds"],
                bitrate=row["bitrate"],
                source=sources_by_track.get(track_id),
            )
        )
    return tracks


def split_genres_and_styles(
    genres: list[str],
    styles: list[str],
    *,
    taxonomy_genres: set[str],
    taxonomy_styles: set[str],
) -> tuple[list[str], list[str]]:
    if styles:
        if not genres:
            return [UNKNOWN_GENRE_TAG], styles
        return genres, styles

    split_genres: list[str] = []
    split_styles: list[str] = []
    for value in genres:
        key = value.casefold()
        if key in taxonomy_styles and key not in taxonomy_genres:
            split_styles.append(value)
            continue
        split_genres.append(value)
    if split_styles and not split_genres:
        split_genres.append(UNKNOWN_GENRE_TAG)
    return normalize_genre_values(split_genres), normalize_genre_values(split_styles)


def store_track_artwork_by_path(
    source: Path,
    path: str,
    artwork: TrackArtwork,
    *,
    height_px: int = TRACK_ARTWORK_HEIGHT,
) -> None:
    if not artwork.data:
        return

    with connect_existing_database(source) as connection:
        row = connection.execute(
            "SELECT track_id FROM library_tracks WHERE path = ?",
            (path,),
        ).fetchone()
        if row is None:
            return
        track_id = int(row["track_id"])
        connection.execute(
            """
            INSERT OR REPLACE INTO library_track_artwork (
                track_id,
                height_px,
                mime_type,
                data
            )
            VALUES (?, ?, ?, ?)
            """,
            (track_id, height_px, artwork.mime_type, artwork.data),
        )
        album_row = connection.execute(
            "SELECT album_id FROM library_tracks WHERE track_id = ?",
            (track_id,),
        ).fetchone()
        if album_row is not None and album_row["album_id"]:
            rebuild_album_rollups(connection, [str(album_row["album_id"])])
        connection.commit()


def batched(values: list[str], *, size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def track_album_id(
    track: TrackRecord,
    album_ids_by_key: dict[tuple[str, str, str], str] | None = None,
) -> str | None:
    artist = track_raw_album_artist_id_text(track)
    album = track.album
    if not artist or not album:
        return None
    key = album_lookup_key(artist, album, track.musicbrainz_release_variant)
    if not key[0] or not key[1]:
        return None
    if album_ids_by_key is None:
        return local_album_id(
            artist,
            album,
            release_variant=track.musicbrainz_release_variant,
        )
    return album_ids_by_key.get(key)


def album_lookup_key(
    artist: str,
    album: str,
    release_variant: str | None,
) -> tuple[str, str, str]:
    return (
        normalize_text(artist),
        normalize_text(album),
        normalize_release_variant(release_variant) or "",
    )


def load_taxonomy_genre_matcher(source: Path) -> TaxonomyGenreMatcher:
    with connect_existing_database(source) as connection:
        return load_taxonomy_genre_matcher_from_connection(connection)


def load_taxonomy_genre_matcher_from_connection(
    connection: sqlite3.Connection,
) -> TaxonomyGenreMatcher:
    genres = [
        str(row["genre"])
        for row in connection.execute(
            "SELECT genre FROM taxonomy_genres ORDER BY lower(genre)"
        )
    ]
    styles = [
        str(row["style"])
        for row in connection.execute(
            "SELECT style FROM taxonomy_styles ORDER BY lower(style)"
        )
    ]
    alias_rows = [
        (str(row["alias"]), str(row["canonical_kind"]), str(row["canonical"]))
        for row in connection.execute(
            """
            SELECT alias, canonical_kind, canonical
            FROM taxonomy_aliases
            ORDER BY lower(alias), lower(canonical_kind)
            """
        )
    ]
    style_parents = {
        str(row["style"]): str(row["parent_genre"])
        for row in connection.execute(
            """
            SELECT style, parent_genre
            FROM taxonomy_styles
            ORDER BY lower(style)
            """
        )
    }
    exact_styles = build_exact_lookup(styles)
    exact_genres = build_exact_lookup(genres)
    for alias, canonical_kind, canonical in alias_rows:
        lookup = exact_styles if canonical_kind == "style" else exact_genres
        for key in exact_lookup_keys(alias):
            lookup.setdefault(key, canonical)

    return TaxonomyGenreMatcher(
        exact_genres=exact_genres,
        exact_styles=exact_styles,
        style_parents=style_parents,
        candidates=[
            *[build_term_candidate(name, kind="genre") for name in genres],
            *[build_term_candidate(name, kind="style") for name in styles],
        ],
    )


def build_exact_lookup(values: list[str]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for value in values:
        for key in exact_lookup_keys(value):
            lookup.setdefault(key, value)
    return lookup


def exact_lookup_keys(value: str) -> list[str]:
    normalized = normalize_text(value)
    compact = normalized.replace(" ", "")
    keys = [value.strip().casefold(), normalized, compact]
    seen: dict[str, str] = {}
    for key in keys:
        if key:
            seen.setdefault(key, key)
    return list(seen.values())


def build_term_candidate(value: str, *, kind: str) -> TaxonomyTermCandidate:
    normalized = normalize_text(value)
    expanded = expand_fuzzy_tokens(normalized)
    return TaxonomyTermCandidate(
        name=value,
        kind=kind,
        normalized=normalized,
        expanded=expanded,
        compact=expanded.replace(" ", ""),
        tokens=frozenset(expanded.split()),
    )


def expand_fuzzy_tokens(value: str) -> str:
    expanded: list[str] = []
    for token in value.split():
        replacement = FUZZY_TOKEN_EXPANSIONS.get(token, token)
        expanded.extend(replacement.split())
    return " ".join(expanded)


def fuzzy_term_score(query: TaxonomyTermCandidate, candidate: TaxonomyTermCandidate) -> float:
    if not query.expanded or not candidate.expanded:
        return 0.0
    if query.expanded == candidate.expanded or query.compact == candidate.compact:
        return 1.0

    token_score = 0.0
    if query.tokens or candidate.tokens:
        shared = len(query.tokens & candidate.tokens)
        total = len(query.tokens | candidate.tokens)
        token_score = (shared / total) if total else 0.0

    expanded_score = SequenceMatcher(None, query.expanded, candidate.expanded).ratio()
    compact_score = SequenceMatcher(None, query.compact, candidate.compact).ratio()
    return max(token_score, expanded_score, compact_score)
