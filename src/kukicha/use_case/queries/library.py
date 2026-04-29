from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from sqlite3 import Connection, Row

from ..database import connect_database
from ..library import split_genres_and_styles
from ...models import TrackArtwork, normalize_genre_values
from .filters import (
    album_where_clause,
    expanded_album_list_query,
    playlist_matches_search,
    playlist_query_can_match,
    playlist_where_clause,
)
from .models import (
    ALBUM_LIST_SORT_ARTIST,
    ALBUM_LIST_SORT_RECENTLY_ADDED,
    AlbumDetails,
    AlbumListQuery,
    AlbumNotFoundError,
    AlbumPage,
    AlbumSummary,
    GenreFilterGroup,
    GenreStyleFilter,
    LibraryFilterOptions,
    LibraryRootFilterOption,
    PlaylistDetails,
    PlaylistItem,
    PlaylistItemNotFoundError,
    PlaylistNotFoundError,
    PlaylistTrack,
    TrackNotFoundError,
    normalize_match,
    normalized_int_tuple,
)
from .sorting import album_page_sort_key, playlist_track_sort_key
from .sql import (
    TRACK_COLUMNS,
    placeholders_for,
    root_scope_clause,
)


class LibraryQueries:
    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)

    def expand_album_list_query(self, query: AlbumListQuery) -> AlbumListQuery:
        with connect_database(self.database, create=False) as connection:
            return expanded_album_list_query(connection, query)

    def list_album_page(
        self,
        query: AlbumListQuery,
        *,
        include_track_ids: bool = True,
    ) -> AlbumPage:
        with connect_database(self.database, create=False) as connection:
            query = expanded_album_list_query(connection, query)
            page_number = query.page
            offset = (page_number - 1) * query.per_page
            items = sorted(
                (
                    *self._album_list_items(
                        connection,
                        query,
                        include_track_ids=include_track_ids,
                    ),
                    *self._playlist_list_items(connection, query),
                ),
                key=album_page_sort_key(query.sort),
            )
            page_items = tuple(items[offset : offset + query.per_page])
            has_next = len(items) > offset + query.per_page
        return AlbumPage(
            items=page_items,
            page=page_number,
            per_page=query.per_page,
            has_next=has_next,
        )

    def _album_list_items(
        self,
        connection: Connection,
        query: AlbumListQuery,
        *,
        include_track_ids: bool,
    ) -> tuple[AlbumSummary, ...]:
        if query.is_playlist is True:
            return ()
        where_sql, params = album_where_clause(query)
        rows = list(
            connection.execute(
                f"""
                SELECT
                    albums.album_id,
                    albums.artist,
                    albums.album,
                    albums.year,
                    albums.track_count,
                    albums.file_created_at
                FROM library_albums AS albums
                {where_sql}
                """,
                params,
            )
        )
        return self._album_summaries_from_rows(
            connection,
            rows,
            root_positions=query.root_positions,
            include_track_ids=include_track_ids,
        )

    def _playlist_list_items(
        self,
        connection: Connection,
        query: AlbumListQuery,
    ) -> tuple[AlbumSummary, ...]:
        if query.is_playlist is False or not playlist_query_can_match(query):
            return ()
        where_sql, params = playlist_where_clause(query)
        rows = [
            row
            for row in connection.execute(
                f"""
                SELECT
                    playlists.playlist_id,
                    playlists.root_position,
                    playlists.path,
                    playlists.name,
                    playlists.cover_svg,
                    playlists.file_created_at,
                    COUNT(items.playlist_item_id) AS item_count
                FROM library_playlists AS playlists
                LEFT JOIN library_playlist_items AS items
                    ON items.playlist_id = playlists.playlist_id
                {where_sql}
                GROUP BY
                    playlists.playlist_id,
                    playlists.root_position,
                    playlists.path,
                    playlists.name,
                    playlists.cover_svg,
                    playlists.file_created_at
                """,
                params,
            )
            if playlist_matches_search(row, query.search)
        ]
        return tuple(
            AlbumSummary(
                album_id=playlist_album_id(int(row["playlist_id"])),
                artist="Playlist",
                album=str(row["name"]),
                year=None,
                track_count=int(row["item_count"]),
                file_created_at=row["file_created_at"],
                is_playlist=True,
                playlist_id=int(row["playlist_id"]),
                path=str(row["path"]),
                cover_svg=str(row["cover_svg"] or ""),
            )
            for row in rows
        )

    def get_album(
        self,
        album_id: str,
        *,
        root_positions: Iterable[int] = (),
    ) -> AlbumDetails:
        root_positions = normalized_int_tuple(root_positions)
        with connect_database(self.database, create=False) as connection:
            row = connection.execute(
                """
                SELECT album_id, artist, album, year, track_count, file_created_at
                FROM library_albums
                WHERE album_id = ?
                """,
                (album_id,),
            ).fetchone()
            if row is None:
                raise AlbumNotFoundError(album_id)
            root_sql, root_params = root_scope_clause("library_tracks", root_positions)
            track_rows = list(
                connection.execute(
                    f"""
                    SELECT {TRACK_COLUMNS}
                    FROM library_tracks
                    WHERE album_id = ?{root_sql}
                    ORDER BY track_id
                    """,
                    [album_id, *root_params],
                )
            )
            if root_positions and not track_rows:
                raise AlbumNotFoundError(album_id)
            track_ids = [int(track_row["track_id"]) for track_row in track_rows]
            genres_by_track = track_values_by_track(
                connection,
                track_ids,
                table="library_track_genres",
                column="genre",
            )
            styles_by_track = track_values_by_track(
                connection,
                track_ids,
                table="library_track_styles",
                column="style",
            )
            track_ids_with_cover = track_ids_with_artwork(connection, track_ids)
            tracks = tuple(
                sorted(
                    self._playlist_tracks_from_rows(
                        connection,
                        track_rows,
                        genres_by_track=genres_by_track,
                        styles_by_track=styles_by_track,
                        track_ids_with_cover=track_ids_with_cover,
                        album_ids_with_cover=(
                            {album_id} if track_ids_with_cover else set()
                        ),
                    ),
                    key=playlist_track_sort_key,
                )
            )
            paths = tuple(
                str(track_row["path"])
                for track_row in sorted(
                    track_rows,
                    key=lambda track_row: (
                        str(track_row["path"]).casefold(),
                        int(track_row["track_id"]),
                    ),
                )
            )
        return AlbumDetails(
            album_id=str(row["album_id"]),
            artist=str(row["artist"]),
            album=str(row["album"]),
            year=int(row["year"]) if row["year"] is not None else None,
            track_count=len(track_rows) if root_positions else int(row["track_count"]),
            file_created_at=row["file_created_at"],
            genres=album_values_from_track_values(genres_by_track),
            styles=album_values_from_track_values(styles_by_track),
            has_cover=bool(track_ids_with_cover),
            is_compilation=any(bool(track_row["is_compilation"]) for track_row in track_rows),
            is_work=any(
                bool(track_row["work"] or track_row["grouping"])
                for track_row in track_rows
            ),
            art_track_id=min(track_ids) if track_ids else None,
            track_ids=tuple(track.track_id for track in tracks if track.track_id is not None),
            paths=paths,
            tracks=tracks,
        )

    def get_playlist(self, playlist_id: int) -> PlaylistDetails:
        with connect_database(self.database, create=False) as connection:
            row = connection.execute(
                """
                SELECT playlist_id, root_position, path, name, cover_svg
                FROM library_playlists
                WHERE playlist_id = ?
                """,
                (playlist_id,),
            ).fetchone()
            if row is None:
                raise PlaylistNotFoundError(playlist_id)
            items = self._playlist_items(
                connection,
                playlist_id,
                playlist_name=str(row["name"]),
                playlist_cover_svg=str(row["cover_svg"] or ""),
            )
        return PlaylistDetails(
            playlist_id=int(row["playlist_id"]),
            root_position=(
                int(row["root_position"])
                if row["root_position"] is not None
                else None
            ),
            path=str(row["path"]),
            name=str(row["name"]),
            cover_svg=str(row["cover_svg"] or ""),
            items=tuple(items),
        )

    def get_playlist_item(self, playlist_item_id: int) -> PlaylistItem:
        items = self.get_playlist_items_by_ids((playlist_item_id,))
        if not items:
            raise PlaylistItemNotFoundError(playlist_item_id)
        return items[0]

    def get_playlist_items_by_ids(
        self,
        playlist_item_ids: Iterable[int],
    ) -> tuple[PlaylistItem, ...]:
        requested_ids = [int(item_id) for item_id in playlist_item_ids]
        if not requested_ids:
            return ()
        with connect_database(self.database, create=False) as connection:
            placeholders = placeholders_for(requested_ids)
            rows = list(
                connection.execute(
                    f"""
                    SELECT
                        items.playlist_item_id,
                        items.playlist_id,
                        items.position,
                        items.path,
                        items.track_id,
                        items.title,
                        items.duration_seconds,
                        items.genre,
                        items.cover_url,
                        playlists.name AS playlist_name,
                        playlists.cover_svg AS playlist_cover_svg
                    FROM library_playlist_items AS items
                    JOIN library_playlists AS playlists
                        ON playlists.playlist_id = items.playlist_id
                    WHERE items.playlist_item_id IN ({placeholders})
                    """,
                    requested_ids,
                )
            )
            items = self._playlist_items_from_rows(connection, rows)
        items_by_id = {item.playlist_item_id: item for item in items}
        return tuple(
            item
            for item_id in requested_ids
            if (item := items_by_id.get(item_id)) is not None
        )

    def get_track(self, track_id: int) -> PlaylistTrack:
        with connect_database(self.database, create=False) as connection:
            row = connection.execute(
                f"""
                SELECT {TRACK_COLUMNS}
                FROM library_tracks
                WHERE track_id = ?
                """,
                (track_id,),
            ).fetchone()
            if row is None:
                raise TrackNotFoundError(track_id)
            return self._playlist_tracks_from_rows(connection, [row])[0]

    def get_tracks_by_ids(self, track_ids: Iterable[int]) -> tuple[PlaylistTrack, ...]:
        requested_ids = [int(track_id) for track_id in track_ids]
        if not requested_ids:
            return ()
        with connect_database(self.database, create=False) as connection:
            placeholders = placeholders_for(requested_ids)
            rows = list(
                connection.execute(
                    f"""
                    SELECT {TRACK_COLUMNS}
                    FROM library_tracks
                    WHERE track_id IN ({placeholders})
                    """,
                    requested_ids,
                )
            )
            tracks_by_id = {
                track.track_id: track
                for track in self._playlist_tracks_from_rows(connection, rows)
                if track.track_id is not None
            }
        return tuple(
            track
            for track_id in requested_ids
            if (track := tracks_by_id.get(track_id)) is not None
        )

    def get_track_audio_path(self, track_id: int) -> Path:
        with connect_database(self.database, create=False) as connection:
            row = connection.execute(
                "SELECT path FROM library_tracks WHERE track_id = ?",
                (track_id,),
            ).fetchone()
        if row is None:
            raise TrackNotFoundError(track_id)
        return Path(str(row["path"]))

    def get_playlist_item_audio_path(self, playlist_item_id: int) -> Path:
        item = self.get_playlist_item(playlist_item_id)
        return Path(item.path)

    def get_track_artwork(self, track_id: int, *, height_px: int) -> TrackArtwork | None:
        with connect_database(self.database, create=False) as connection:
            row = connection.execute(
                """
                SELECT mime_type, data
                FROM library_track_artwork
                WHERE track_id = ? AND height_px = ?
                """,
                (track_id, height_px),
            ).fetchone()
        if row is None:
            return None
        return TrackArtwork(
            mime_type=str(row["mime_type"]),
            data=bytes(row["data"]),
        )

    def filter_options(self) -> LibraryFilterOptions:
        with connect_database(self.database, create=False) as connection:
            roots = library_root_options(connection)
            artists = unique_sorted(
                str(row["artist"])
                for row in connection.execute(
                    """
                    SELECT DISTINCT artist
                    FROM library_albums
                    WHERE artist != ''
                    ORDER BY artist COLLATE NOCASE
                    """
                )
            )
            genre_values = unique_sorted(
                str(row["genre"])
                for row in connection.execute(
                    """
                    SELECT DISTINCT genre
                    FROM library_track_genres
                    ORDER BY genre COLLATE NOCASE
                    """
                )
            )
            styles_by_parent: dict[str, list[str]] = {}
            loose_styles: list[str] = []
            for row in connection.execute(
                """
                SELECT DISTINCT
                    library_track_styles.style,
                    taxonomy_styles.parent_genre
                FROM library_track_styles
                LEFT JOIN taxonomy_styles
                    ON taxonomy_styles.style = library_track_styles.style
                ORDER BY library_track_styles.style COLLATE NOCASE
                """
            ):
                style = str(row["style"])
                parent = str(row["parent_genre"]) if row["parent_genre"] else ""
                if parent:
                    styles_by_parent.setdefault(parent, []).append(style)
                else:
                    loose_styles.append(style)
        genre_groups = tuple(
            GenreFilterGroup(
                genre=genre,
                styles=unique_sorted(styles_by_parent.get(genre, ())),
            )
            for genre in genre_values
        )
        genre_keys = {normalize_match(value) for value in genre_values}
        extra_parent_groups = tuple(
            GenreFilterGroup(genre=genre, styles=unique_sorted(styles))
            for genre, styles in sorted(
                styles_by_parent.items(),
                key=lambda item: item[0].casefold(),
            )
            if normalize_match(genre) not in genre_keys
        )
        return LibraryFilterOptions(
            roots=roots,
            artists=artists,
            genre_groups=genre_groups + extra_parent_groups,
            loose_styles=unique_sorted(loose_styles),
        )

    def library_roots(self) -> tuple[LibraryRootFilterOption, ...]:
        with connect_database(self.database, create=False) as connection:
            return library_root_options(connection)

    def _playlist_items(
        self,
        connection: Connection,
        playlist_id: int,
        *,
        playlist_name: str = "",
        playlist_cover_svg: str = "",
    ) -> list[PlaylistItem]:
        rows = list(
            connection.execute(
                """
                SELECT
                    playlist_item_id,
                    playlist_id,
                    position,
                    path,
                    track_id,
                    title,
                    duration_seconds,
                    genre,
                    cover_url
                FROM library_playlist_items
                WHERE playlist_id = ?
                ORDER BY position
                """,
                (playlist_id,),
            )
        )
        return self._playlist_items_from_rows(
            connection,
            rows,
            playlist_name=playlist_name,
            playlist_cover_svg=playlist_cover_svg,
        )

    def _playlist_items_from_rows(
        self,
        connection: Connection,
        rows: Iterable[Row],
        *,
        playlist_name: str = "",
        playlist_cover_svg: str = "",
    ) -> list[PlaylistItem]:
        item_rows = list(rows)
        track_ids = [
            int(row["track_id"])
            for row in item_rows
            if row["track_id"] is not None
        ]
        tracks_by_id: dict[int, PlaylistTrack] = {}
        if track_ids:
            placeholders = placeholders_for(track_ids)
            track_rows = list(
                connection.execute(
                    f"""
                    SELECT {TRACK_COLUMNS}
                    FROM library_tracks
                    WHERE track_id IN ({placeholders})
                    """,
                    track_ids,
                )
            )
            tracks_by_id = {
                track.track_id: track
                for track in self._playlist_tracks_from_rows(connection, track_rows)
                if track.track_id is not None
            }
        items: list[PlaylistItem] = []
        for row in item_rows:
            track_id = int(row["track_id"]) if row["track_id"] is not None else None
            row_playlist_name = (
                str(row["playlist_name"])
                if "playlist_name" in row.keys() and row["playlist_name"] is not None
                else playlist_name
            )
            row_playlist_cover_svg = (
                str(row["playlist_cover_svg"])
                if "playlist_cover_svg" in row.keys()
                and row["playlist_cover_svg"] is not None
                else playlist_cover_svg
            )
            items.append(
                PlaylistItem(
                    playlist_item_id=int(row["playlist_item_id"]),
                    playlist_id=int(row["playlist_id"]),
                    position=int(row["position"]),
                    path=str(row["path"]),
                    playlist_name=row_playlist_name,
                    track_id=track_id,
                    track=tracks_by_id.get(track_id) if track_id is not None else None,
                    title=row["title"],
                    duration_seconds=row["duration_seconds"],
                    genre=row["genre"],
                    cover_url=row["cover_url"],
                    playlist_cover_svg=row_playlist_cover_svg,
                )
            )
        return items

    def _tracks_for_album(
        self,
        connection: Connection,
        album_id: str,
        *,
        root_positions: tuple[int, ...] = (),
    ) -> list[PlaylistTrack]:
        root_sql, root_params = root_scope_clause("library_tracks", root_positions)
        rows = list(
            connection.execute(
                f"""
                SELECT {TRACK_COLUMNS}
                FROM library_tracks
                WHERE album_id = ?{root_sql}
                ORDER BY track_id
                """,
                [album_id, *root_params],
            )
        )
        return self._playlist_tracks_from_rows(connection, rows)

    def _album_summaries_from_rows(
        self,
        connection: Connection,
        rows: Iterable[Row],
        *,
        root_positions: tuple[int, ...] = (),
        include_track_ids: bool = True,
    ) -> tuple[AlbumSummary, ...]:
        album_rows = list(rows)
        album_ids = [str(row["album_id"]) for row in album_rows]
        genres_by_album = album_values_by_album(
            connection,
            album_ids,
            table="library_track_genres",
            column="genre",
            root_positions=root_positions,
        )
        styles_by_album = album_values_by_album(
            connection,
            album_ids,
            table="library_track_styles",
            column="style",
            root_positions=root_positions,
        )
        flags_by_album = album_flags_by_album(
            connection,
            album_ids,
            root_positions=root_positions,
        )
        art_track_ids = album_art_track_ids(
            connection,
            album_ids,
            root_positions=root_positions,
        )
        track_ids_by_album = (
            album_track_ids_by_album(
                connection,
                album_ids,
                root_positions=root_positions,
            )
            if include_track_ids
            else {}
        )
        track_counts = album_track_counts_by_album(
            connection,
            album_ids,
            root_positions=root_positions,
        )
        return tuple(
            AlbumSummary(
                album_id=str(row["album_id"]),
                artist=str(row["artist"]),
                album=str(row["album"]),
                year=int(row["year"]) if row["year"] is not None else None,
                track_count=track_counts.get(
                    str(row["album_id"]),
                    int(row["track_count"]),
                ),
                file_created_at=row["file_created_at"],
                genres=genres_by_album.get(str(row["album_id"]), ()),
                styles=styles_by_album.get(str(row["album_id"]), ()),
                has_cover=flags_by_album.get(str(row["album_id"]), {}).get("has_cover", False),
                is_compilation=flags_by_album.get(str(row["album_id"]), {}).get(
                    "is_compilation",
                    False,
                ),
                is_work=flags_by_album.get(str(row["album_id"]), {}).get("is_work", False),
                art_track_id=art_track_ids.get(str(row["album_id"])),
                track_ids=track_ids_by_album.get(str(row["album_id"]), ()),
            )
            for row in album_rows
        )

    def _playlist_tracks_from_rows(
        self,
        connection: Connection,
        rows: Iterable[Row],
        *,
        genres_by_track: dict[int, list[str]] | None = None,
        styles_by_track: dict[int, list[str]] | None = None,
        track_ids_with_cover: set[int] | None = None,
        album_ids_with_cover: set[str] | None = None,
    ) -> list[PlaylistTrack]:
        track_rows = list(rows)
        track_ids = [int(row["track_id"]) for row in track_rows]
        album_ids = [
            str(row["album_id"])
            for row in track_rows
            if row["album_id"] is not None and str(row["album_id"])
        ]
        if genres_by_track is None:
            genres_by_track = track_values_by_track(
                connection,
                track_ids,
                table="library_track_genres",
                column="genre",
            )
        if styles_by_track is None:
            styles_by_track = track_values_by_track(
                connection,
                track_ids,
                table="library_track_styles",
                column="style",
            )
        taxonomy_genres, taxonomy_styles = taxonomy_sets(connection)
        track_ids_with_playlist_membership: set[int] = set()
        if track_ids:
            placeholders = placeholders_for(track_ids)
            track_ids_with_playlist_membership = set(
                int(row["track_id"])
                for row in connection.execute(
                    f"""
                    SELECT DISTINCT track_id
                    FROM library_playlist_items
                    WHERE track_id IN ({placeholders})
                    """,
                    track_ids,
                )
            )
        if track_ids_with_cover is None:
            track_ids_with_cover = track_ids_with_artwork(connection, track_ids)
        if album_ids_with_cover is None and album_ids:
            placeholders = placeholders_for(album_ids)
            album_ids_with_cover = set(
                str(row["album_id"])
                for row in connection.execute(
                    f"""
                    SELECT DISTINCT library_tracks.album_id
                    FROM library_tracks
                    JOIN library_track_artwork
                        ON library_track_artwork.track_id = library_tracks.track_id
                    WHERE library_tracks.album_id IN ({placeholders})
                    """,
                    album_ids,
                )
            )
        elif album_ids_with_cover is None:
            album_ids_with_cover = set()
        tracks: list[PlaylistTrack] = []
        for row in track_rows:
            track_id = int(row["track_id"])
            album_id = str(row["album_id"]) if row["album_id"] else None
            genres, styles = split_genres_and_styles(
                normalize_genre_values(genres_by_track.get(track_id, [])),
                normalize_genre_values(styles_by_track.get(track_id, [])),
                taxonomy_genres=taxonomy_genres,
                taxonomy_styles=taxonomy_styles,
            )
            tracks.append(
                PlaylistTrack(
                    track_id=track_id,
                    album_id=album_id,
                    root_position=(
                        int(row["root_position"])
                        if row["root_position"] is not None
                        else None
                    ),
                    path=str(row["path"]),
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
                    track_number=row["track_number"],
                    disc_number=row["disc_number"],
                    date=row["date"],
                    genres=tuple(genres),
                    styles=tuple(styles),
                    has_cover=(
                        track_id in track_ids_with_cover
                        or bool(album_id and album_id in album_ids_with_cover)
                    ),
                    is_compilation=bool(row["is_compilation"]),
                    duration_seconds=row["duration_seconds"],
                    bitrate=row["bitrate"],
                    has_playlist_membership=track_id in track_ids_with_playlist_membership,
                )
            )
        return tracks


def album_values_by_album(
    connection: Connection,
    album_ids: list[str],
    *,
    table: str,
    column: str,
    root_positions: tuple[int, ...] = (),
) -> dict[str, tuple[str, ...]]:
    if not album_ids:
        return {}
    placeholders = placeholders_for(album_ids)
    root_sql, root_params = root_scope_clause("tracks", root_positions)
    values: dict[str, list[str]] = {}
    for row in connection.execute(
        f"""
        SELECT tracks.album_id, track_values.{column}
        FROM library_tracks AS tracks
        JOIN {table} AS track_values
            ON track_values.track_id = tracks.track_id
        WHERE tracks.album_id IN ({placeholders}){root_sql}
        ORDER BY tracks.album_id, track_values.position
        """,
        [*album_ids, *root_params],
    ):
        values.setdefault(str(row["album_id"]), []).append(str(row[column]))
    return {
        album_id: unique_sorted(album_values)
        for album_id, album_values in values.items()
    }


def album_flags_by_album(
    connection: Connection,
    album_ids: list[str],
    *,
    root_positions: tuple[int, ...] = (),
) -> dict[str, dict[str, bool]]:
    if not album_ids:
        return {}
    placeholders = placeholders_for(album_ids)
    root_sql, root_params = root_scope_clause("library_tracks", root_positions)
    flags = {
        str(row["album_id"]): {
            "is_compilation": bool(row["is_compilation"]),
            "is_work": bool(row["is_work"]),
            "has_cover": False,
        }
        for row in connection.execute(
            f"""
            SELECT
                album_id,
                MAX(is_compilation) AS is_compilation,
                MAX(
                    CASE
                        WHEN COALESCE(work, '') != '' OR COALESCE(grouping, '') != ''
                        THEN 1
                        ELSE 0
                    END
                ) AS is_work
            FROM library_tracks
            WHERE album_id IN ({placeholders}){root_sql}
            GROUP BY album_id
            """,
            [*album_ids, *root_params],
        )
    }
    root_sql, root_params = root_scope_clause("tracks", root_positions)
    for row in connection.execute(
        f"""
        SELECT DISTINCT tracks.album_id
        FROM library_tracks AS tracks
        JOIN library_track_artwork AS artwork
            ON artwork.track_id = tracks.track_id
        WHERE tracks.album_id IN ({placeholders}){root_sql}
        """,
        [*album_ids, *root_params],
    ):
        flags.setdefault(str(row["album_id"]), {})["has_cover"] = True
    return flags


def album_art_track_ids(
    connection: Connection,
    album_ids: list[str],
    *,
    root_positions: tuple[int, ...] = (),
) -> dict[str, int]:
    if not album_ids:
        return {}
    placeholders = placeholders_for(album_ids)
    root_sql, root_params = root_scope_clause("library_tracks", root_positions)
    return {
        str(row["album_id"]): int(row["track_id"])
        for row in connection.execute(
            f"""
            SELECT album_id, MIN(track_id) AS track_id
            FROM library_tracks
            WHERE album_id IN ({placeholders}){root_sql}
            GROUP BY album_id
            """,
            [*album_ids, *root_params],
        )
    }


def album_track_counts_by_album(
    connection: Connection,
    album_ids: list[str],
    *,
    root_positions: tuple[int, ...] = (),
) -> dict[str, int]:
    if not album_ids or not root_positions:
        return {}
    placeholders = placeholders_for(album_ids)
    root_sql, root_params = root_scope_clause("library_tracks", root_positions)
    return {
        str(row["album_id"]): int(row["track_count"])
        for row in connection.execute(
            f"""
            SELECT album_id, COUNT(*) AS track_count
            FROM library_tracks
            WHERE album_id IN ({placeholders}){root_sql}
            GROUP BY album_id
            """,
            [*album_ids, *root_params],
        )
    }


def album_track_ids_by_album(
    connection: Connection,
    album_ids: list[str],
    *,
    root_positions: tuple[int, ...] = (),
) -> dict[str, tuple[int, ...]]:
    if not album_ids:
        return {}
    placeholders = placeholders_for(album_ids)
    root_sql, root_params = root_scope_clause("library_tracks", root_positions)
    tracks_by_album: dict[str, list[PlaylistTrack]] = {}
    for row in connection.execute(
        f"""
        SELECT
            track_id,
            album_id,
            path,
            artist,
            album_artist,
            album,
            title,
            track_number,
            disc_number
        FROM library_tracks
        WHERE album_id IN ({placeholders}){root_sql}
        ORDER BY album_id, track_id
        """,
        [*album_ids, *root_params],
    ):
        album_id = str(row["album_id"])
        tracks_by_album.setdefault(album_id, []).append(
            PlaylistTrack(
                track_id=int(row["track_id"]),
                album_id=album_id,
                path=str(row["path"]),
                artist=row["artist"],
                album_artist=row["album_artist"],
                album=row["album"],
                title=row["title"],
                track_number=row["track_number"],
                disc_number=row["disc_number"],
            )
        )
    return {
        album_id: tuple(
            track.track_id
            for track in sorted(album_tracks, key=playlist_track_sort_key)
            if track.track_id is not None
        )
        for album_id, album_tracks in tracks_by_album.items()
    }


def playlist_album_id(playlist_id: int) -> str:
    return f"playlist:{playlist_id}"


def track_values_by_track(
    connection: Connection,
    track_ids: list[int],
    *,
    table: str,
    column: str,
) -> dict[int, list[str]]:
    if not track_ids:
        return {}
    placeholders = placeholders_for(track_ids)
    values: dict[int, list[str]] = {}
    for row in connection.execute(
        f"""
        SELECT track_id, {column}
        FROM {table}
        WHERE track_id IN ({placeholders})
        ORDER BY track_id, position
        """,
        track_ids,
    ):
        values.setdefault(int(row["track_id"]), []).append(str(row[column]))
    return values


def track_ids_with_artwork(connection: Connection, track_ids: list[int]) -> set[int]:
    if not track_ids:
        return set()
    placeholders = placeholders_for(track_ids)
    return {
        int(row["track_id"])
        for row in connection.execute(
            f"""
            SELECT DISTINCT track_id
            FROM library_track_artwork
            WHERE track_id IN ({placeholders})
            """,
            track_ids,
        )
    }


def album_values_from_track_values(
    values_by_track: dict[int, list[str]],
) -> tuple[str, ...]:
    return unique_sorted(
        value
        for track_values in values_by_track.values()
        for value in track_values
    )


def taxonomy_sets(connection: Connection) -> tuple[set[str], set[str]]:
    genres = {
        str(row["genre"]).casefold()
        for row in connection.execute("SELECT genre FROM taxonomy_genres")
    }
    styles = {
        str(row["style"]).casefold()
        for row in connection.execute("SELECT style FROM taxonomy_styles")
    }
    return genres, styles


def unique_sorted(values: Iterable[object]) -> tuple[str, ...]:
    seen: dict[str, str] = {}
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        seen.setdefault(value.casefold(), value)
    return tuple(sorted(seen.values(), key=str.casefold))


def library_root_filter_label(root_path: str) -> str:
    path = Path(root_path)
    name = path.name
    if not name:
        return root_path
    if str(path.parent) == path.anchor:
        return root_path
    return f".../{name}"


def library_root_options(connection: Connection) -> tuple[LibraryRootFilterOption, ...]:
    return tuple(
        LibraryRootFilterOption(
            position=int(row["position"]),
            path=str(row["root_path"]),
            label=library_root_filter_label(str(row["root_path"])),
        )
        for row in connection.execute(
            """
            SELECT position, root_path
            FROM library_roots
            ORDER BY position
            """
        )
    )



__all__ = [
    "ALBUM_LIST_SORT_ARTIST",
    "ALBUM_LIST_SORT_RECENTLY_ADDED",
    "AlbumDetails",
    "AlbumListQuery",
    "AlbumNotFoundError",
    "AlbumPage",
    "AlbumSummary",
    "GenreFilterGroup",
    "GenreStyleFilter",
    "LibraryQueries",
    "LibraryFilterOptions",
    "LibraryRootFilterOption",
    "PlaylistDetails",
    "PlaylistItem",
    "PlaylistItemNotFoundError",
    "PlaylistNotFoundError",
    "PlaylistTrack",
    "TrackNotFoundError",
]
