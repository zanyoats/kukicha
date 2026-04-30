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
)
from ..discogs import LocalAlbum, group_library_albums
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
    clean_mbid,
    get_musicbrainz_entity,
    load_album_musicbrainz_links,
    musicbrainz_genres,
    musicbrainz_release_group_mbid,
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
        albums = group_library_albums(library)
        copy_album_musicbrainz_links_from_legacy_album_ids(connection, albums)
        album_ids_by_key = {
            (normalize_text(album.artist_id_text), normalize_text(album.album)): album.album_id
            for album in albums
        }
        existing_track_ids_by_path = {
            str(row["path"]): int(row["track_id"])
            for row in connection.execute(
                "SELECT path, track_id FROM library_tracks"
            )
        }
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
                    file_created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    album.album_id,
                    album.album,
                    album.year,
                    album.track_count,
                    album.file_created_at,
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
                        work,
                        grouping,
                        movement_name,
                        is_compilation,
                        track_number,
                        disc_number,
                        date,
                        duration_seconds,
                        bitrate
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            cursor = connection.execute(
                """
                INSERT INTO library_playlists (
                    root_position,
                    path,
                    name,
                    cover_svg,
                    file_created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    root_position,
                    playlist.path,
                    playlist.name,
                    playlist.cover_svg or playlist_cover_svg(playlist.name),
                    playlist.file_created_at,
                ),
            )
            playlist_id = int(cursor.lastrowid)
            playlist.playlist_id = playlist_id
            for position, item in enumerate(playlist.items):
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
                        genre,
                        cover_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        playlist_id,
                        position,
                        item.path,
                        track_id,
                        None if is_tracked else item.title or item.path,
                        None if is_tracked else item.duration_seconds,
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
                WHERE album_id = ?
                """,
                (legacy_album_id,),
            ).fetchone()
            if row is None:
                continue

            release_mbid = clean_mbid(row["release_mbid"])
            release_group_mbid = clean_mbid(row["release_group_mbid"])
            if not release_mbid and not release_group_mbid:
                continue

            connection.execute(
                """
                INSERT INTO album_musicbrainz_links (
                    album_id,
                    release_mbid,
                    release_group_mbid
                ) VALUES (?, ?, ?)
                ON CONFLICT(album_id) DO UPDATE SET
                    release_mbid = CASE
                        WHEN COALESCE(album_musicbrainz_links.release_mbid, '') = ''
                        THEN excluded.release_mbid
                        ELSE album_musicbrainz_links.release_mbid
                    END,
                    release_group_mbid = CASE
                        WHEN COALESCE(album_musicbrainz_links.release_group_mbid, '') = ''
                        THEN excluded.release_group_mbid
                        ELSE album_musicbrainz_links.release_group_mbid
                    END
                """,
                (album.album_id, release_mbid, release_group_mbid),
            )


def legacy_album_musicbrainz_album_ids(album: LocalAlbum) -> tuple[str, ...]:
    artists = tuple(artist for artist in album.artists if artist)
    if len(artists) < 2:
        return ()

    album_slug = normalize_slug_text(album.album)
    if not album_slug:
        return ()

    legacy_ids: list[str] = []
    seen: set[str] = set()
    for artist_text in legacy_album_artist_id_texts(artists):
        artist_slug = normalize_slug_text(artist_text)
        if not artist_slug:
            continue
        album_id = f"{artist_slug}::{album_slug}"
        if album_id == album.album_id or album_id in seen:
            continue
        seen.add(album_id)
        legacy_ids.append(album_id)
    return tuple(legacy_ids)


def legacy_album_artist_id_texts(artists: tuple[str, ...]) -> tuple[str, ...]:
    joined = (" and ".join(artists), " with ".join(artists))
    return tuple(dict.fromkeys(joined))


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
        copy_album_musicbrainz_links_from_legacy_album_ids(
            connection,
            group_library_albums(library),
        )
        musicbrainz_links = load_album_musicbrainz_links(connection)
        musicbrainz_client = MusicBrainzClient(stats=mb_lookup_stats, log=log)

        for tracks in genre_resolution_groups(library.tracks):
            album_id = track_album_id(tracks[0])
            musicbrainz_link = musicbrainz_links.get(album_id) if album_id else None
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
    has_genre_tags = False
    for track in tracks:
        for genre in normalize_genre_values(track.genres):
            has_genre_tags = True
            match = matcher.resolve(genre)
            update_genre_resolution_stats(stats, match.resolution)
            resolved_genres.extend(match.genres)
            resolved_styles.extend(match.styles)

    album_genres = normalize_genre_values(resolved_genres)
    album_styles = normalize_genre_values(resolved_styles)
    if album_genres:
        return ResolvedAlbumGenres(genres=album_genres, styles=album_styles)
    if has_genre_tags:
        return ResolvedAlbumGenres(genres=[UNKNOWN_GENRE_TAG], styles=album_styles)
    return ResolvedAlbumGenres(genres=[], styles=[])


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
        release_group_mbid,
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
        copy_album_musicbrainz_links_from_legacy_album_ids(
            connection,
            group_library_albums(library),
        )
        musicbrainz_links = load_album_musicbrainz_links(connection)
        caa_client = CoverArtArchiveClient(stats=caa_stats, log=log)
        itunes_client = ItunesLookupClient(stats=itunes_stats, log=log)

        for tracks in genre_resolution_groups(library.tracks):
            album_id = track_album_id(tracks[0])
            musicbrainz_link = musicbrainz_links.get(album_id) if album_id else None
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
    album_ids_by_key: dict[tuple[str, str], str] | None = None,
) -> str | None:
    artist = "\n".join(track_album_artist_values(track))
    album = track.album
    if not artist or not album:
        return None
    key = (normalize_text(artist), normalize_text(album))
    if not key[0] or not key[1]:
        return None
    if album_ids_by_key is None:
        return f"{normalize_slug_text(artist)}::{normalize_slug_text(album)}"
    return album_ids_by_key.get(key)


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
