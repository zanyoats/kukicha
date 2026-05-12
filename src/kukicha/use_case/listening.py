from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from sqlite3 import Connection, Row
from typing import Any

from ..album_artists import normalized_album_artist_values
from ..models import normalize_genre_values
from ..playlist_art import playlist_cover_svg
from ..text import normalize_text
from .database import connect_database
from .queries.models import (
    AlbumSummary,
    PlaylistItemNotFoundError,
    TrackNotFoundError,
)


@dataclass(frozen=True, slots=True)
class ListeningAlbum:
    album: AlbumSummary
    play_count: int
    last_played_at: str


@dataclass(frozen=True, slots=True)
class ListeningPlaylist:
    playlist: AlbumSummary
    play_count: int
    last_played_at: str


@dataclass(frozen=True, slots=True)
class ListeningTrack:
    track_key: str
    track_id: int | None
    album_id: str
    title: str
    artist: str
    album: str
    path: str
    play_count: int
    last_played_at: str


@dataclass(frozen=True, slots=True)
class ListeningNamedStat:
    key: str
    name: str
    play_count: int
    last_played_at: str
    url: str = ""


@dataclass(frozen=True, slots=True)
class ListeningNowPlaying:
    album: AlbumSummary
    track_title: str
    artist: str
    updated_at: str
    playback_id: int | None


@dataclass(frozen=True, slots=True)
class HomeDashboard:
    now_playing: ListeningNowPlaying | None
    recent_albums: tuple[ListeningAlbum, ...]
    recent_playlists: tuple[ListeningPlaylist, ...]
    recent_tracks: tuple[ListeningTrack, ...]
    recent_artists: tuple[ListeningNamedStat, ...]
    recent_genres: tuple[ListeningNamedStat, ...]
    recently_added_albums: tuple[AlbumSummary, ...]

    @property
    def has_listening_history(self) -> bool:
        return bool(
            self.recent_albums
            or self.recent_playlists
            or self.recent_tracks
            or self.recent_artists
            or self.recent_genres
        )


def record_playback(
    database: Path,
    playback_id: int,
    *,
    submission: bool,
    played_at: datetime | None = None,
    source: str = "",
    session_key: str = "default",
) -> None:
    timestamp = (played_at or datetime.now(UTC)).astimezone(UTC).isoformat()
    with connect_database(database, create=False) as connection:
        snapshot = playback_snapshot(connection, playback_id)
        snapshot_json = json.dumps(snapshot, sort_keys=True)
        connection.execute(
            """
            INSERT INTO play_now_playing (
                session_key,
                updated_at,
                source,
                playback_id,
                track_key,
                album_id,
                playlist_key,
                snapshot_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_key) DO UPDATE SET
                updated_at = excluded.updated_at,
                source = excluded.source,
                playback_id = excluded.playback_id,
                track_key = excluded.track_key,
                album_id = excluded.album_id,
                playlist_key = excluded.playlist_key,
                snapshot_json = excluded.snapshot_json
            """,
            (
                session_key,
                timestamp,
                source,
                playback_id,
                snapshot.get("track_key"),
                snapshot.get("album_id"),
                snapshot.get("playlist_key"),
                snapshot_json,
            ),
        )
        if not submission:
            return

        connection.execute(
            """
            INSERT INTO play_events (
                played_at,
                source,
                playback_id,
                track_key,
                album_id,
                playlist_key,
                snapshot_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                source,
                playback_id,
                snapshot.get("track_key"),
                snapshot.get("album_id"),
                snapshot.get("playlist_key"),
                snapshot_json,
            ),
        )
        increment_track_stats(connection, snapshot, timestamp, snapshot_json)
        increment_album_stats(connection, snapshot, timestamp, snapshot_json)
        increment_playlist_stats(connection, snapshot, timestamp, snapshot_json)
        increment_artist_stats(connection, snapshot, timestamp)
        increment_genre_stats(connection, snapshot, timestamp)


def playback_snapshot(connection: Connection, playback_id: int) -> dict[str, object]:
    if playback_id >= 0:
        return track_playback_snapshot(connection, playback_id)
    return playlist_item_playback_snapshot(connection, -playback_id)


def track_playback_snapshot(connection: Connection, track_id: int) -> dict[str, object]:
    row = connection.execute(
        """
        SELECT
            track_id,
            play_fingerprint,
            album_id,
            path,
            artist,
            album_artist,
            album,
            title,
            track_number,
            disc_number
        FROM library_tracks
        WHERE track_id = ?
        """,
        (track_id,),
    ).fetchone()
    if row is None:
        raise TrackNotFoundError(track_id)
    return snapshot_from_track_row(connection, row)


def playlist_item_playback_snapshot(
    connection: Connection,
    playlist_item_id: int,
) -> dict[str, object]:
    row = connection.execute(
        """
        SELECT
            items.playlist_item_id,
            items.track_id,
            items.path,
            items.title,
            items.genre,
            playlists.playlist_id,
            playlists.path AS playlist_path,
            playlists.name AS playlist_name,
            playlists.cover_svg
        FROM library_playlist_items AS items
        JOIN library_playlists AS playlists
            ON playlists.playlist_id = items.playlist_id
        WHERE items.playlist_item_id = ?
        """,
        (playlist_item_id,),
    ).fetchone()
    if row is None:
        raise PlaylistItemNotFoundError(playlist_item_id)

    playlist_snapshot = {
        "playlist_key": str(row["playlist_path"]),
        "playlist_id": int(row["playlist_id"]),
        "playlist_path": str(row["playlist_path"]),
        "playlist_name": str(row["playlist_name"]),
        "playlist_cover_svg": str(row["cover_svg"] or ""),
    }
    track_id = row["track_id"]
    if track_id is not None:
        snapshot = track_playback_snapshot(connection, int(track_id))
        snapshot.update(playlist_snapshot)
        return snapshot

    title = str(row["title"] or Path(str(row["path"])).name)
    genre = str(row["genre"] or "").strip()
    return {
        **playlist_snapshot,
        "path": str(row["path"]),
        "title": title,
        "artist": "",
        "album": str(row["playlist_name"]),
        "album_artists": [],
        "genres": [genre] if genre else [],
    }


def snapshot_from_track_row(connection: Connection, row: Row) -> dict[str, object]:
    track_id = int(row["track_id"])
    album_id = str(row["album_id"] or "")
    path = str(row["path"])
    title = str(row["title"] or Path(path).stem)
    track_key = str(row["play_fingerprint"] or "") or track_play_fingerprint(
        album_id=album_id,
        disc_number=row["disc_number"],
        track_number=row["track_number"],
        title=title,
        path=path,
    )
    album_artists = album_artists_for_album(connection, album_id)
    artist = str(row["artist"] or row["album_artist"] or album_artist_text(album_artists))
    return {
        "track_id": track_id,
        "track_key": track_key,
        "album_id": album_id,
        "path": path,
        "title": title,
        "artist": artist,
        "album_artist": str(row["album_artist"] or ""),
        "album_artists": list(album_artists),
        "album": str(row["album"] or ""),
        "genres": track_genres(connection, track_id),
    }


def increment_track_stats(
    connection: Connection,
    snapshot: dict[str, object],
    timestamp: str,
    snapshot_json: str,
) -> None:
    track_key = text_value(snapshot.get("track_key"))
    if not track_key:
        return
    connection.execute(
        """
        INSERT INTO play_track_stats (
            track_key,
            play_count,
            last_played_at,
            track_id,
            album_id,
            path,
            title,
            artist,
            album,
            snapshot_json
        ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(track_key) DO UPDATE SET
            play_count = play_track_stats.play_count + 1,
            last_played_at = excluded.last_played_at,
            track_id = excluded.track_id,
            album_id = excluded.album_id,
            path = excluded.path,
            title = excluded.title,
            artist = excluded.artist,
            album = excluded.album,
            snapshot_json = excluded.snapshot_json
        """,
        (
            track_key,
            timestamp,
            int_value(snapshot.get("track_id")),
            text_value(snapshot.get("album_id")),
            text_value(snapshot.get("path")),
            text_value(snapshot.get("title")),
            text_value(snapshot.get("artist")),
            text_value(snapshot.get("album")),
            snapshot_json,
        ),
    )


def increment_album_stats(
    connection: Connection,
    snapshot: dict[str, object],
    timestamp: str,
    snapshot_json: str,
) -> None:
    album_id = text_value(snapshot.get("album_id"))
    if not album_id:
        return
    album_artists = tuple_value(snapshot.get("album_artists"))
    artist = album_artist_text(album_artists) or text_value(snapshot.get("artist"))
    art_track_id = album_art_track_id(connection, album_id)
    connection.execute(
        """
        INSERT INTO play_album_stats (
            album_id,
            play_count,
            last_played_at,
            album,
            artist,
            art_track_id,
            snapshot_json
        ) VALUES (?, 1, ?, ?, ?, ?, ?)
        ON CONFLICT(album_id) DO UPDATE SET
            play_count = play_album_stats.play_count + 1,
            last_played_at = excluded.last_played_at,
            album = excluded.album,
            artist = excluded.artist,
            art_track_id = excluded.art_track_id,
            snapshot_json = excluded.snapshot_json
        """,
        (
            album_id,
            timestamp,
            text_value(snapshot.get("album")),
            artist,
            art_track_id,
            snapshot_json,
        ),
    )


def increment_playlist_stats(
    connection: Connection,
    snapshot: dict[str, object],
    timestamp: str,
    snapshot_json: str,
) -> None:
    playlist_key = text_value(snapshot.get("playlist_key"))
    if not playlist_key:
        return
    connection.execute(
        """
        INSERT INTO play_playlist_stats (
            playlist_key,
            play_count,
            last_played_at,
            playlist_id,
            path,
            name,
            cover_svg,
            snapshot_json
        ) VALUES (?, 1, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(playlist_key) DO UPDATE SET
            play_count = play_playlist_stats.play_count + 1,
            last_played_at = excluded.last_played_at,
            playlist_id = excluded.playlist_id,
            path = excluded.path,
            name = excluded.name,
            cover_svg = excluded.cover_svg,
            snapshot_json = excluded.snapshot_json
        """,
        (
            playlist_key,
            timestamp,
            int_value(snapshot.get("playlist_id")),
            text_value(snapshot.get("playlist_path")),
            text_value(snapshot.get("playlist_name")),
            text_value(snapshot.get("playlist_cover_svg")),
            snapshot_json,
        ),
    )


def increment_artist_stats(
    connection: Connection,
    snapshot: dict[str, object],
    timestamp: str,
) -> None:
    artists = normalized_album_artist_values(tuple_value(snapshot.get("album_artists")))
    if not artists:
        artists = normalized_album_artist_values((text_value(snapshot.get("artist")),))
    for artist in artists:
        artist_key = normalize_text(artist)
        if not artist_key:
            continue
        connection.execute(
            """
            INSERT INTO play_artist_stats (
                artist_key,
                artist,
                play_count,
                last_played_at
            ) VALUES (?, ?, 1, ?)
            ON CONFLICT(artist_key) DO UPDATE SET
                artist = excluded.artist,
                play_count = play_artist_stats.play_count + 1,
                last_played_at = excluded.last_played_at
            """,
            (artist_key, artist, timestamp),
        )


def increment_genre_stats(
    connection: Connection,
    snapshot: dict[str, object],
    timestamp: str,
) -> None:
    for genre in normalize_genre_values(tuple_value(snapshot.get("genres"))):
        genre_key = normalize_text(genre)
        if not genre_key:
            continue
        connection.execute(
            """
            INSERT INTO play_genre_stats (
                genre_key,
                genre,
                play_count,
                last_played_at
            ) VALUES (?, ?, 1, ?)
            ON CONFLICT(genre_key) DO UPDATE SET
                genre = excluded.genre,
                play_count = play_genre_stats.play_count + 1,
                last_played_at = excluded.last_played_at
            """,
            (genre_key, genre, timestamp),
        )


def home_dashboard(
    database: Path,
    *,
    limit: int = 8,
    recently_added_days: int = 30,
) -> HomeDashboard:
    with connect_database(database, create=False) as connection:
        return HomeDashboard(
            now_playing=now_playing_album(connection),
            recent_albums=recent_listening_albums(connection, limit=limit),
            recent_playlists=recent_listening_playlists(connection, limit=limit),
            recent_tracks=recent_listening_tracks(connection, limit=limit),
            recent_artists=recent_named_stats(
                connection,
                table="play_artist_stats",
                key_column="artist_key",
                name_column="artist",
                url_prefix="/albums?artist=",
                limit=limit,
            ),
            recent_genres=recent_named_stats(
                connection,
                table="play_genre_stats",
                key_column="genre_key",
                name_column="genre",
                url_prefix="/albums?genre[0][p]=",
                limit=limit,
            ),
            recently_added_albums=recently_added_album_summaries(
                connection,
                days=recently_added_days,
                limit=limit,
            ),
        )


def now_playing_album(connection: Connection) -> ListeningNowPlaying | None:
    row = connection.execute(
        """
        SELECT updated_at, playback_id, album_id, snapshot_json
        FROM play_now_playing
        ORDER BY updated_at DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    snapshot = snapshot_payload(row["snapshot_json"])
    album_id = str(row["album_id"] or snapshot.get("album_id") or "")
    if not album_id:
        return None
    album = current_album_summary(
        connection,
        album_id,
        snapshot=snapshot,
    )
    return ListeningNowPlaying(
        album=album,
        track_title=text_value(snapshot.get("title")),
        artist=text_value(snapshot.get("artist")) or album.artist,
        updated_at=str(row["updated_at"]),
        playback_id=int_value(row["playback_id"]),
    )


def snapshot_payload(value: object) -> dict[str, object]:
    try:
        payload = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def current_album_summary(
    connection: Connection,
    album_id: str,
    *,
    snapshot: dict[str, object],
) -> AlbumSummary:
    row = connection.execute(
        """
        SELECT
            album_id,
            album,
            year,
            track_count,
            file_created_at,
            art_track_id
        FROM library_albums
        WHERE album_id = ?
        """,
        (album_id,),
    ).fetchone()
    if row is not None:
        artists = album_artists_by_ids(connection, (album_id,))
        return AlbumSummary(
            album_id=album_id,
            artist=album_artist_text(artists.get(album_id, ()))
            or text_value(snapshot.get("artist")),
            album=str(row["album"] or snapshot.get("album") or album_id),
            year=int(row["year"]) if row["year"] is not None else None,
            track_count=int(row["track_count"] or 0),
            album_artists=artists.get(album_id, ()),
            file_created_at=row["file_created_at"],
            art_track_id=(
                int(row["art_track_id"]) if row["art_track_id"] is not None else None
            ),
        )

    album_artists = tuple_value(snapshot.get("album_artists"))
    artist = album_artist_text(album_artists) or text_value(snapshot.get("artist"))
    return AlbumSummary(
        album_id=album_id,
        artist=artist,
        album=text_value(snapshot.get("album")) or album_id,
        year=None,
        track_count=0,
        album_artists=album_artists,
        file_created_at=None,
    )


def recent_listening_albums(
    connection: Connection,
    *,
    limit: int,
) -> tuple[ListeningAlbum, ...]:
    rows = list(
        connection.execute(
            """
            SELECT
                stats.album_id,
                stats.play_count,
                stats.last_played_at,
                stats.album AS snapshot_album,
                stats.artist AS snapshot_artist,
                stats.art_track_id AS snapshot_art_track_id,
                albums.album,
                albums.year,
                albums.track_count,
                albums.file_created_at,
                albums.art_track_id
            FROM play_album_stats AS stats
            LEFT JOIN library_albums AS albums
                ON albums.album_id = stats.album_id
            ORDER BY stats.last_played_at DESC, stats.play_count DESC, stats.album_id
            LIMIT ?
            """,
            (limit,),
        )
    )
    artists = album_artists_by_ids(
        connection,
        (str(row["album_id"]) for row in rows),
    )
    return tuple(
        ListeningAlbum(
            album=AlbumSummary(
                album_id=str(row["album_id"]),
                artist=album_artist_text(artists.get(str(row["album_id"]), ()))
                or str(row["snapshot_artist"] or ""),
                album=str(row["album"] or row["snapshot_album"] or row["album_id"]),
                year=int(row["year"]) if row["year"] is not None else None,
                track_count=int(row["track_count"] or 0),
                album_artists=artists.get(str(row["album_id"]), ()),
                file_created_at=row["file_created_at"],
                art_track_id=(
                    int(row["art_track_id"])
                    if row["art_track_id"] is not None
                    else int(row["snapshot_art_track_id"])
                    if row["snapshot_art_track_id"] is not None
                    else None
                ),
            ),
            play_count=int(row["play_count"]),
            last_played_at=str(row["last_played_at"]),
        )
        for row in rows
    )


def recent_listening_playlists(
    connection: Connection,
    *,
    limit: int,
) -> tuple[ListeningPlaylist, ...]:
    rows = list(
        connection.execute(
            """
            SELECT
                stats.playlist_key,
                stats.play_count,
                stats.last_played_at,
                stats.path AS snapshot_path,
                stats.name AS snapshot_name,
                stats.cover_svg AS snapshot_cover_svg,
                playlists.playlist_id,
                playlists.path,
                playlists.name,
                playlists.cover_svg,
                playlists.file_created_at,
                COUNT(items.playlist_item_id) AS item_count
            FROM play_playlist_stats AS stats
            LEFT JOIN library_playlists AS playlists
                ON playlists.path = stats.playlist_key
            LEFT JOIN library_playlist_items AS items
                ON items.playlist_id = playlists.playlist_id
            GROUP BY stats.playlist_key
            ORDER BY stats.last_played_at DESC, stats.play_count DESC, stats.playlist_key
            LIMIT ?
            """,
            (limit,),
        )
    )
    return tuple(
        ListeningPlaylist(
            playlist=AlbumSummary(
                album_id=f"playlist:{int(row['playlist_id'])}"
                if row["playlist_id"] is not None
                else f"playlist:{row['playlist_key']}",
                artist="Playlist",
                album=str(row["name"] or row["snapshot_name"] or "Playlist"),
                year=None,
                track_count=int(row["item_count"] or 0),
                file_created_at=row["file_created_at"],
                is_playlist=True,
                playlist_id=(
                    int(row["playlist_id"]) if row["playlist_id"] is not None else None
                ),
                path=str(row["path"] or row["snapshot_path"] or row["playlist_key"]),
                cover_svg=str(
                    row["cover_svg"]
                    or row["snapshot_cover_svg"]
                    or playlist_cover_svg(str(row["snapshot_name"] or "Playlist"))
                ),
            ),
            play_count=int(row["play_count"]),
            last_played_at=str(row["last_played_at"]),
        )
        for row in rows
    )


def recent_listening_tracks(
    connection: Connection,
    *,
    limit: int,
) -> tuple[ListeningTrack, ...]:
    rows = list(
        connection.execute(
            """
            SELECT
                track_key,
                play_count,
                last_played_at,
                track_id,
                album_id,
                path,
                title,
                artist,
                album
            FROM play_track_stats
            ORDER BY last_played_at DESC, play_count DESC, track_key
            LIMIT ?
            """,
            (limit,),
        )
    )
    tracks: list[ListeningTrack] = []
    for row in rows:
        current = current_track_for_key(connection, str(row["track_key"]))
        tracks.append(
            ListeningTrack(
                track_key=str(row["track_key"]),
                track_id=(
                    int(current["track_id"])
                    if current is not None and current["track_id"] is not None
                    else int(row["track_id"])
                    if row["track_id"] is not None
                    else None
                ),
                album_id=(
                    str(current["album_id"])
                    if current is not None and current["album_id"] is not None
                    else str(row["album_id"] or "")
                ),
                title=(
                    str(current["title"])
                    if current is not None and current["title"] is not None
                    else str(row["title"] or "")
                ),
                artist=(
                    str(current["artist"] or current["album_artist"] or "")
                    if current is not None
                    else str(row["artist"] or "")
                ),
                album=(
                    str(current["album"])
                    if current is not None and current["album"] is not None
                    else str(row["album"] or "")
                ),
                path=(
                    str(current["path"])
                    if current is not None and current["path"] is not None
                    else str(row["path"] or "")
                ),
                play_count=int(row["play_count"]),
                last_played_at=str(row["last_played_at"]),
            )
        )
    return tuple(tracks)


def recent_named_stats(
    connection: Connection,
    *,
    table: str,
    key_column: str,
    name_column: str,
    url_prefix: str,
    limit: int,
) -> tuple[ListeningNamedStat, ...]:
    rows = list(
        connection.execute(
            f"""
            SELECT
                {key_column} AS item_key,
                {name_column} AS item_name,
                play_count,
                last_played_at
            FROM {table}
            ORDER BY last_played_at DESC, play_count DESC, {key_column}
            LIMIT ?
            """,
            (limit,),
        )
    )
    return tuple(
        ListeningNamedStat(
            key=str(row["item_key"]),
            name=str(row["item_name"]),
            play_count=int(row["play_count"]),
            last_played_at=str(row["last_played_at"]),
            url=f"{url_prefix}{quote_query_value(str(row['item_name']))}",
        )
        for row in rows
    )


def recently_added_album_summaries(
    connection: Connection,
    *,
    days: int,
    limit: int,
) -> tuple[AlbumSummary, ...]:
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    rows = list(
        connection.execute(
            """
            SELECT
                album_id,
                album,
                year,
                track_count,
                file_created_at,
                art_track_id
            FROM library_albums
            WHERE NULLIF(file_created_at, '') IS NOT NULL
                AND file_created_at >= ?
            ORDER BY
                file_created_at DESC,
                artist_sort_key,
                CASE WHEN year IS NULL THEN 1 ELSE 0 END,
                year,
                album_sort_key,
                album_id
            LIMIT ?
            """,
            (cutoff, limit),
        )
    )
    artists = album_artists_by_ids(
        connection,
        (str(row["album_id"]) for row in rows),
    )
    return tuple(
        AlbumSummary(
            album_id=str(row["album_id"]),
            artist=album_artist_text(artists.get(str(row["album_id"]), ())),
            album=str(row["album"]),
            year=int(row["year"]) if row["year"] is not None else None,
            track_count=int(row["track_count"]),
            album_artists=artists.get(str(row["album_id"]), ()),
            file_created_at=row["file_created_at"],
            art_track_id=(
                int(row["art_track_id"]) if row["art_track_id"] is not None else None
            ),
        )
        for row in rows
    )


def update_track_play_fingerprints(connection: Connection) -> None:
    rows = list(
        connection.execute(
            """
            SELECT track_id, album_id, path, title, track_number, disc_number
            FROM library_tracks
            """
        )
    )
    for row in rows:
        connection.execute(
            """
            UPDATE library_tracks
            SET play_fingerprint = ?
            WHERE track_id = ?
            """,
            (
                track_play_fingerprint(
                    album_id=str(row["album_id"] or ""),
                    disc_number=row["disc_number"],
                    track_number=row["track_number"],
                    title=str(row["title"] or ""),
                    path=str(row["path"]),
                ),
                int(row["track_id"]),
            ),
        )


def track_play_fingerprint(
    *,
    album_id: str,
    disc_number: object,
    track_number: object,
    title: str,
    path: str,
) -> str:
    title_key = normalize_text(title) or normalize_text(Path(path).stem)
    payload = json.dumps(
        [
            str(album_id or ""),
            first_number(disc_number),
            first_number(track_number),
            title_key,
        ],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def first_number(value: object) -> int:
    if value is None:
        return 0
    text = str(value).strip()
    digits = []
    for character in text:
        if character.isdigit():
            digits.append(character)
            continue
        if digits:
            break
    return int("".join(digits)) if digits else 0


def album_artists_for_album(connection: Connection, album_id: str) -> tuple[str, ...]:
    if not album_id:
        return ()
    return tuple(
        str(row["artist"])
        for row in connection.execute(
            """
            SELECT artist
            FROM library_album_artists
            WHERE album_id = ?
            ORDER BY position
            """,
            (album_id,),
        )
    )


def album_artists_by_ids(
    connection: Connection,
    album_ids: Iterable[str],
) -> dict[str, tuple[str, ...]]:
    requested_ids = tuple(dict.fromkeys(album_id for album_id in album_ids if album_id))
    if not requested_ids:
        return {}
    placeholders = ", ".join("?" for _ in requested_ids)
    artists: dict[str, list[str]] = {}
    for row in connection.execute(
        f"""
        SELECT album_id, artist
        FROM library_album_artists
        WHERE album_id IN ({placeholders})
        ORDER BY album_id, position
        """,
        requested_ids,
    ):
        artists.setdefault(str(row["album_id"]), []).append(str(row["artist"]))
    return {album_id: tuple(values) for album_id, values in artists.items()}


def track_genres(connection: Connection, track_id: int) -> list[str]:
    return [
        str(row["genre"])
        for row in connection.execute(
            """
            SELECT genre
            FROM library_track_genres
            WHERE track_id = ?
            ORDER BY position
            """,
            (track_id,),
        )
    ]


def album_art_track_id(connection: Connection, album_id: str) -> int | None:
    row = connection.execute(
        """
        SELECT art_track_id
        FROM library_albums
        WHERE album_id = ?
        """,
        (album_id,),
    ).fetchone()
    return int(row["art_track_id"]) if row is not None and row["art_track_id"] is not None else None


def current_track_for_key(connection: Connection, track_key: str) -> Row | None:
    return connection.execute(
        """
        SELECT track_id, album_id, path, artist, album_artist, album, title
        FROM library_tracks
        WHERE play_fingerprint = ?
        ORDER BY track_id
        LIMIT 1
        """,
        (track_key,),
    ).fetchone()


def album_artist_text(values: Iterable[str]) -> str:
    return ", ".join(normalized_album_artist_values(values))


def text_value(value: object) -> str:
    return str(value or "")


def int_value(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def tuple_value(value: object) -> tuple[str, ...]:
    if isinstance(value, str) or value is None:
        return (value,) if value else ()
    if not isinstance(value, Iterable):
        return ()
    return tuple(str(item) for item in value if str(item or "").strip())


def quote_query_value(value: str) -> str:
    from urllib.parse import quote_plus

    return quote_plus(value)
