from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

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
    get_metadata,
    library_root_position_for_path,
    rebuild_album_rollups,
    rebuild_album_search_index,
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
)
from ..album_artists import (
    DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
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
from .listening import track_play_fingerprint
from ..models import (
    ALBUM_ARTWORK_HEIGHT,
    TRACK_ARTWORK_HEIGHT,
    MusicLibrary,
    PlaylistItemRecord,
    PlaylistRecord,
    TrackArtwork,
    TrackRecord,
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
from ..scanner import thumbnail_artworks
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


def save_library_with_options(
    library: MusicLibrary,
    destination: Path,
    *,
    connection: sqlite3.Connection | None = None,
    root_rows: Iterable[tuple[int, str]] | None = None,
    album_artist_split_patterns: Iterable[str | None] = DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
) -> None:
    library_roots = (
        [(int(position), str(root_path)) for position, root_path in root_rows]
        if root_rows is not None
        else [(position, root) for position, root in enumerate(library.roots)]
    )
    valid_root_positions = {position for position, _root in library_roots}
    root_positions_by_index = [position for position, _root in library_roots]
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
        copy_album_musicbrainz_links_from_legacy_album_ids(connection, albums)
        store_scanned_album_musicbrainz_links(connection, albums)
        starred_at_by_album_id = album_starred_at_by_album_id(connection)
        added_at_by_album_id = album_added_at_by_album_id(connection)
        new_album_added_at = utc_now_iso()
        album_ids_by_key = {
            album_lookup_key(
                album.artist_id_text,
                album.album,
                album.release_variant,
            ): album.album_id
            for album in albums
        }
        existing_track_ids_by_path = {
            str(row["path"]): int(row["track_id"])
            for row in connection.execute(
                "SELECT path, track_id FROM library_tracks"
            )
        }
        existing_playlist_ids_by_path = playlist_ids_by_path(connection)
        existing_playlist_item_ids = playlist_item_ids_by_playlist_path(connection)
        for track in library.tracks:
            if track.track_id is None:
                track.track_id = existing_track_ids_by_path.get(track.path)

        clear_library(connection)

        for position, root_path in library_roots:
            connection.execute(
                "INSERT INTO library_roots (position, root_path) VALUES (?, ?)",
                (position, root_path),
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
                    added_at_by_album_id.get(album.album_id, new_album_added_at),
                    starred_at_by_album_id.get(album.album_id),
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
                root_position = library_root_position_for_path(track.path, library_roots)
            params = (
                album_id,
                root_position,
                track.path,
                track.file_created_at,
                track.file_type,
                track.scan_error,
                track.artist,
                track.album_artist,
                track.composer,
                track.album,
                track.title,
                track_play_fingerprint(
                    album_id=album_id or "",
                    disc_number=track.disc_number,
                    track_number=track.track_number,
                    title=track.title or "",
                    path=track.path,
                ),
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
                        file_type,
                        scan_error,
                        artist,
                        album_artist,
                        composer,
                        album,
                        title,
                        play_fingerprint,
                        work,
                        grouping,
                        movement_name,
                        is_compilation,
                        track_number,
                        disc_number,
                        date,
                        duration_seconds,
                        bitrate
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        file_type,
                        scan_error,
                        artist,
                        album_artist,
                        composer,
                        album,
                        title,
                        play_fingerprint,
                        work,
                        grouping,
                        movement_name,
                        is_compilation,
                        track_number,
                        disc_number,
                        date,
                        duration_seconds,
                        bitrate
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (track.track_id, *params),
                )
                track_id = int(track.track_id)
            track_ids_by_path[track.path] = track_id
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

        playlist_item_occurrences: dict[tuple[str, str], int] = defaultdict(int)
        for playlist in library.playlists:
            root_position = playlist.root_position
            if (
                root_rows is not None
                and root_position is not None
                and 0 <= root_position < len(root_positions_by_index)
            ):
                root_position = root_positions_by_index[root_position]
            elif root_position not in valid_root_positions:
                root_position = library_root_position_for_path(
                    playlist.path,
                    library_roots,
                )
            existing_playlist_id = existing_playlist_ids_by_path.get(playlist.path)
            cursor = connection.execute(
                """
                INSERT INTO library_playlists (
                    playlist_id,
                    root_position,
                    path,
                    name,
                    cover_svg,
                    file_created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    existing_playlist_id,
                    root_position,
                    playlist.path,
                    playlist.name,
                    playlist.cover_svg or playlist_cover_svg(playlist.name),
                    playlist.file_created_at,
                ),
            )
            playlist_id = (
                int(existing_playlist_id)
                if existing_playlist_id is not None
                else int(cursor.lastrowid)
            )
            playlist.playlist_id = playlist_id
            for position, item in enumerate(playlist.items):
                track_id = item.track_id or track_ids_by_path.get(item.path)
                is_tracked = track_id is not None
                occurrence_key = (playlist.path, item.path)
                occurrence = playlist_item_occurrences[occurrence_key]
                playlist_item_occurrences[occurrence_key] += 1
                existing_playlist_item_id = existing_playlist_item_ids.get(
                    (playlist.path, item.path, occurrence)
                )
                connection.execute(
                    """
                    INSERT INTO library_playlist_items (
                        playlist_item_id,
                        playlist_id,
                        position,
                        path,
                        track_id,
                        title,
                        duration_seconds,
                        duration_is_indeterminate,
                        genre,
                        cover_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        existing_playlist_item_id,
                        playlist_id,
                        position,
                        item.path,
                        track_id,
                        None if is_tracked else item.title or item.path,
                        (
                            None
                            if is_tracked or item.duration_is_indeterminate
                            else item.duration_seconds
                        ),
                        0 if is_tracked else 1 if item.duration_is_indeterminate else 0,
                        None if is_tracked else item.genre,
                        None if is_tracked else item.cover_url,
                    ),
                )

        set_metadata(connection, "library_generated_at", library.generated_at)
        set_metadata(
            connection,
            "library_supported_extensions_json",
            json.dumps(library.supported_extensions),
        )
        rebuild_album_rollups(connection)
        rebuild_root_scan_stats(connection)
        rebuild_album_search_index(connection)
        if owns_connection:
            connection.commit()
    finally:
        if owns_connection:
            connection.close()


def playlist_ids_by_path(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        str(row["path"]): int(row["playlist_id"])
        for row in connection.execute(
            """
            SELECT path, playlist_id
            FROM library_playlists
            """
        )
    }


def album_starred_at_by_album_id(connection: sqlite3.Connection) -> dict[str, str]:
    return {
        str(row["album_id"]): str(row["starred_at"])
        for row in connection.execute(
            """
            SELECT album_id, starred_at
            FROM library_albums
            WHERE starred_at IS NOT NULL
            """
        )
    }


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


def playlist_item_ids_by_playlist_path(
    connection: sqlite3.Connection,
) -> dict[tuple[str, str, int], int]:
    occurrences: dict[tuple[str, str], int] = defaultdict(int)
    item_ids: dict[tuple[str, str, int], int] = {}
    for row in connection.execute(
        """
        SELECT
            playlists.path AS playlist_path,
            items.path AS item_path,
            items.playlist_item_id
        FROM library_playlist_items AS items
        JOIN library_playlists AS playlists
            ON playlists.playlist_id = items.playlist_id
        ORDER BY playlists.path, items.position, items.playlist_item_id
        """
    ):
        occurrence_key = (str(row["playlist_path"]), str(row["item_path"]))
        occurrence = occurrences[occurrence_key]
        occurrences[occurrence_key] += 1
        item_ids[
            (
                str(row["playlist_path"]),
                str(row["item_path"]),
                occurrence,
            )
        ] = int(row["playlist_item_id"])
    return item_ids


def copy_album_musicbrainz_links_from_legacy_album_ids(
    connection: sqlite3.Connection,
    albums: Iterable[LocalAlbum],
) -> None:
    for album in albums:
        for legacy_album_id in legacy_album_musicbrainz_album_ids(album):
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


def legacy_album_musicbrainz_album_ids(album: LocalAlbum) -> tuple[str, ...]:
    artists = tuple(artist for artist in album.artists if artist)
    album_slug = normalize_slug_text(album.album)
    if not album_slug:
        return ()

    legacy_ids: list[str] = []
    seen: set[str] = set()
    if album.release_variant:
        seen.add(album.album_id)
        legacy_ids.append(album.album_id)

    if len(artists) < 2:
        return tuple(legacy_ids)

    for artist_text in legacy_album_artist_id_texts(artists):
        album_id = local_album_id(artist_text, album.album)
        if not album_id.split("::", 1)[0]:
            continue
        if album_id == album.album_id or album_id in seen:
            continue
        seen.add(album_id)
        legacy_ids.append(album_id)
    return tuple(legacy_ids)


def legacy_album_artist_id_texts(artists: tuple[str, ...]) -> tuple[str, ...]:
    joined = (" and ".join(artists), " with ".join(artists))
    return tuple(dict.fromkeys(joined))


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
        if base_album_id != link.file_album_id:
            continue

        if not track.musicbrainz_release_mbid and link.release_mbid:
            track.musicbrainz_release_mbid = link.release_mbid
        if not track.musicbrainz_release_group_mbid and link.release_group_mbid:
            track.musicbrainz_release_group_mbid = link.release_group_mbid
        if track.musicbrainz_release_mbid:
            release_tracks = tracks_by_release[track.musicbrainz_release_mbid]
            if not any(existing is track for existing in release_tracks):
                release_tracks.append(track)


def apply_musicbrainz_link_variants_to_tracks(
    connection: sqlite3.Connection,
    tracks: Iterable[TrackRecord],
    tracks_by_release: dict[str, list[TrackRecord]],
) -> None:
    tracks_by_base_album_id: dict[str, list[TrackRecord]] = defaultdict(list)
    for track in tracks:
        if normalize_release_variant(track.musicbrainz_release_variant):
            continue
        base_album_id = track_base_album_id(track)
        if base_album_id:
            tracks_by_base_album_id[base_album_id].append(track)

    if not tracks_by_base_album_id:
        return

    musicbrainz_links = load_album_musicbrainz_links(connection)
    for base_album_id, album_tracks in tracks_by_base_album_id.items():
        candidates: dict[tuple[str | None, str | None], tuple[str | None, str | None, str | None]] = {}
        for link_group in musicbrainz_links.values():
            for link in link_group:
                release_variant = release_variant_from_link_album_id(
                    link.file_album_id,
                    base_album_id,
                )
                if link.file_album_id != base_album_id and release_variant is None:
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


def track_base_album_id(track: TrackRecord) -> str | None:
    artist = "\n".join(track_album_artist_values(track))
    album = track.album
    if not artist or not album:
        return None
    if not normalize_text(artist) or not normalize_text(album):
        return None
    return local_album_id(artist, album)


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
    base_album_id = track_base_album_id(track)
    if not base_album_id:
        return None

    candidates = list(musicbrainz_links.get(base_album_id, ()))
    release_variant = normalize_release_variant(track.musicbrainz_release_variant)
    if release_variant:
        candidates.extend(musicbrainz_links.get(f"{base_album_id}::{release_variant}", ()))

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
        connection = connect_database(source, create=False)
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
        copy_album_musicbrainz_links_from_legacy_album_ids(
            connection,
            albums,
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
        connection = connect_database(source, create=False)
    try:
        apply_album_artist_mappings(
            connection,
            library.tracks,
            split_patterns=album_artist_split_patterns,
        )
        apply_musicbrainz_release_variants(connection, library.tracks, log=log)
        albums = group_library_albums(library)
        copy_album_musicbrainz_links_from_legacy_album_ids(
            connection,
            albums,
        )
        store_scanned_album_musicbrainz_links(
            connection,
            albums,
        )
        musicbrainz_links = load_album_musicbrainz_links(connection)
        caa_client = CoverArtArchiveClient(stats=caa_stats, log=log)
        itunes_client = ItunesLookupClient(stats=itunes_stats, log=log)

        for tracks in genre_resolution_groups(library.tracks):
            musicbrainz_link = musicbrainz_link_for_track(musicbrainz_links, tracks[0])
            chosen_artwork = itunes_artwork_for_album(
                connection,
                tracks,
                itunes_client,
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
    stats.metadata_api_calls = caa_stats.metadata_api_calls
    stats.metadata_cached_calls = caa_stats.metadata_cached_calls
    stats.image_downloads = caa_stats.image_downloads
    stats.image_cached_calls = caa_stats.image_cached_calls
    stats.fetch_failures = itunes_stats.fetch_failures + caa_stats.fetch_failures
    stats.missing_art = itunes_stats.missing_art + caa_stats.missing_art
    return stats


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

def load_library(source: Path, *, include_artwork: bool = False) -> MusicLibrary:
    with connect_database(source, create=False) as connection:
        roots = [
            str(row["root_path"])
            for row in connection.execute(
                "SELECT root_path FROM library_roots ORDER BY position"
            )
        ]
        generated_at = get_metadata(connection, "library_generated_at")
        supported_extensions = json.loads(
            get_metadata(connection, "library_supported_extensions_json", "[]")
        )

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

        album_artists_by_album: dict[str, tuple[str, ...]] = {}
        rows_by_album: dict[str, list[str]] = defaultdict(list)
        for row in connection.execute(
            """
            SELECT album_id, artist
            FROM library_album_artists
            ORDER BY album_id, position
            """
        ):
            rows_by_album[str(row["album_id"])].append(str(row["artist"]))
        album_artists_by_album = {
            album_id: tuple(artists)
            for album_id, artists in rows_by_album.items()
        }

        taxonomy_genres = {
            str(row["genre"]).casefold()
            for row in connection.execute("SELECT genre FROM taxonomy_genres")
        }
        taxonomy_styles = {
            str(row["style"]).casefold()
            for row in connection.execute("SELECT style FROM taxonomy_styles")
        }
        track_ids_with_cover = {
            int(row["track_id"])
            for row in connection.execute(
                "SELECT DISTINCT track_id FROM library_track_artwork"
            )
        }
        album_ids_with_cover = {
            str(row["album_id"])
            for row in connection.execute(
                """
                SELECT DISTINCT library_tracks.album_id
                FROM library_tracks
                JOIN library_track_artwork
                    ON library_track_artwork.track_id = library_tracks.track_id
                WHERE library_tracks.album_id IS NOT NULL
                    AND library_tracks.album_id != ''
                """
            )
        }
        artwork_by_track: dict[tuple[int, int], TrackArtwork] = {}
        if include_artwork:
            for row in connection.execute(
                """
                SELECT track_id, height_px, mime_type, data
                FROM library_track_artwork
                """
            ):
                artwork_by_track[(int(row["track_id"]), int(row["height_px"]))] = TrackArtwork(
                    mime_type=str(row["mime_type"]),
                    data=bytes(row["data"]),
                )

        tracks: list[TrackRecord] = []
        for row in connection.execute(
            """
            SELECT
                track_id,
                album_id,
                root_position,
                path,
                file_created_at,
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
            album_id = str(row["album_id"]) if row["album_id"] else None
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
                    file_type=row["file_type"],
                    scan_error=row["scan_error"],
                    artist=row["artist"],
                    album_artist=row["album_artist"],
                    album_artists=album_artists_by_album.get(album_id, ()),
                    composer=row["composer"],
                    album=row["album"],
                    title=row["title"],
                    work=row["work"],
                    grouping=row["grouping"],
                    movement_name=row["movement_name"],
                    has_cover=(
                        track_id in track_ids_with_cover
                        or bool(album_id and album_id in album_ids_with_cover)
                    ),
                    is_compilation=bool(row["is_compilation"]),
                    track_number=row["track_number"],
                    disc_number=row["disc_number"],
                    date=row["date"],
                    genres=genres,
                    styles=styles,
                    artwork=artwork_by_track.get((track_id, TRACK_ARTWORK_HEIGHT)),
                    album_artwork=artwork_by_track.get((track_id, ALBUM_ARTWORK_HEIGHT)),
                    duration_seconds=row["duration_seconds"],
                    bitrate=row["bitrate"],
                )
            )

        playlist_items_by_playlist: dict[int, list[PlaylistItemRecord]] = defaultdict(list)
        for row in connection.execute(
            """
            SELECT
                playlist_id,
                track_id,
                path,
                title,
                duration_seconds,
                duration_is_indeterminate,
                genre,
                cover_url
            FROM library_playlist_items
            ORDER BY playlist_id, position
            """
        ):
            playlist_items_by_playlist[int(row["playlist_id"])].append(
                PlaylistItemRecord(
                    path=str(row["path"]),
                    track_id=int(row["track_id"]) if row["track_id"] is not None else None,
                    title=row["title"],
                    duration_seconds=row["duration_seconds"],
                    duration_is_indeterminate=bool(row["duration_is_indeterminate"]),
                    genre=row["genre"],
                    cover_url=row["cover_url"],
                )
            )

        playlists = [
            PlaylistRecord(
                playlist_id=int(row["playlist_id"]),
                root_position=(
                    int(row["root_position"])
                    if row["root_position"] is not None
                    else None
                ),
                path=str(row["path"]),
                name=str(row["name"]),
                file_created_at=row["file_created_at"],
                cover_svg=str(row["cover_svg"] or ""),
                items=playlist_items_by_playlist.get(int(row["playlist_id"]), []),
            )
            for row in connection.execute(
                """
                SELECT playlist_id, root_position, path, name, cover_svg, file_created_at
                FROM library_playlists
                ORDER BY playlist_id
                """
            )
        ]

    return MusicLibrary(
        roots=roots,
        tracks=tracks,
        supported_extensions=list(supported_extensions),
        generated_at=generated_at,
        playlists=playlists,
    )


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

    with connect_database(source, create=False) as connection:
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
    artist = "\n".join(track_album_artist_values(track))
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
    with connect_database(source, create=False) as connection:
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
