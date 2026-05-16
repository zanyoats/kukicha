from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Connection, Row

from ..database import connect_database
from ..library import split_genres_and_styles
from ...library_sources import SOURCE_KIND_LOCAL, SOURCE_KIND_S3, root_source_label
from ...media_resources import AudioResource, local_audio_resource
from ...models import ALBUM_ARTWORK_HEIGHT, TrackArtwork, normalize_genre_values
from .filters import (
    album_where_clause,
    expanded_album_list_query,
    playlist_matches_search,
    playlist_query_can_match,
    playlist_where_clause,
)
from .models import (
    ALBUM_LIST_SORT_ALBUMS,
    ALBUM_LIST_SORT_ARTIST,
    ALBUM_LIST_SORT_FREQUENT,
    ALBUM_LIST_SORT_GENRE,
    ALBUM_LIST_SORT_RECENT,
    ALBUM_LIST_SORT_RECENTLY_ADDED,
    ALBUM_LIST_SORT_STARRED,
    AlbumArtistSplitMapping,
    AlbumMusicBrainzOverride,
    AlbumDetails,
    AlbumListQuery,
    AlbumNotFoundError,
    AlbumPage,
    AlbumSummary,
    ArtistNotFoundError,
    CacheStat,
    GenreFilterGroup,
    GenreStyleFilter,
    LibraryArtistAlbum,
    LibraryArtistDetails,
    LibraryArtistSummary,
    LibraryFilterOptions,
    LibraryAlbumArtistStats,
    LibraryGenre,
    LibraryRootAlbumArtistStats,
    LibraryRootFilterOption,
    LibraryRootStats,
    LibraryStats,
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


CACHE_STAT_TABLES = (
    ("MusicBrainz", "musicbrainz_entity_cache"),
    ("Cover Art Metadata", "cover_art_archive_entity_cache"),
    ("Cover Art Images", "cover_art_archive_image_cache"),
    ("iTunes Artwork", "itunes_lookup_image_cache"),
)


@dataclass(frozen=True, slots=True)
class AlbumSortColumn:
    expression: str
    alias: str
    direction: str = "ASC"


class LibraryQueries:
    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)

    def expand_album_list_query(self, query: AlbumListQuery) -> AlbumListQuery:
        with connect_database(self.database, create=False) as connection:
            return expanded_album_list_query(connection, query)

    def list_album_page(
        self,
        query: AlbumListQuery,
    ) -> AlbumPage:
        with connect_database(self.database, create=False) as connection:
            query = expanded_album_list_query(connection, query)
            if query.is_playlist is True:
                return self._playlist_list_page(connection, query)
            return self._album_list_page(connection, query)

    def _album_list_page(
        self,
        connection: Connection,
        query: AlbumListQuery,
    ) -> AlbumPage:
        where_sql, params = album_where_clause(query)
        sort_columns = album_sort_columns(query)
        order_sql = album_order_by_clause(sort_columns)
        sort_select_sql = album_sort_select_sql(sort_columns)
        play_stats_join_sql = album_play_stats_join_sql(query)
        limit = query.size + 1

        if query.root_positions:
            root_placeholders = placeholders_for(query.root_positions)
            rows = list(
                connection.execute(
                    f"""
                    WITH selected_album_roots AS (
                        SELECT
                            album_id,
                            SUM(track_count) AS track_count,
                            MIN(art_track_id) AS art_track_id,
                            COALESCE(MIN(NULLIF(genre_sort_key, '')), '') AS genre_sort_key
                        FROM library_album_roots
                        WHERE root_position IN ({root_placeholders})
                        GROUP BY album_id
                    )
                    SELECT
                        albums.album_id,
                        albums.album,
                        albums.year,
                        selected_album_roots.track_count,
                        albums.file_created_at,
                        albums.added_at,
                        albums.starred_at,
                        selected_album_roots.art_track_id
                        {sort_select_sql}
                    FROM library_albums AS albums
                    JOIN selected_album_roots
                        ON selected_album_roots.album_id = albums.album_id
                    {play_stats_join_sql}
                    {where_sql}
                    {order_sql}
                    LIMIT ? OFFSET ?
                    """,
                    [*query.root_positions, *params, limit, query.offset],
                )
            )
            return self._album_page_from_rows(
                connection,
                rows,
                query=query,
                root_positions=query.root_positions,
            )

        rows = list(
            connection.execute(
                f"""
                SELECT
                    albums.album_id,
                    albums.album,
                    albums.year,
                    albums.track_count,
                    albums.file_created_at,
                    albums.added_at,
                    albums.starred_at,
                    albums.art_track_id
                    {sort_select_sql}
                FROM library_albums AS albums
                {play_stats_join_sql}
                {where_sql}
                {order_sql}
                LIMIT ? OFFSET ?
                """,
                [*params, limit, query.offset],
            )
        )
        return self._album_page_from_rows(
            connection,
            rows,
            query=query,
        )

    def _album_page_from_rows(
        self,
        connection: Connection,
        rows: list[Row],
        *,
        query: AlbumListQuery,
        root_positions: tuple[int, ...] = (),
    ) -> AlbumPage:
        has_extra = len(rows) > query.size
        page_rows = rows[: query.size]

        return AlbumPage(
            items=self._album_summaries_from_rows(
                connection,
                page_rows,
                root_positions=root_positions,
            ),
            size=query.size,
            offset=query.offset,
            has_next=has_extra,
            has_previous=query.offset > 0,
        )

    def _playlist_list_page(
        self,
        connection: Connection,
        query: AlbumListQuery,
    ) -> AlbumPage:
        items = tuple(
            sorted(
                self._playlist_list_items(connection, query),
                key=album_page_sort_key(query.sort),
            )
        )
        return AlbumPage(
            items=items,
            size=query.size,
            offset=0,
        )

    def _playlist_list_items(
        self,
        connection: Connection,
        query: AlbumListQuery,
    ) -> tuple[AlbumSummary, ...]:
        if query.is_playlist is not True or not playlist_query_can_match(query):
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
                SELECT album_id, album, year, track_count, file_created_at, added_at, starred_at
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
            track_ids_with_album_artwork = track_ids_with_artwork(
                connection,
                track_ids,
                height_px=ALBUM_ARTWORK_HEIGHT,
            )
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
            album_artists = album_artists_by_album(connection, (album_id,)).get(
                album_id,
                (),
            )
            artist = album_artist_display_text(album_artists)
        return AlbumDetails(
            album_id=str(row["album_id"]),
            artist=artist,
            album=str(row["album"]),
            year=int(row["year"]) if row["year"] is not None else None,
            track_count=len(track_rows) if root_positions else int(row["track_count"]),
            album_artists=album_artists,
            file_created_at=row["file_created_at"],
            added_at=row["added_at"],
            starred_at=row["starred_at"],
            genres=album_values_from_track_values(genres_by_track),
            styles=album_values_from_track_values(styles_by_track),
            has_cover=bool(track_ids_with_album_artwork),
            is_compilation=any(bool(track_row["is_compilation"]) for track_row in track_rows),
            is_work=any(
                bool(track_row["work"] or track_row["grouping"])
                for track_row in track_rows
            ),
            art_track_id=(
                min(track_ids_with_album_artwork)
                if track_ids_with_album_artwork
                else None
            ),
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
                        items.duration_is_indeterminate,
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

    def get_track_audio_resource(self, track_id: int) -> AudioResource:
        with connect_database(self.database, create=False) as connection:
            row = connection.execute(
                """
                SELECT
                    tracks.path,
                    COALESCE(sources.source_kind, roots.kind, 'local') AS source_kind,
                    COALESCE(roots.source_json, '{}') AS source_json,
                    sources.object_key,
                    sources.content_type,
                    COALESCE(sources.size_bytes, tracks.file_size_bytes) AS size_bytes
                FROM library_tracks AS tracks
                LEFT JOIN library_track_sources AS sources
                    ON sources.track_id = tracks.track_id
                LEFT JOIN library_roots AS roots
                    ON roots.position = tracks.root_position
                WHERE tracks.track_id = ?
                """,
                (track_id,),
            ).fetchone()
        if row is None:
            raise TrackNotFoundError(track_id)
        kind = str(row["source_kind"] or SOURCE_KIND_LOCAL)
        if kind != SOURCE_KIND_S3:
            return local_audio_resource(str(row["path"]))
        return AudioResource(
            kind=SOURCE_KIND_S3,
            path=str(row["path"]),
            source_json=str(row["source_json"] or "{}"),
            object_key=str(row["object_key"]) if row["object_key"] else None,
            content_type=str(row["content_type"]) if row["content_type"] else None,
            size_bytes=int(row["size_bytes"]) if row["size_bytes"] is not None else None,
        )

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
                    FROM library_album_artists
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
                    FROM library_album_genres
                    ORDER BY genre COLLATE NOCASE
                    """
                )
            )
            styles_by_parent: dict[str, list[str]] = {}
            loose_styles: list[str] = []
            for row in connection.execute(
                """
                SELECT DISTINCT
                    library_album_styles.style,
                    taxonomy_styles.parent_genre
                FROM library_album_styles
                LEFT JOIN taxonomy_styles
                    ON taxonomy_styles.style = library_album_styles.style
                ORDER BY library_album_styles.style COLLATE NOCASE
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

    def list_genres(self) -> tuple[LibraryGenre, ...]:
        with connect_database(self.database, create=False) as connection:
            rows = list(
                connection.execute(
                    """
                    SELECT
                        album_genres.genre,
                        COUNT(DISTINCT tracks.track_id) AS song_count,
                        COUNT(DISTINCT album_genres.album_id) AS album_count
                    FROM library_album_genres AS album_genres
                    LEFT JOIN library_tracks AS tracks
                        ON tracks.album_id = album_genres.album_id
                    WHERE album_genres.genre != ''
                    GROUP BY album_genres.genre
                    ORDER BY album_genres.genre COLLATE NOCASE
                    """
                )
            )
        return tuple(
            LibraryGenre(
                value=str(row["genre"]),
                song_count=int(row["song_count"]),
                album_count=int(row["album_count"]),
            )
            for row in rows
        )

    def list_album_artists(
        self,
        *,
        root_position: int | None = None,
    ) -> tuple[LibraryArtistSummary, ...]:
        with connect_database(self.database, create=False) as connection:
            if root_position is None:
                rows = list(
                    connection.execute(
                        """
                        SELECT album_artist, albums_scanned
                        FROM library_album_artist_stats
                        WHERE COALESCE(album_artist, '') != ''
                        ORDER BY album_artist COLLATE NOCASE
                        """
                    )
                )
            else:
                rows = list(
                    connection.execute(
                        """
                        SELECT album_artist, albums_scanned
                        FROM library_root_album_artist_stats
                        WHERE root_position = ?
                            AND COALESCE(album_artist, '') != ''
                        ORDER BY album_artist COLLATE NOCASE
                        """,
                        (root_position,),
                    )
                )
            cover_album_ids = album_artist_cover_album_ids(
                connection,
                (str(row["album_artist"]) for row in rows),
                root_position=root_position,
            )
        return tuple(
            LibraryArtistSummary(
                artist=str(row["album_artist"]),
                album_count=int(row["albums_scanned"]),
                cover_album_id=cover_album_ids.get(str(row["album_artist"]).casefold()),
            )
            for row in rows
        )

    def get_album_artist(self, artist: str) -> LibraryArtistDetails:
        with connect_database(self.database, create=False) as connection:
            artist_row = connection.execute(
                """
                SELECT album_artist, albums_scanned
                FROM library_album_artist_stats
                WHERE album_artist = ? COLLATE NOCASE
                """,
                (artist,),
            ).fetchone()
            if artist_row is None:
                raise ArtistNotFoundError(artist)
            artist_name = str(artist_row["album_artist"])
            cover_album_ids = album_artist_cover_album_ids(
                connection,
                (artist_name,),
            )
            rows = list(
                connection.execute(
                    """
                    SELECT
                        albums.album_id,
                        albums.album,
                        albums.year,
                        albums.track_count,
                        albums.file_created_at,
                        albums.added_at,
                        albums.starred_at,
                        albums.art_track_id,
                        COALESCE(
                            (
                                SELECT SUM(COALESCE(tracks.duration_seconds, 0))
                                FROM library_tracks AS tracks
                                WHERE tracks.album_id = albums.album_id
                            ),
                            0
                        ) AS duration,
                        (
                            SELECT genres.genre
                            FROM library_album_genres AS genres
                            WHERE genres.album_id = albums.album_id
                            ORDER BY genres.genre COLLATE NOCASE
                            LIMIT 1
                        ) AS genre
                    FROM library_albums AS albums
                    JOIN library_album_artists AS artists
                        ON artists.album_id = albums.album_id
                    WHERE artists.artist = ? COLLATE NOCASE
                    ORDER BY albums.rowid
                    """,
                    (artist_name,),
                )
            )
            artists_by_album = album_artists_by_album(
                connection,
                (str(row["album_id"]) for row in rows),
            )
        albums = tuple(
            LibraryArtistAlbum(
                album_id=str(row["album_id"]),
                artist=album_artist_display_text(
                    artists_by_album.get(str(row["album_id"]), ())
                ),
                album=str(row["album"]),
                year=int(row["year"]) if row["year"] is not None else None,
                track_count=int(row["track_count"]),
                album_artists=artists_by_album.get(str(row["album_id"]), ()),
                file_created_at=row["file_created_at"],
                added_at=row["added_at"],
                starred_at=row["starred_at"],
                art_track_id=(
                    int(row["art_track_id"])
                    if row["art_track_id"] is not None
                    else None
                ),
                duration_seconds=int(row["duration"] or 0),
                genre=str(row["genre"]) if row["genre"] is not None else None,
                has_cover=row["art_track_id"] is not None,
            )
            for row in rows
        )
        return LibraryArtistDetails(
            artist=artist_name,
            album_count=int(artist_row["albums_scanned"]),
            cover_album_id=cover_album_ids.get(artist_name.casefold()),
            albums=albums,
        )

    def album_artist_split_mappings(self) -> tuple[AlbumArtistSplitMapping, ...]:
        with connect_database(self.database, create=False) as connection:
            rows = list(
                connection.execute(
                    """
                    SELECT album_artist, mapped_artists
                    FROM album_artist_split_mappings
                    ORDER BY album_artist COLLATE NOCASE
                    """
                )
            )
        return tuple(
            AlbumArtistSplitMapping(
                album_artist=str(row["album_artist"]),
                mapped_artists=mapped_artist_lines(str(row["mapped_artists"])),
            )
            for row in rows
        )

    def album_musicbrainz_overrides(self) -> tuple[AlbumMusicBrainzOverride, ...]:
        with connect_database(self.database, create=False) as connection:
            rows = list(
                connection.execute(
                    """
                    WITH resolved_links AS (
                        SELECT
                            links.file_album_id,
                            COALESCE(
                                (
                                    SELECT tracks.album_id
                                    FROM album_musicbrainz_track_links AS track_links
                                    JOIN library_tracks AS tracks
                                        ON tracks.path = track_links.path
                                    WHERE track_links.file_album_id = links.file_album_id
                                        AND COALESCE(track_links.release_mbid, '') =
                                            COALESCE(NULLIF(TRIM(links.release_mbid), ''), '')
                                        AND COALESCE(track_links.release_group_mbid, '') =
                                            COALESCE(NULLIF(TRIM(links.release_group_mbid), ''), '')
                                        AND COALESCE(tracks.album_id, '') != ''
                                    GROUP BY tracks.album_id
                                    ORDER BY COUNT(*) DESC, tracks.album_id
                                    LIMIT 1
                                ),
                                (
                                    SELECT candidate.album_id
                                    FROM library_albums AS candidate
                                    WHERE candidate.album_id = links.file_album_id
                                        OR candidate.album_id LIKE links.file_album_id || '::___'
                                    ORDER BY candidate.album_id = links.file_album_id DESC,
                                        candidate.album_id
                                    LIMIT 1
                                ),
                                links.file_album_id
                            ) AS album_id,
                            NULLIF(TRIM(links.release_mbid), '') AS release_mbid,
                            NULLIF(TRIM(links.release_group_mbid), '') AS release_group_mbid
                        FROM album_musicbrainz_links AS links
                        WHERE
                            COALESCE(TRIM(links.release_mbid), '') != ''
                            OR COALESCE(TRIM(links.release_group_mbid), '') != ''
                    )
                    SELECT
                        resolved_links.file_album_id,
                        resolved_links.album_id,
                        resolved_links.release_mbid,
                        resolved_links.release_group_mbid,
                        albums.album,
                        albums.year
                    FROM resolved_links
                    LEFT JOIN library_albums AS albums
                        ON albums.album_id = resolved_links.album_id
                    """
                )
            )
            artists_by_album = album_artists_by_album(
                connection,
                (
                    str(row["album_id"])
                    for row in rows
                    if row["album"] is not None
                ),
            )

        overrides = [
            AlbumMusicBrainzOverride(
                album_id=album_id,
                album=str(row["album"]) if row["album"] is not None else album_id,
                artist=(
                    album_artist_display_text(artists_by_album.get(album_id, ()))
                    if row["album"] is not None
                    else ""
                ),
                year=int(row["year"]) if row["year"] is not None else None,
                release_mbid=(
                    str(row["release_mbid"])
                    if row["release_mbid"] is not None
                    else None
                ),
                release_group_mbid=(
                    str(row["release_group_mbid"])
                    if row["release_group_mbid"] is not None
                    else None
                ),
                is_current_album=row["album"] is not None,
            )
            for row in rows
            for album_id in (str(row["album_id"]),)
        ]
        return tuple(
            sorted(
                overrides,
                key=lambda item: (
                    item.release_group_mbid is None,
                    item.release_group_mbid or "",
                    item.release_mbid is None,
                    item.release_mbid or "",
                    item.album_id.casefold(),
                ),
            )
        )

    def cache_stats(self) -> tuple[CacheStat, ...]:
        with connect_database(self.database, create=False) as connection:
            return tuple(
                CacheStat(
                    label=label,
                    count=int(
                        connection.execute(
                            f"SELECT COUNT(*) AS count FROM {table_name}"
                        ).fetchone()["count"]
                    ),
                )
                for label, table_name in CACHE_STAT_TABLES
            )

    def library_stats(self) -> LibraryStats:
        with connect_database(self.database, create=False) as connection:
            stats_row = connection.execute(
                """
                SELECT
                    tracks_scanned,
                    albums_scanned,
                    playlists_scanned
                FROM library_stats
                WHERE stats_id = 1
                """
            ).fetchone()
            artist_rows = list(
                connection.execute(
                    """
                    SELECT
                        album_artist,
                        tracks_scanned,
                        albums_scanned
                    FROM library_album_artist_stats
                    ORDER BY album_artist COLLATE NOCASE
                    """
                )
            )

        album_artists = tuple(
            LibraryAlbumArtistStats(
                album_artist=str(row["album_artist"]),
                tracks_scanned=int(row["tracks_scanned"]),
                albums_scanned=int(row["albums_scanned"]),
            )
            for row in artist_rows
        )
        if stats_row is None:
            return LibraryStats(
                tracks_scanned=0,
                albums_scanned=0,
                playlists_scanned=0,
                album_artists=album_artists,
            )
        return LibraryStats(
            tracks_scanned=int(stats_row["tracks_scanned"]),
            albums_scanned=int(stats_row["albums_scanned"]),
            playlists_scanned=int(stats_row["playlists_scanned"]),
            album_artists=album_artists,
        )

    def library_root_stats(self) -> tuple[LibraryRootStats, ...]:
        with connect_database(self.database, create=False) as connection:
            stats_rows = list(
                connection.execute(
                    """
                    SELECT
                        root_position,
                        tracks_scanned,
                        albums_scanned,
                        playlists_scanned
                    FROM library_root_stats
                    ORDER BY root_position
                    """
                )
            )
            artist_rows = list(
                connection.execute(
                    """
                    SELECT
                        root_position,
                        album_artist,
                        tracks_scanned,
                        albums_scanned
                    FROM library_root_album_artist_stats
                    ORDER BY root_position, album_artist COLLATE NOCASE
                    """
                )
            )

        artist_stats_by_root: dict[int, list[LibraryRootAlbumArtistStats]] = {}
        for row in artist_rows:
            root_position = int(row["root_position"])
            artist_stats_by_root.setdefault(root_position, []).append(
                LibraryRootAlbumArtistStats(
                    root_position=root_position,
                    album_artist=str(row["album_artist"]),
                    tracks_scanned=int(row["tracks_scanned"]),
                    albums_scanned=int(row["albums_scanned"]),
                )
            )

        return tuple(
            LibraryRootStats(
                root_position=int(row["root_position"]),
                tracks_scanned=int(row["tracks_scanned"]),
                albums_scanned=int(row["albums_scanned"]),
                playlists_scanned=int(row["playlists_scanned"]),
                album_artists=tuple(
                    artist_stats_by_root.get(int(row["root_position"]), ())
                ),
            )
            for row in stats_rows
        )

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
                    duration_is_indeterminate,
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
                    duration_is_indeterminate=(
                        bool(row["duration_is_indeterminate"])
                        if "duration_is_indeterminate" in row.keys()
                        else False
                    ),
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
        root_positions: Iterable[int] = (),
    ) -> tuple[AlbumSummary, ...]:
        album_rows = list(rows)
        album_ids = tuple(
            dict.fromkeys(str(row["album_id"]) for row in album_rows if row["album_id"])
        )
        artists_by_album = album_artists_by_album(
            connection,
            album_ids,
        )
        sort_genres_by_album = album_sort_genres_by_album(
            connection,
            album_ids,
            root_positions=root_positions,
        )
        summaries: list[AlbumSummary] = []
        for row in album_rows:
            album_id = str(row["album_id"])
            album_artists = artists_by_album.get(album_id, ())
            summaries.append(
                AlbumSummary(
                    album_id=album_id,
                    artist=album_artist_display_text(album_artists),
                    album=str(row["album"]),
                    year=int(row["year"]) if row["year"] is not None else None,
                    track_count=int(row["track_count"]),
                    album_artists=album_artists,
                    file_created_at=row["file_created_at"],
                    added_at=row["added_at"],
                    starred_at=row["starred_at"],
                    art_track_id=(
                        int(row["art_track_id"])
                        if row["art_track_id"] is not None
                        else None
                    ),
                    sort_genre=sort_genres_by_album.get(album_id),
                )
            )
        return tuple(summaries)

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
        artists_by_album = album_artists_by_album(connection, album_ids)
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
                    album_artists=artists_by_album.get(album_id or "", ()),
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


def playlist_album_id(playlist_id: int) -> str:
    return f"playlist:{playlist_id}"


def album_sort_columns(query: AlbumListQuery) -> tuple[AlbumSortColumn, ...]:
    artist_column = AlbumSortColumn("albums.artist_sort_key", "sort_artist_key")
    year_missing_column = AlbumSortColumn(
        "CASE WHEN albums.year IS NULL THEN 1 ELSE 0 END",
        "sort_year_missing",
    )
    year_column = AlbumSortColumn("albums.year", "sort_year")
    album_column = AlbumSortColumn("albums.album_sort_key", "sort_album_key")
    album_id_column = AlbumSortColumn("albums.album_id", "sort_album_id")

    if query.sort == ALBUM_LIST_SORT_ARTIST:
        return (
            artist_column,
            year_missing_column,
            year_column,
            album_column,
            album_id_column,
        )

    if query.sort == ALBUM_LIST_SORT_ALBUMS:
        return (
            album_column,
            artist_column,
            year_missing_column,
            year_column,
            album_id_column,
        )

    if query.sort == ALBUM_LIST_SORT_GENRE:
        genre_expression = (
            "selected_album_roots.genre_sort_key"
            if query.root_positions
            else "albums.genre_sort_key"
        )
        return (
            AlbumSortColumn(
                f"CASE WHEN NULLIF({genre_expression}, '') IS NULL THEN 1 ELSE 0 END",
                "sort_genre_missing",
            ),
            AlbumSortColumn(genre_expression, "sort_genre_key"),
            artist_column,
            year_missing_column,
            year_column,
            album_column,
            album_id_column,
        )

    if query.sort == ALBUM_LIST_SORT_STARRED:
        return (
            AlbumSortColumn(
                "albums.starred_at",
                "sort_starred_at",
                direction="DESC",
            ),
            artist_column,
            year_missing_column,
            year_column,
            album_column,
            album_id_column,
        )

    if query.sort == ALBUM_LIST_SORT_RECENT:
        return (
            AlbumSortColumn(
                "recent_album_stats.last_played_at",
                "sort_recent_last_played_at",
                direction="DESC",
            ),
            AlbumSortColumn(
                "recent_album_stats.play_count",
                "sort_recent_play_count",
                direction="DESC",
            ),
            album_id_column,
        )

    if query.sort == ALBUM_LIST_SORT_FREQUENT:
        return (
            AlbumSortColumn(
                "frequent_album_stats.play_count",
                "sort_frequent_play_count",
                direction="DESC",
            ),
            AlbumSortColumn(
                "frequent_album_stats.last_played_at",
                "sort_frequent_last_played_at",
                direction="DESC",
            ),
            album_id_column,
        )

    return (
        AlbumSortColumn(
            "CASE WHEN NULLIF(albums.added_at, '') IS NULL THEN 1 ELSE 0 END",
            "sort_added_missing",
        ),
        AlbumSortColumn(
            "albums.added_at",
            "sort_added_at",
            direction="DESC",
        ),
        artist_column,
        year_missing_column,
        year_column,
        album_column,
        album_id_column,
    )


def album_play_stats_join_sql(query: AlbumListQuery) -> str:
    if query.sort == ALBUM_LIST_SORT_RECENT:
        return """
                    JOIN play_album_stats AS recent_album_stats
                        ON recent_album_stats.album_id = albums.album_id
                        AND recent_album_stats.album_id IS NOT NULL
                        AND recent_album_stats.album_id != ''
    """
    if query.sort == ALBUM_LIST_SORT_FREQUENT:
        return """
                    JOIN play_album_stats AS frequent_album_stats
                        ON frequent_album_stats.album_id = albums.album_id
                        AND frequent_album_stats.album_id IS NOT NULL
                        AND frequent_album_stats.album_id != ''
    """
    return ""


def album_sort_select_sql(sort_columns: tuple[AlbumSortColumn, ...]) -> str:
    return "".join(
        f",\n                        {column.expression} AS {column.alias}"
        for column in sort_columns
    )


def album_order_by_clause(
    sort_columns: tuple[AlbumSortColumn, ...],
    *,
    reverse: bool = False,
) -> str:
    parts: list[str] = []
    for column in sort_columns:
        direction = column.direction
        if reverse:
            direction = "DESC" if direction == "ASC" else "ASC"
        parts.append(f"{column.expression} {direction}")
    return "ORDER BY " + ", ".join(parts)


def album_artists_by_album(
    connection: Connection,
    album_ids: Iterable[str],
) -> dict[str, tuple[str, ...]]:
    requested_ids = tuple(dict.fromkeys(album_id for album_id in album_ids if album_id))
    if not requested_ids:
        return {}
    placeholders = placeholders_for(requested_ids)
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
    return {
        album_id: unique_artist_values(values)
        for album_id, values in artists.items()
    }


def album_artist_cover_album_ids(
    connection: Connection,
    artists: Iterable[str],
    *,
    root_position: int | None = None,
) -> dict[str, str]:
    requested_artists = tuple(
        dict.fromkeys(artist for artist in artists if artist)
    )
    if not requested_artists:
        return {}
    cover_album_ids: dict[str, str] = {}
    for artist in requested_artists:
        if root_position is None:
            row = connection.execute(
                """
                SELECT albums.album_id
                FROM library_albums AS albums
                JOIN library_album_artists AS artists
                    ON artists.album_id = albums.album_id
                WHERE artists.artist = ? COLLATE NOCASE
                    AND albums.art_track_id IS NOT NULL
                ORDER BY albums.rowid
                LIMIT 1
                """,
                (artist,),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT albums.album_id
                FROM library_albums AS albums
                JOIN library_album_roots AS album_roots
                    ON album_roots.album_id = albums.album_id
                JOIN library_album_artists AS artists
                    ON artists.album_id = albums.album_id
                WHERE artists.artist = ? COLLATE NOCASE
                    AND album_roots.root_position = ?
                    AND album_roots.art_track_id IS NOT NULL
                ORDER BY albums.rowid
                LIMIT 1
                """,
                (artist, root_position),
            ).fetchone()
        if row is not None:
            cover_album_ids[artist.casefold()] = str(row["album_id"])
    return cover_album_ids


def album_sort_genres_by_album(
    connection: Connection,
    album_ids: Iterable[str],
    *,
    root_positions: Iterable[int] = (),
) -> dict[str, str]:
    requested_ids = tuple(dict.fromkeys(album_id for album_id in album_ids if album_id))
    if not requested_ids:
        return {}
    root_positions = tuple(root_positions)
    album_placeholders = placeholders_for(requested_ids)
    params: list[object] = [*requested_ids]
    table = "library_album_genres"
    root_sql = ""
    if root_positions:
        table = "library_album_root_genres"
        root_sql = f"AND root_position IN ({placeholders_for(root_positions)})"
        params.extend(root_positions)
    genres_by_album: dict[str, list[str]] = {}
    for row in connection.execute(
        f"""
        SELECT album_id, genre
        FROM {table}
        WHERE album_id IN ({album_placeholders})
            {root_sql}
        ORDER BY album_id
        """,
        params,
    ):
        genres_by_album.setdefault(str(row["album_id"]), []).append(str(row["genre"]))
    genres: dict[str, str] = {}
    for album_id, values in genres_by_album.items():
        sorted_values = unique_sorted(values)
        if sorted_values:
            genres[album_id] = sorted_values[0]
    return genres


def album_artist_display_text(artists: Iterable[str]) -> str:
    return ", ".join(artists) or "<unknown artist>"


def unique_artist_values(values: Iterable[str]) -> tuple[str, ...]:
    artists: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = normalize_match(value)
        if not key or key in seen:
            continue
        seen.add(key)
        artists.append(value)
    return tuple(artists)


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


def track_ids_with_artwork(
    connection: Connection,
    track_ids: list[int],
    *,
    height_px: int | None = None,
) -> set[int]:
    if not track_ids:
        return set()
    placeholders = placeholders_for(track_ids)
    height_sql = ""
    params: list[object] = list(track_ids)
    if height_px is not None:
        height_sql = " AND height_px = ?"
        params.append(height_px)
    return {
        int(row["track_id"])
        for row in connection.execute(
            f"""
            SELECT DISTINCT track_id
            FROM library_track_artwork
            WHERE track_id IN ({placeholders}){height_sql}
            """,
            params,
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


def mapped_artist_lines(value: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in value.splitlines() if line.strip())


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
            label=root_source_label(
                str(row["root_path"]),
                str(row["kind"] or SOURCE_KIND_LOCAL),
                str(row["source_json"] or "{}"),
            ),
            kind=str(row["kind"] or SOURCE_KIND_LOCAL),
            source_json=str(row["source_json"] or "{}"),
        )
        for row in connection.execute(
            """
            SELECT
                position,
                root_path,
                COALESCE(kind, 'local') AS kind,
                COALESCE(source_json, '{}') AS source_json
            FROM library_roots
            ORDER BY position
            """
        )
    )



__all__ = [
    "ALBUM_LIST_SORT_ALBUMS",
    "ALBUM_LIST_SORT_ARTIST",
    "ALBUM_LIST_SORT_FREQUENT",
    "ALBUM_LIST_SORT_GENRE",
    "ALBUM_LIST_SORT_RECENT",
    "ALBUM_LIST_SORT_RECENTLY_ADDED",
    "ALBUM_LIST_SORT_STARRED",
    "AlbumArtistSplitMapping",
    "AlbumDetails",
    "AlbumListQuery",
    "AlbumNotFoundError",
    "AlbumPage",
    "AlbumSummary",
    "ArtistNotFoundError",
    "GenreFilterGroup",
    "GenreStyleFilter",
    "LibraryArtistAlbum",
    "LibraryArtistDetails",
    "LibraryArtistSummary",
    "LibraryAlbumArtistStats",
    "LibraryGenre",
    "LibraryQueries",
    "LibraryFilterOptions",
    "LibraryRootAlbumArtistStats",
    "LibraryRootFilterOption",
    "LibraryRootStats",
    "LibraryStats",
    "PlaylistDetails",
    "PlaylistItem",
    "PlaylistItemNotFoundError",
    "PlaylistNotFoundError",
    "PlaylistTrack",
    "TrackNotFoundError",
]
