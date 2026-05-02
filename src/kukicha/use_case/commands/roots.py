from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from pathlib import Path
import sqlite3
from time import perf_counter

from ..queries import LibraryQueries
from ..database import (
    canonicalize_library_album_artists,
    connect_database,
    rebuild_album_rollups,
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
from ...player_runtime import PlayerJobCancelToken, PlayerJobResult, PlayerRuntime
from ...models import MusicLibrary
from ...scanner import SUPPORTED_EXTENSIONS, build_library

LOGGER = logging.getLogger("kukicha.player")

def library_root_count(database: Path) -> int:
    connection = connect_database(database)
    try:
        return int(connection.execute("SELECT COUNT(*) AS count FROM library_roots").fetchone()["count"])
    finally:
        connection.close()


def library_job_summary_text(
    job_label: str,
    root_path: str,
    *,
    tracks_scanned: int,
    albums_scanned: int,
    playlists_scanned: int | None = None,
    duration_seconds: float,
) -> str:
    scan_parts = f"tracks={tracks_scanned}, albums={albums_scanned}"
    if playlists_scanned is not None:
        scan_parts = f"{scan_parts}, playlists={playlists_scanned}"
    return (
        f"{job_label} completed for {root_path} "
        f"({scan_parts}, duration={duration_seconds:.2f}s)"
    )


def library_job_detail_lines(
    *,
    tracks_scanned: int,
    albums_scanned: int,
    playlists_scanned: int | None = None,
    genre_resolution: GenreResolutionStats,
    cover_art_resolution: CoverArtResolutionStats,
) -> tuple[str, ...]:
    scan_lines = [
        f"tracks scanned: {tracks_scanned}",
        f"albums scanned: {albums_scanned}",
    ]
    if playlists_scanned is not None:
        scan_lines.append(f"playlists scanned: {playlists_scanned}")
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


@dataclass(frozen=True, slots=True)
class LibraryRescanResult:
    roots_scanned: int
    tracks_scanned: int
    albums_scanned: int
    playlists_scanned: int
    genre_resolution: GenreResolutionStats
    cover_art_resolution: CoverArtResolutionStats


@dataclass(frozen=True, slots=True)
class LibrarySyncPlan:
    root_rows: tuple[tuple[int, str], ...]
    roots_added: int
    roots_removed: int
    changed: bool


@dataclass(frozen=True, slots=True)
class LibrarySyncResult:
    roots_configured: int
    roots_added: int
    roots_removed: int
    roots_scanned: int
    tracks_scanned: int
    albums_scanned: int
    playlists_scanned: int
    changed: bool
    genre_resolution: GenreResolutionStats
    cover_art_resolution: CoverArtResolutionStats


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
            duration_seconds=duration_seconds,
        ),
    )
    for line in library_job_detail_lines(
        tracks_scanned=result.tracks_scanned,
        albums_scanned=result.albums_scanned,
        playlists_scanned=result.playlists_scanned,
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
            "duration_seconds": duration_seconds,
        },
    )


def sync_library_roots(
    database: Path,
    configured_roots: Iterable[Path],
    *,
    album_artist_split_patterns: Iterable[str | None] = DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
    cancel_check: Callable[[], None] | None = None,
) -> LibrarySyncResult:
    desired_roots = tuple(normalized_configured_roots(configured_roots))
    validate_sync_roots(desired_roots)
    sync_plan = plan_library_root_sync(database, desired_roots)

    if cancel_check is not None:
        cancel_check()
    if not sync_plan.changed:
        return LibrarySyncResult(
            roots_configured=len(desired_roots),
            roots_added=0,
            roots_removed=0,
            roots_scanned=0,
            tracks_scanned=0,
            albums_scanned=0,
            playlists_scanned=0,
            changed=False,
            genre_resolution=GenreResolutionStats(),
            cover_art_resolution=CoverArtResolutionStats(),
        )

    def scan_progress(message: str) -> None:
        if cancel_check is not None:
            cancel_check()
        LOGGER.info("%s", library_scan_progress_text("sync", message))

    if sync_plan.root_rows:
        library = build_library(
            [Path(root_path) for _position, root_path in sync_plan.root_rows],
            progress=scan_progress,
            progress_every=500,
        )
    else:
        library = MusicLibrary(
            roots=[],
            tracks=[],
            supported_extensions=sorted(SUPPORTED_EXTENSIONS),
            generated_at=datetime.now(UTC).isoformat(),
        )

    if cancel_check is not None:
        cancel_check()
    connection = connect_database(database, create=False)
    try:
        connection.execute("SAVEPOINT sync_roots")
        try:
            if cancel_check is not None:
                cancel_check()
            if sync_plan.root_rows:
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
            else:
                genre_resolution = GenreResolutionStats()
                cover_art_resolution = CoverArtResolutionStats()
            save_library_with_options(
                library,
                database,
                connection=connection,
                root_rows=sync_plan.root_rows,
                album_artist_split_patterns=album_artist_split_patterns,
            )
            if cancel_check is not None:
                cancel_check()
            connection.execute("RELEASE SAVEPOINT sync_roots")
            connection.commit()
        except Exception:
            connection.execute("ROLLBACK TO SAVEPOINT sync_roots")
            connection.execute("RELEASE SAVEPOINT sync_roots")
            connection.rollback()
            raise
    finally:
        connection.close()

    albums = group_library_albums(library)
    return LibrarySyncResult(
        roots_configured=len(desired_roots),
        roots_added=sync_plan.roots_added,
        roots_removed=sync_plan.roots_removed,
        roots_scanned=len(sync_plan.root_rows),
        tracks_scanned=len(library.tracks),
        albums_scanned=len(albums),
        playlists_scanned=len(library.playlists),
        changed=True,
        genre_resolution=genre_resolution,
        cover_art_resolution=cover_art_resolution,
    )


def normalized_configured_roots(configured_roots: Iterable[Path]) -> tuple[Path, ...]:
    return tuple(Path(root).expanduser().resolve(strict=False) for root in configured_roots)


def validate_sync_roots(roots: Iterable[Path]) -> None:
    for root in roots:
        if not root.exists():
            raise ValueError(f"directory does not exist: {root}")
        if not root.is_dir():
            raise ValueError(f"path is not a directory: {root}")


def plan_library_root_sync(database: Path, desired_roots: tuple[Path, ...]) -> LibrarySyncPlan:
    current_roots = tuple(LibraryQueries(database).library_roots())
    current_rows = tuple(
        (
            root.position,
            str(Path(root.path).expanduser().resolve(strict=False)),
        )
        for root in current_roots
    )
    current_by_path: dict[str, int] = {}
    for position, root_path in current_rows:
        current_by_path.setdefault(root_path, position)

    desired_paths = tuple(str(root) for root in desired_roots)
    desired_path_set = set(desired_paths)
    next_position = max((position for position, _path in current_rows), default=-1) + 1
    roots_added = 0
    root_rows: list[tuple[int, str]] = []
    for root_path in desired_paths:
        position = current_by_path.get(root_path)
        if position is None:
            position = next_position
            next_position += 1
            roots_added += 1
        root_rows.append((position, root_path))

    roots_removed = len({root_path for _position, root_path in current_rows} - desired_path_set)
    changed = tuple(sorted(current_rows)) != tuple(sorted(root_rows))
    return LibrarySyncPlan(
        root_rows=tuple(root_rows),
        roots_added=roots_added,
        roots_removed=roots_removed,
        changed=changed,
    )


def run_sync_job(
    runtime: PlayerRuntime,
    configured_roots: Iterable[Path],
    cancel_token: PlayerJobCancelToken,
) -> PlayerJobResult:
    started_at = perf_counter()
    result = sync_library_roots(
        runtime.database,
        configured_roots,
        album_artist_split_patterns=runtime.album_artist_split_patterns,
        cancel_check=cancel_token.raise_if_canceled,
    )
    duration_seconds = perf_counter() - started_at
    if not result.changed:
        LOGGER.info(
            "sync completed with no library root changes (roots=%s, duration=%.2fs)",
            result.roots_configured,
            duration_seconds,
        )
        return PlayerJobResult(
            message="Sync completed.",
            context={
                "roots_configured": result.roots_configured,
                "roots_added": 0,
                "roots_removed": 0,
                "roots_scanned": 0,
                "duration_seconds": duration_seconds,
            },
        )

    LOGGER.info(
        "%s",
        library_job_summary_text(
            "sync",
            "configured roots",
            tracks_scanned=result.tracks_scanned,
            albums_scanned=result.albums_scanned,
            playlists_scanned=result.playlists_scanned,
            duration_seconds=duration_seconds,
        ),
    )
    for line in library_job_detail_lines(
        tracks_scanned=result.tracks_scanned,
        albums_scanned=result.albums_scanned,
        playlists_scanned=result.playlists_scanned,
        genre_resolution=result.genre_resolution,
        cover_art_resolution=result.cover_art_resolution,
    ):
        LOGGER.info("%s", line)
    return PlayerJobResult(
        message="Sync completed.",
        context={
            "roots_configured": result.roots_configured,
            "roots_added": result.roots_added,
            "roots_removed": result.roots_removed,
            "roots_scanned": result.roots_scanned,
            "tracks_scanned": result.tracks_scanned,
            "albums_scanned": result.albums_scanned,
            "playlists_scanned": result.playlists_scanned,
            "duration_seconds": duration_seconds,
        },
    )


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
