from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import logging
from pathlib import Path
import sqlite3
from time import perf_counter

from ..queries import LibraryQueries, LibraryRootFilterOption, library_root_filter_label
from ..database import (
    canonicalize_library_album_artists,
    connect_database,
    rebuild_album_rollups,
    rebuild_album_search_index,
    rebuild_root_scan_stats,
)
from ...discogs import (
    group_library_albums,
    most_common_artist_values,
    most_common_value,
    most_common_year,
    parse_year,
)
from ...album_artists import (
    DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
    display_album_artists,
)
from ..library import (
    AlbumArtistMappingResolver,
    CoverArtResolutionStats,
    GenreResolutionStats,
    resolve_library_cover_art,
    resolve_library_genres,
    save_library_with_options,
)
from ...player_errors import PlayerNotFoundError
from ...player_runtime import PlayerJobCancelToken, PlayerJobResult, PlayerRuntime
from ...scanner import build_library

LOGGER = logging.getLogger("kukicha.player")

def create_library_root(database: Path, root_path: str) -> LibraryRootFilterOption:
    root = prepare_library_root(database, root_path)
    connection = connect_database(database)
    try:
        connection.execute(
            "INSERT INTO library_roots (position, root_path) VALUES (?, ?)",
            (root.position, root.path),
        )
        rebuild_root_scan_stats(connection)
        connection.commit()
    finally:
        connection.close()
    return root


def prepare_library_root(database: Path, root_path: str) -> LibraryRootFilterOption:
    stripped = root_path.strip()
    if not stripped:
        raise ValueError("root path is required")

    resolved = Path(stripped).expanduser().resolve(strict=False)
    if not resolved.exists():
        raise ValueError(f"directory does not exist: {resolved}")
    if not resolved.is_dir():
        raise ValueError(f"path is not a directory: {resolved}")

    resolved_path = str(resolved)
    connection = connect_database(database)
    try:
        existing = connection.execute(
            "SELECT position FROM library_roots WHERE root_path = ?",
            (resolved_path,),
        ).fetchone()
        if existing is not None:
            raise ValueError(f"root already exists: {resolved_path}")
        position = int(
            connection.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 AS position FROM library_roots"
            ).fetchone()["position"]
        )
    finally:
        connection.close()

    return LibraryRootFilterOption(
        position=position,
        path=resolved_path,
        label=library_root_filter_label(resolved_path),
    )


def library_root_count(database: Path) -> int:
    connection = connect_database(database)
    try:
        return int(connection.execute("SELECT COUNT(*) AS count FROM library_roots").fetchone()["count"])
    finally:
        connection.close()


def library_root_by_position(database: Path, position: int) -> LibraryRootFilterOption:
    connection = connect_database(database, create=False)
    try:
        row = connection.execute(
            "SELECT position, root_path FROM library_roots WHERE position = ?",
            (position,),
        ).fetchone()
    finally:
        connection.close()

    if row is None:
        raise PlayerNotFoundError(f"root does not exist: {position}")

    return LibraryRootFilterOption(
        position=int(row["position"]),
        path=str(row["root_path"]),
        label=library_root_filter_label(str(row["root_path"])),
    )


def root_display_label(root: LibraryRootFilterOption) -> str:
    return root.label or library_root_filter_label(root.path) or root.path


def library_job_summary_text(
    job_label: str,
    root_path: str,
    *,
    tracks_scanned: int,
    albums_scanned: int,
    playlists_scanned: int | None = None,
    files_missing_required_tags: int,
    duration_seconds: float,
) -> str:
    scan_parts = f"tracks={tracks_scanned}, albums={albums_scanned}"
    if playlists_scanned is not None:
        scan_parts = f"{scan_parts}, playlists={playlists_scanned}"
    return (
        f"{job_label} completed for {root_path} "
        f"({scan_parts}, "
        f"missing_required_tags={files_missing_required_tags}, "
        f"duration={duration_seconds:.2f}s)"
    )


def library_job_detail_lines(
    *,
    tracks_scanned: int,
    albums_scanned: int,
    playlists_scanned: int | None = None,
    files_missing_required_tags: int,
    genre_resolution: GenreResolutionStats,
    cover_art_resolution: CoverArtResolutionStats,
) -> tuple[str, ...]:
    scan_lines = [
        f"tracks scanned: {tracks_scanned}",
        f"albums scanned: {albums_scanned}",
    ]
    if playlists_scanned is not None:
        scan_lines.append(f"playlists scanned: {playlists_scanned}")
    scan_lines.append(f"files missing required tags: {files_missing_required_tags}")
    return (
        *scan_lines,
        f"exact genre matches: {genre_resolution.exact_genre_matches}",
        f"exact style matches: {genre_resolution.exact_style_matches}",
        f"fuzzy genre matches: {genre_resolution.fuzzy_genre_matches}",
        f"fuzzy style matches: {genre_resolution.fuzzy_style_matches}",
        f"unmatched genre terms: {genre_resolution.unmatched}",
        f"albums set to __Unknown: {genre_resolution.unknown_albums}",
        f"tracks set to __Unknown: {genre_resolution.unknown_tracks}",
        f"musicbrainz api calls: {genre_resolution.musicbrainz_api_calls}",
        f"musicbrainz cached calls: {genre_resolution.musicbrainz_cached_calls}",
        f"musicbrainz rate-limit retries: {genre_resolution.musicbrainz_rate_limit_retries}",
        f"musicbrainz fetch failures: {genre_resolution.musicbrainz_fetch_failures}",
        f"musicbrainz album overrides: {genre_resolution.musicbrainz_album_overrides}",
        f"unmatched musicbrainz genres: {genre_resolution.musicbrainz_unmatched_genres}",
        f"itunes lookup api calls: {cover_art_resolution.itunes_lookup_api_calls}",
        f"itunes lookup cached calls: {cover_art_resolution.itunes_lookup_cached_calls}",
        f"cover art metadata api calls: {cover_art_resolution.metadata_api_calls}",
        f"cover art metadata cached calls: {cover_art_resolution.metadata_cached_calls}",
        f"cover art image downloads: {cover_art_resolution.image_downloads}",
        f"cover art image cached calls: {cover_art_resolution.image_cached_calls}",
        f"cover art fetch failures: {cover_art_resolution.fetch_failures}",
        f"cover art missing: {cover_art_resolution.missing_art}",
        f"cover art album overrides: {cover_art_resolution.album_cover_overrides}",
        f"cover art tracks updated: {cover_art_resolution.tracks_updated}",
    )


def library_scan_progress_text(job_label: str, message: str) -> str:
    return f"{job_label} progress: {message}"


def run_delete_root_job(
    runtime: PlayerRuntime,
    root: LibraryRootFilterOption,
    cancel_token: PlayerJobCancelToken,
) -> PlayerJobResult:
    started_at = perf_counter()
    root_label = root_display_label(root)
    delete_library_root(
        runtime.database,
        root.position,
        cancel_check=cancel_token.raise_if_canceled,
    )
    return PlayerJobResult(
        message=f"Delete completed for {root_label}.",
        context={
            "path": root.path,
            "root_position": root.position,
            "duration_seconds": perf_counter() - started_at,
        },
    )


@dataclass(frozen=True, slots=True)
class RootScanResult:
    root: LibraryRootFilterOption
    tracks_scanned: int
    albums_scanned: int
    playlists_scanned: int
    files_missing_required_tags: int
    genre_resolution: GenreResolutionStats
    cover_art_resolution: CoverArtResolutionStats


@dataclass(frozen=True, slots=True)
class LibraryRescanResult:
    roots_scanned: int
    tracks_scanned: int
    albums_scanned: int
    playlists_scanned: int
    files_missing_required_tags: int
    genre_resolution: GenreResolutionStats
    cover_art_resolution: CoverArtResolutionStats


def scan_library_with_new_root(
    database: Path,
    root_path: str,
    *,
    album_artist_split_patterns: Iterable[str | None] = DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
    cancel_check: Callable[[], None] | None = None,
) -> RootScanResult:
    root = prepare_library_root(database, root_path)
    existing_roots = tuple(LibraryQueries(database).library_roots())
    combined_root_rows = [*( (item.position, item.path) for item in existing_roots ), (root.position, root.path)]
    combined_root_paths = [Path(root_path) for _position, root_path in combined_root_rows]
    missing_required_tag_count = 0

    def log_missing_required_tags(_track: object, _missing_fields: list[str]) -> None:
        nonlocal missing_required_tag_count
        missing_required_tag_count += 1

    def scan_progress(message: str) -> None:
        if cancel_check is not None:
            cancel_check()
        LOGGER.info("%s", library_scan_progress_text("add and scan", message))

    if cancel_check is not None:
        cancel_check()
    library = build_library(
        combined_root_paths,
        progress=scan_progress,
        progress_every=500,
        on_missing_required_tags=log_missing_required_tags,
    )
    if cancel_check is not None:
        cancel_check()
    connection = connect_database(database, create=False)
    try:
        connection.execute("SAVEPOINT add_root_scan")
        try:
            if cancel_check is not None:
                cancel_check()
            genre_resolution = resolve_library_genres(
                library,
                database,
                connection=connection,
                album_artist_split_patterns=album_artist_split_patterns,
            ) or GenreResolutionStats()
            cover_art_resolution = resolve_library_cover_art(
                library,
                database,
                connection=connection,
                album_artist_split_patterns=album_artist_split_patterns,
            ) or CoverArtResolutionStats()
            save_library_with_options(
                library,
                database,
                connection=connection,
                root_rows=combined_root_rows,
                album_artist_split_patterns=album_artist_split_patterns,
            )
            if cancel_check is not None:
                cancel_check()
            connection.execute("RELEASE SAVEPOINT add_root_scan")
            connection.commit()
        except Exception:
            connection.execute("ROLLBACK TO SAVEPOINT add_root_scan")
            connection.execute("RELEASE SAVEPOINT add_root_scan")
            connection.rollback()
            raise
    finally:
        connection.close()

    albums = group_library_albums(library)
    return RootScanResult(
        root=root,
        tracks_scanned=len(library.tracks),
        albums_scanned=len(albums),
        playlists_scanned=len(library.playlists),
        files_missing_required_tags=missing_required_tag_count,
        genre_resolution=genre_resolution,
        cover_art_resolution=cover_art_resolution,
    )


def run_add_root_job(
    runtime: PlayerRuntime,
    root: LibraryRootFilterOption,
    cancel_token: PlayerJobCancelToken,
) -> PlayerJobResult:
    started_at = perf_counter()
    root_label = root_display_label(root)
    result = scan_library_with_new_root(
        runtime.database,
        root.path,
        album_artist_split_patterns=runtime.album_artist_split_patterns,
        cancel_check=cancel_token.raise_if_canceled,
    )
    duration_seconds = perf_counter() - started_at
    LOGGER.info(
        "%s",
        library_job_summary_text(
            "add and scan",
            root.path,
            tracks_scanned=result.tracks_scanned,
            albums_scanned=result.albums_scanned,
            playlists_scanned=result.playlists_scanned,
            files_missing_required_tags=result.files_missing_required_tags,
            duration_seconds=duration_seconds,
        ),
    )
    for line in library_job_detail_lines(
        tracks_scanned=result.tracks_scanned,
        albums_scanned=result.albums_scanned,
        playlists_scanned=result.playlists_scanned,
        files_missing_required_tags=result.files_missing_required_tags,
        genre_resolution=result.genre_resolution,
        cover_art_resolution=result.cover_art_resolution,
    ):
        LOGGER.info("%s", line)
    return PlayerJobResult(
        message=f"Add and scan completed for {root_label}.",
        context={
            "path": root.path,
            "root_position": root.position,
            "tracks_scanned": result.tracks_scanned,
            "albums_scanned": result.albums_scanned,
            "playlists_scanned": result.playlists_scanned,
            "files_missing_required_tags": result.files_missing_required_tags,
            "duration_seconds": duration_seconds,
        },
    )


def rescan_library(
    database: Path,
    *,
    album_artist_split_patterns: Iterable[str | None] = DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
    cancel_check: Callable[[], None] | None = None,
) -> LibraryRescanResult:
    roots = tuple(LibraryQueries(database).library_roots())
    if not roots:
        raise ValueError("no roots configured")

    root_rows = [(root.position, root.path) for root in roots]
    missing_required_tag_count = 0

    def log_missing_required_tags(_track: object, _missing_fields: list[str]) -> None:
        nonlocal missing_required_tag_count
        missing_required_tag_count += 1

    def scan_progress(message: str) -> None:
        if cancel_check is not None:
            cancel_check()
        LOGGER.info("%s", library_scan_progress_text("rescan", message))

    if cancel_check is not None:
        cancel_check()
    library = build_library(
        [Path(root.path) for root in roots],
        progress=scan_progress,
        progress_every=500,
        on_missing_required_tags=log_missing_required_tags,
    )
    if cancel_check is not None:
        cancel_check()
    connection = connect_database(database, create=False)
    try:
        connection.execute("SAVEPOINT rescan_library")
        try:
            if cancel_check is not None:
                cancel_check()
            genre_resolution = resolve_library_genres(
                library,
                database,
                connection=connection,
                album_artist_split_patterns=album_artist_split_patterns,
            ) or GenreResolutionStats()
            cover_art_resolution = resolve_library_cover_art(
                library,
                database,
                connection=connection,
                album_artist_split_patterns=album_artist_split_patterns,
            ) or CoverArtResolutionStats()
            save_library_with_options(
                library,
                database,
                connection=connection,
                root_rows=root_rows,
                album_artist_split_patterns=album_artist_split_patterns,
            )
            if cancel_check is not None:
                cancel_check()
            connection.execute("RELEASE SAVEPOINT rescan_library")
            connection.commit()
        except Exception:
            connection.execute("ROLLBACK TO SAVEPOINT rescan_library")
            connection.execute("RELEASE SAVEPOINT rescan_library")
            connection.rollback()
            raise
    finally:
        connection.close()

    albums = group_library_albums(library)
    return LibraryRescanResult(
        roots_scanned=len(roots),
        tracks_scanned=len(library.tracks),
        albums_scanned=len(albums),
        playlists_scanned=len(library.playlists),
        files_missing_required_tags=missing_required_tag_count,
        genre_resolution=genre_resolution,
        cover_art_resolution=cover_art_resolution,
    )


def run_rescan_library_job(
    runtime: PlayerRuntime,
    cancel_token: PlayerJobCancelToken,
) -> PlayerJobResult:
    started_at = perf_counter()
    result = rescan_library(
        runtime.database,
        album_artist_split_patterns=runtime.album_artist_split_patterns,
        cancel_check=cancel_token.raise_if_canceled,
    )
    duration_seconds = perf_counter() - started_at
    LOGGER.info(
        "%s",
        library_job_summary_text(
            "rescan",
            "library",
            tracks_scanned=result.tracks_scanned,
            albums_scanned=result.albums_scanned,
            playlists_scanned=result.playlists_scanned,
            files_missing_required_tags=result.files_missing_required_tags,
            duration_seconds=duration_seconds,
        ),
    )
    for line in library_job_detail_lines(
        tracks_scanned=result.tracks_scanned,
        albums_scanned=result.albums_scanned,
        playlists_scanned=result.playlists_scanned,
        files_missing_required_tags=result.files_missing_required_tags,
        genre_resolution=result.genre_resolution,
        cover_art_resolution=result.cover_art_resolution,
    ):
        LOGGER.info("%s", line)
    return PlayerJobResult(
        message="Rescan completed.",
        context={
            "roots_scanned": result.roots_scanned,
            "tracks_scanned": result.tracks_scanned,
            "albums_scanned": result.albums_scanned,
            "playlists_scanned": result.playlists_scanned,
            "files_missing_required_tags": result.files_missing_required_tags,
            "duration_seconds": duration_seconds,
        },
    )


def delete_library_root(
    database: Path,
    position: int,
    *,
    cancel_check: Callable[[], None] | None = None,
) -> LibraryRootFilterOption:
    connection = connect_database(database, create=False)
    try:
        if cancel_check is not None:
            cancel_check()
        connection.execute("SAVEPOINT delete_root")
        try:
            row = connection.execute(
                "SELECT position, root_path FROM library_roots WHERE position = ?",
                (position,),
            ).fetchone()
            if row is None:
                raise ValueError(f"root does not exist: {position}")

            root = LibraryRootFilterOption(
                position=int(row["position"]),
                path=str(row["root_path"]),
                label=library_root_filter_label(str(row["root_path"])),
            )
            affected_album_ids = [
                str(album_row["album_id"])
                for album_row in connection.execute(
                    """
                    SELECT DISTINCT album_id
                    FROM library_tracks
                    WHERE root_position = ?
                        AND album_id IS NOT NULL
                        AND album_id != ''
                    ORDER BY album_id
                    """,
                    (position,),
                )
            ]

            connection.execute("DELETE FROM library_playlists WHERE root_position = ?", (position,))
            connection.execute("DELETE FROM library_tracks WHERE root_position = ?", (position,))
            connection.execute("DELETE FROM library_roots WHERE position = ?", (position,))
            reconcile_deleted_root_albums(connection, affected_album_ids)
            rebuild_root_scan_stats(connection)
            rebuild_album_search_index(connection)
            if cancel_check is not None:
                cancel_check()
            connection.execute("RELEASE SAVEPOINT delete_root")
            connection.commit()
            return root
        except Exception:
            connection.execute("ROLLBACK TO SAVEPOINT delete_root")
            connection.execute("RELEASE SAVEPOINT delete_root")
            connection.rollback()
            raise
    finally:
        connection.close()


def reconcile_deleted_root_albums(
    connection: sqlite3.Connection,
    affected_album_ids: list[str],
) -> None:
    reconcile_library_albums(connection, affected_album_ids)


def reconcile_library_albums(
    connection: sqlite3.Connection,
    affected_album_ids: list[str],
    *,
    album_artist_split_patterns: Iterable[str | None] = DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
) -> None:
    if not affected_album_ids:
        return

    placeholders = ", ".join("?" for _ in affected_album_ids)
    surviving_rows = list(
        connection.execute(
            f"""
            SELECT
                album_id,
                album_artist,
                artist,
                album,
                date,
                file_created_at,
                path
            FROM library_tracks
            WHERE album_id IN ({placeholders})
            ORDER BY album_id, path COLLATE NOCASE, track_id
            """,
            affected_album_ids,
        )
    )
    surviving_album_ids = {str(row["album_id"]) for row in surviving_rows}
    deleted_album_ids = [
        album_id
        for album_id in affected_album_ids
        if album_id not in surviving_album_ids
    ]
    if deleted_album_ids:
        deleted_placeholders = ", ".join("?" for _ in deleted_album_ids)
        connection.execute(
            f"DELETE FROM library_albums WHERE album_id IN ({deleted_placeholders})",
            deleted_album_ids,
        )

    if not surviving_album_ids:
        canonicalize_library_album_artists(connection)
        rebuild_album_rollups(connection, affected_album_ids)
        return

    rows_by_album: dict[str, list[sqlite3.Row]] = {}
    for row in surviving_rows:
        rows_by_album.setdefault(str(row["album_id"]), []).append(row)

    album_artist_resolver = AlbumArtistMappingResolver(
        connection,
        album_artist_split_patterns,
    )
    for album_id, rows in rows_by_album.items():
        artists = most_common_artist_values(
            album_artist_resolver.resolve(
                str(row["album_artist"])
                if row["album_artist"]
                else str(row["artist"])
                if row["artist"]
                else None
            )
            for row in rows
        )
        artist = display_album_artists(artists) or "<unknown artist>"
        album = most_common_value(
            str(row["album"]) if row["album"] else None
            for row in rows
        ) or "<unknown album>"
        year = most_common_year(
            parse_year(str(row["date"])) if row["date"] else None
            for row in rows
        )
        file_created_at = min(
            (str(row["file_created_at"]) for row in rows if row["file_created_at"]),
            default=None,
        )
        connection.execute(
            """
            INSERT INTO library_albums (
                album_id,
                album,
                year,
                track_count,
                file_created_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(album_id) DO UPDATE SET
                album = excluded.album,
                year = excluded.year,
                track_count = excluded.track_count,
                file_created_at = excluded.file_created_at
            """,
            (album_id, album, year, len(rows), file_created_at),
        )
        connection.execute(
            "DELETE FROM library_album_artists WHERE album_id = ?",
            (album_id,),
        )
        for position, album_artist in enumerate(artists or (artist,)):
            connection.execute(
                """
                INSERT INTO library_album_artists (album_id, position, artist)
                VALUES (?, ?, ?)
                """,
                (album_id, position, album_artist),
            )
    canonicalize_library_album_artists(connection)
    rebuild_album_rollups(connection, affected_album_ids)


def root_payload(root: LibraryRootFilterOption) -> dict[str, object]:
    return {
        "position": root.position,
        "path": root.path,
        "label": root.label,
    }
