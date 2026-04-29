from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import sqlite3
from time import perf_counter

from ..queries import LibraryQueries, LibraryRootFilterOption, library_root_filter_label
from ..database import connect_database, rebuild_album_search_index
from ...discogs import group_library_albums, most_common_value, most_common_year, parse_year
from ..library import (
    CoverArtResolutionStats,
    GenreResolutionStats,
    load_library,
    resolve_library_cover_art,
    resolve_library_genres,
    save_library_with_options,
)
from ...models import MusicLibrary
from .actions import record_player_action
from ...player_errors import PlayerNotFoundError
from ...player_runtime import PlayerRuntime
from ...scanner import build_library, iter_playlist_files, parse_playlists

LOGGER = logging.getLogger("kukicha.player")

def create_library_root(database: Path, root_path: str) -> LibraryRootFilterOption:
    root = prepare_library_root(database, root_path)
    connection = connect_database(database)
    try:
        connection.execute(
            "INSERT INTO library_roots (position, root_path) VALUES (?, ?)",
            (root.position, root.path),
        )
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
    files_missing_required_tags: int,
    duration_seconds: float,
) -> str:
    return (
        f"{job_label} completed for {root_path} "
        f"(tracks={tracks_scanned}, albums={albums_scanned}, "
        f"missing_required_tags={files_missing_required_tags}, "
        f"duration={duration_seconds:.2f}s)"
    )


def library_job_detail_lines(
    *,
    tracks_scanned: int,
    albums_scanned: int,
    files_missing_required_tags: int,
    genre_resolution: GenreResolutionStats,
    cover_art_resolution: CoverArtResolutionStats,
) -> tuple[str, ...]:
    return (
        f"tracks scanned: {tracks_scanned}",
        f"albums scanned: {albums_scanned}",
        f"files missing required tags: {files_missing_required_tags}",
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


def run_delete_root_job(runtime: PlayerRuntime, root: LibraryRootFilterOption) -> None:
    started_at = perf_counter()
    root_label = root_display_label(root)
    try:
        delete_library_root(runtime.database, root.position)
        action = record_player_action(
            runtime.database,
            kind="delete_root",
            status="succeeded",
            message=f"Delete completed for {root_label}.",
            context={
                "path": root.path,
                "root_position": root.position,
                "duration_seconds": perf_counter() - started_at,
            },
        )
        runtime.publish_notification(action)
    except Exception as error:
        LOGGER.exception("delete root failed for %s", root.path)
        try:
            action = record_player_action(
                runtime.database,
                kind="delete_root",
                status="failed",
                message=f"Delete failed for {root_label}.",
                context={
                    "path": root.path,
                    "root_position": root.position,
                    "duration_seconds": perf_counter() - started_at,
                    "error": str(error),
                },
            )
        except Exception:
            LOGGER.exception("failed to record delete failure for %s", root.path)
        else:
            runtime.publish_notification(action)
    finally:
        runtime.finish_library_job()


@dataclass(frozen=True, slots=True)
class RootScanResult:
    root: LibraryRootFilterOption
    tracks_scanned: int
    albums_scanned: int
    files_missing_required_tags: int
    genre_resolution: GenreResolutionStats
    cover_art_resolution: CoverArtResolutionStats


@dataclass(frozen=True, slots=True)
class RootRescanResult:
    root: LibraryRootFilterOption
    tracks_scanned: int
    albums_scanned: int
    files_missing_required_tags: int
    genre_resolution: GenreResolutionStats
    cover_art_resolution: CoverArtResolutionStats


def scan_library_with_new_root(database: Path, root_path: str) -> RootScanResult:
    root = prepare_library_root(database, root_path)
    existing_roots = tuple(LibraryQueries(database).library_roots())
    combined_root_rows = [*( (item.position, item.path) for item in existing_roots ), (root.position, root.path)]
    combined_root_paths = [Path(root_path) for _position, root_path in combined_root_rows]
    missing_required_tag_count = 0

    def log_missing_required_tags(_track: object, _missing_fields: list[str]) -> None:
        nonlocal missing_required_tag_count
        missing_required_tag_count += 1

    library = build_library(
        combined_root_paths,
        progress=lambda message: LOGGER.info(
            "%s",
            library_scan_progress_text("add and scan", message),
        ),
        progress_every=500,
        on_missing_required_tags=log_missing_required_tags,
    )
    connection = connect_database(database, create=False)
    try:
        connection.execute("SAVEPOINT add_root_scan")
        try:
            genre_resolution = resolve_library_genres(
                library,
                database,
                connection=connection,
            ) or GenreResolutionStats()
            cover_art_resolution = resolve_library_cover_art(
                library,
                database,
                connection=connection,
            ) or CoverArtResolutionStats()
            save_library_with_options(
                library,
                database,
                connection=connection,
                root_rows=combined_root_rows,
            )
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
        files_missing_required_tags=missing_required_tag_count,
        genre_resolution=genre_resolution,
        cover_art_resolution=cover_art_resolution,
    )


def run_add_root_job(runtime: PlayerRuntime, root: LibraryRootFilterOption) -> None:
    started_at = perf_counter()
    root_label = root_display_label(root)
    try:
        result = scan_library_with_new_root(runtime.database, root.path)
        duration_seconds = perf_counter() - started_at
        LOGGER.info(
            "%s",
            library_job_summary_text(
                "add and scan",
                root.path,
                tracks_scanned=result.tracks_scanned,
                albums_scanned=result.albums_scanned,
                files_missing_required_tags=result.files_missing_required_tags,
                duration_seconds=duration_seconds,
            ),
        )
        for line in library_job_detail_lines(
            tracks_scanned=result.tracks_scanned,
            albums_scanned=result.albums_scanned,
            files_missing_required_tags=result.files_missing_required_tags,
            genre_resolution=result.genre_resolution,
            cover_art_resolution=result.cover_art_resolution,
        ):
            LOGGER.info("%s", line)
        action = record_player_action(
            runtime.database,
            kind="add_root",
            status="succeeded",
            message=f"Add and scan completed for {root_label}.",
            context={
                "path": root.path,
                "root_position": root.position,
                "tracks_scanned": result.tracks_scanned,
                "albums_scanned": result.albums_scanned,
                "files_missing_required_tags": result.files_missing_required_tags,
                "duration_seconds": duration_seconds,
            },
        )
        runtime.publish_notification(action)
    except Exception as error:
        LOGGER.exception("add and scan failed for %s", root.path)
        try:
            action = record_player_action(
                runtime.database,
                kind="add_root",
                status="failed",
                message=f"Add and scan failed for {root_label}.",
                context={
                    "path": root.path,
                    "root_position": root.position,
                    "duration_seconds": perf_counter() - started_at,
                    "error": str(error),
                },
            )
        except Exception:
            LOGGER.exception("failed to record add-root failure for %s", root.path)
        else:
            runtime.publish_notification(action)
    finally:
        runtime.finish_library_job()


def rescan_library_root(database: Path, position: int) -> RootRescanResult:
    root = library_root_by_position(database, position)
    roots = tuple(LibraryQueries(database).library_roots())
    existing_library = load_library(database, include_artwork=True)
    missing_required_tag_count = 0

    def log_missing_required_tags(_track: object, _missing_fields: list[str]) -> None:
        nonlocal missing_required_tag_count
        missing_required_tag_count += 1

    rescanned_library = build_library(
        [Path(root.path)],
        progress=lambda message: LOGGER.info(
            "%s",
            library_scan_progress_text("rescan", message),
        ),
        progress_every=500,
        on_missing_required_tags=log_missing_required_tags,
    )
    for track in rescanned_library.tracks:
        track.root_position = position

    combined_tracks = [
        *[track for track in existing_library.tracks if track.root_position != position],
        *rescanned_library.tracks,
    ]
    rescanned_playlist_paths = [
        (position, path)
        for path in iter_playlist_files([Path(root.path)])
    ]
    combined_library = MusicLibrary(
        roots=[item.path for item in roots],
        tracks=combined_tracks,
        supported_extensions=(
            rescanned_library.supported_extensions
            if rescanned_library.supported_extensions
            else existing_library.supported_extensions
        ),
        generated_at=rescanned_library.generated_at,
        playlists=[
            *[
                playlist
                for playlist in existing_library.playlists
                if playlist.root_position != position
            ],
            *parse_playlists(rescanned_playlist_paths, combined_tracks),
        ],
    )
    connection = connect_database(database, create=False)
    try:
        connection.execute("SAVEPOINT rescan_root")
        try:
            genre_resolution = resolve_library_genres(
                rescanned_library,
                database,
                connection=connection,
            ) or GenreResolutionStats()
            cover_art_resolution = resolve_library_cover_art(
                rescanned_library,
                database,
                connection=connection,
            ) or CoverArtResolutionStats()
            save_library_with_options(
                combined_library,
                database,
                connection=connection,
                root_rows=[(item.position, item.path) for item in roots],
            )
            connection.execute("RELEASE SAVEPOINT rescan_root")
            connection.commit()
        except Exception:
            connection.execute("ROLLBACK TO SAVEPOINT rescan_root")
            connection.execute("RELEASE SAVEPOINT rescan_root")
            connection.rollback()
            raise
    finally:
        connection.close()

    albums = group_library_albums(rescanned_library)
    return RootRescanResult(
        root=root,
        tracks_scanned=len(rescanned_library.tracks),
        albums_scanned=len(albums),
        files_missing_required_tags=missing_required_tag_count,
        genre_resolution=genre_resolution,
        cover_art_resolution=cover_art_resolution,
    )


def run_rescan_root_job(runtime: PlayerRuntime, root: LibraryRootFilterOption) -> None:
    started_at = perf_counter()
    root_label = root_display_label(root)
    try:
        result = rescan_library_root(runtime.database, root.position)
        duration_seconds = perf_counter() - started_at
        LOGGER.info(
            "%s",
            library_job_summary_text(
                "rescan",
                root.path,
                tracks_scanned=result.tracks_scanned,
                albums_scanned=result.albums_scanned,
                files_missing_required_tags=result.files_missing_required_tags,
                duration_seconds=duration_seconds,
            ),
        )
        for line in library_job_detail_lines(
            tracks_scanned=result.tracks_scanned,
            albums_scanned=result.albums_scanned,
            files_missing_required_tags=result.files_missing_required_tags,
            genre_resolution=result.genre_resolution,
            cover_art_resolution=result.cover_art_resolution,
        ):
            LOGGER.info("%s", line)
        action = record_player_action(
            runtime.database,
            kind="rescan_root",
            status="succeeded",
            message=f"Rescan completed for {root_label}.",
            context={
                "path": root.path,
                "root_position": root.position,
                "tracks_scanned": result.tracks_scanned,
                "albums_scanned": result.albums_scanned,
                "files_missing_required_tags": result.files_missing_required_tags,
                "duration_seconds": duration_seconds,
            },
        )
        runtime.publish_notification(action)
    except Exception as error:
        LOGGER.exception("rescan root failed for %s", root.path)
        try:
            action = record_player_action(
                runtime.database,
                kind="rescan_root",
                status="failed",
                message=f"Rescan failed for {root_label}.",
                context={
                    "path": root.path,
                    "root_position": root.position,
                    "duration_seconds": perf_counter() - started_at,
                    "error": str(error),
                },
            )
        except Exception:
            LOGGER.exception("failed to record rescan failure for %s", root.path)
        else:
            runtime.publish_notification(action)
    finally:
        runtime.finish_library_job()

def delete_library_root(database: Path, position: int) -> LibraryRootFilterOption:
    connection = connect_database(database, create=False)
    try:
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
            rebuild_album_search_index(connection)
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
    deleted_album_ids = [album_id for album_id in affected_album_ids if album_id not in surviving_album_ids]
    if deleted_album_ids:
        deleted_placeholders = ", ".join("?" for _ in deleted_album_ids)
        connection.execute(
            f"DELETE FROM library_albums WHERE album_id IN ({deleted_placeholders})",
            deleted_album_ids,
        )

    if not surviving_album_ids:
        return

    rows_by_album: dict[str, list[sqlite3.Row]] = {}
    for row in surviving_rows:
        rows_by_album.setdefault(str(row["album_id"]), []).append(row)

    for album_id, rows in rows_by_album.items():
        artist = most_common_value(
            (str(row["album_artist"]) if row["album_artist"] else str(row["artist"]) if row["artist"] else None)
            for row in rows
        ) or "<unknown artist>"
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
                artist,
                album,
                year,
                track_count,
                file_created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(album_id) DO UPDATE SET
                artist = excluded.artist,
                album = excluded.album,
                year = excluded.year,
                track_count = excluded.track_count,
                file_created_at = excluded.file_created_at
            """,
            (album_id, artist, album, year, len(rows), file_created_at),
        )


def root_payload(root: LibraryRootFilterOption) -> dict[str, object]:
    return {
        "position": root.position,
        "path": root.path,
        "label": root.label,
    }
