from __future__ import annotations

from collections.abc import Iterable
from sqlite3 import Connection, Row

from ...models import normalize_genre_values
from ...search import SearchFactor, parse_album_search_query
from .artists import canonical_album_artist_values
from .models import AlbumListQuery, GenreStyleFilter, normalize_match
from .sql import placeholders_for


def expanded_album_list_query(
    connection: Connection,
    query: AlbumListQuery,
) -> AlbumListQuery:
    artists = (
        canonical_album_artist_values(connection, query.artists)
        if query.artists
        else query.artists
    )
    genre_filters = query.genre_filters
    if query.genre_filters:
        genre_filters = expanded_genre_style_filters(connection, query.genre_filters)
    if artists == query.artists and genre_filters == query.genre_filters:
        return query
    return AlbumListQuery(
        artists=artists,
        album=query.album,
        root_positions=query.root_positions,
        genres=query.genres,
        styles=query.styles,
        genre_filters=genre_filters,
        has_cover=query.has_cover,
        is_compilation=query.is_compilation,
        is_work=query.is_work,
        is_playlist=query.is_playlist,
        page=query.page,
        per_page=query.per_page,
        search=query.search,
        sort=query.sort,
        cursor=query.cursor,
    )


def expanded_genre_style_filters(
    connection: Connection,
    filters: tuple[GenreStyleFilter, ...],
) -> tuple[GenreStyleFilter, ...]:
    genres = tuple(filter_item.genre for filter_item in filters)
    available_styles_by_genre = library_styles_by_genre(connection, genres)
    expanded: list[GenreStyleFilter] = []
    for filter_item in filters:
        if not filter_item.styles:
            expanded.append(filter_item)
            continue
        genre_key = normalize_match(filter_item.genre)
        available_style_keys = normalized_match_set(
            available_styles_by_genre.get(genre_key, ())
        )
        selected_style_keys = normalized_match_set(filter_item.styles)
        styles = (
            ()
            if available_style_keys and available_style_keys <= selected_style_keys
            else filter_item.styles
        )
        expanded.append(
            GenreStyleFilter(
                genre=filter_item.genre,
                styles=styles,
            )
        )
    return tuple(expanded)


def library_styles_by_genre(
    connection: Connection,
    genres: Iterable[str],
) -> dict[str, tuple[str, ...]]:
    ordered_genres = tuple(normalize_genre_values(genres))
    if not ordered_genres:
        return {}
    placeholders = placeholders_for(ordered_genres)
    rows = connection.execute(
        f"""
        SELECT DISTINCT
            library_album_styles.style,
            taxonomy_styles.parent_genre
        FROM library_album_styles
        JOIN taxonomy_styles
            ON taxonomy_styles.style = library_album_styles.style
        WHERE taxonomy_styles.parent_genre COLLATE NOCASE IN ({placeholders})
        """,
        ordered_genres,
    )
    styles_by_genre: dict[str, list[str]] = {}
    for row in rows:
        genre_key = normalize_match(str(row["parent_genre"]))
        styles_by_genre.setdefault(genre_key, []).append(str(row["style"]))
    return {
        genre_key: tuple(normalize_genre_values(values))
        for genre_key, values in styles_by_genre.items()
    }


def playlist_query_can_match(query: AlbumListQuery) -> bool:
    return not (
        query.artists
        or query.album
        or query.genres
        or query.styles
        or query.genre_filters
        or query.has_cover is not None
        or query.is_compilation is not None
        or query.is_work is not None
    )


def playlist_where_clause(query: AlbumListQuery) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if query.root_positions:
        placeholders = placeholders_for(query.root_positions)
        clauses.append(f"playlists.root_position IN ({placeholders})")
        params.extend(query.root_positions)
    if not clauses:
        return "", params
    return "WHERE " + " AND ".join(f"({clause})" for clause in clauses), params


def playlist_matches_search(row: Row, value: str | None) -> bool:
    if not value:
        return True
    haystack = f"{row['name']} {row['path']}".casefold()
    groups = parse_album_search_query(value)
    if not groups:
        return True
    for group in groups:
        group_matches = True
        for factor in group:
            needle = search_factor_text(factor.match_query)
            if not needle:
                continue
            contains = needle in haystack
            if factor.negated:
                contains = not contains
            if not contains:
                group_matches = False
                break
        if group_matches:
            return True
    return False


def search_factor_text(match_query: str) -> str:
    text = match_query.strip()
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1].replace('""', '"')
    return text.casefold().strip()


def album_where_clause(query: AlbumListQuery) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if query.artists:
        placeholders = placeholders_for(query.artists)
        clauses.append(
            f"""
            EXISTS (
                SELECT 1
                FROM library_album_artists AS album_artists
                WHERE album_artists.album_id = albums.album_id
                    AND album_artists.artist COLLATE NOCASE IN ({placeholders})
            )
            """
        )
        params.extend(query.artists)
    if query.album:
        clauses.append("albums.album = ? COLLATE NOCASE")
        params.append(query.album)
    if query.search:
        search_clause, search_params = album_search_clause(query.search)
        if search_clause:
            clauses.append(search_clause)
            params.extend(search_params)
    if query.root_positions:
        clauses.append(
            f"""
            EXISTS (
                SELECT 1
                FROM library_album_roots AS album_roots
                WHERE album_roots.album_id = albums.album_id
                    AND album_roots.root_position IN ({placeholders_for(query.root_positions)})
            )
            """
        )
        params.extend(query.root_positions)
    if query.genre_filters:
        genre_clause, genre_params = grouped_genre_filter_clause(query)
        if genre_clause:
            clauses.append(genre_clause)
            params.extend(genre_params)
    elif query.genres:
        genre_clause, genre_params = album_value_clause(
            query,
            root_table="library_album_root_genres",
            table="library_album_genres",
            column="genre",
            values=query.genres,
        )
        clauses.append(genre_clause)
        params.extend(genre_params)
    if query.genre_filters:
        pass
    elif query.styles:
        style_clause, style_params = album_value_clause(
            query,
            root_table="library_album_root_styles",
            table="library_album_styles",
            column="style",
            values=query.styles,
        )
        clauses.append(style_clause)
        params.extend(style_params)
    if query.has_cover is not None:
        cover_clause, cover_params = album_flag_clause(
            query,
            column="has_cover",
            value=query.has_cover,
        )
        clauses.append(cover_clause)
        params.extend(cover_params)
    if query.is_compilation is not None:
        compilation_clause, compilation_params = album_flag_clause(
            query,
            column="is_compilation",
            value=query.is_compilation,
        )
        clauses.append(compilation_clause)
        params.extend(compilation_params)
    if query.is_work is not None:
        work_clause, work_params = album_flag_clause(
            query,
            column="is_work",
            value=query.is_work,
        )
        clauses.append(work_clause)
        params.extend(work_params)
    if not clauses:
        return "", params
    return "WHERE " + " AND ".join(f"({clause})" for clause in clauses), params


def grouped_genre_filter_clause(query: AlbumListQuery) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []

    for genre_filter in query.genre_filters:
        clause, clause_params = grouped_genre_style_clause(
            genre_filter,
            root_positions=query.root_positions,
        )
        if clause:
            clauses.append(clause)
            params.extend(clause_params)

    if query.genres:
        genre_clause, genre_params = album_value_clause(
            query,
            root_table="library_album_root_genres",
            table="library_album_genres",
            column="genre",
            values=query.genres,
        )
        clauses.append(genre_clause)
        params.extend(genre_params)

    if query.styles:
        style_clause, style_params = album_value_clause(
            query,
            root_table="library_album_root_styles",
            table="library_album_styles",
            column="style",
            values=query.styles,
        )
        clauses.append(style_clause)
        params.extend(style_params)

    if not clauses:
        return "", []
    return " OR ".join(f"({clause})" for clause in clauses), params


def grouped_genre_style_clause(
    genre_filter: GenreStyleFilter,
    *,
    root_positions: tuple[int, ...],
) -> tuple[str, list[object]]:
    if not genre_filter.genre:
        return "", []

    if not genre_filter.styles:
        query = AlbumListQuery(root_positions=root_positions)
        return album_value_clause(
            query,
            root_table="library_album_root_genres",
            table="library_album_genres",
            column="genre",
            values=(genre_filter.genre,),
        )

    style_placeholders = placeholders_for(genre_filter.styles)
    if root_positions:
        root_placeholders = placeholders_for(root_positions)
        clause = f"""
            EXISTS (
                SELECT 1
                FROM library_album_root_genre_styles AS album_values
                WHERE album_values.album_id = albums.album_id
                    AND album_values.root_position IN ({root_placeholders})
                    AND album_values.genre COLLATE NOCASE = ?
                    AND album_values.style COLLATE NOCASE IN ({style_placeholders})
            )
        """
        return clause, [*root_positions, genre_filter.genre, *genre_filter.styles]

    clause = f"""
        EXISTS (
            SELECT 1
            FROM library_album_genre_styles AS album_values
            WHERE album_values.album_id = albums.album_id
                AND album_values.genre COLLATE NOCASE = ?
                AND album_values.style COLLATE NOCASE IN ({style_placeholders})
        )
    """
    return clause, [genre_filter.genre, *genre_filter.styles]


def album_value_clause(
    query: AlbumListQuery,
    *,
    root_table: str,
    table: str,
    column: str,
    values: tuple[str, ...],
) -> tuple[str, list[object]]:
    value_placeholders = placeholders_for(values)
    if query.root_positions:
        root_placeholders = placeholders_for(query.root_positions)
        clause = f"""
            EXISTS (
                SELECT 1
                FROM {root_table} AS album_values
                WHERE album_values.album_id = albums.album_id
                    AND album_values.root_position IN ({root_placeholders})
                    AND album_values.{column} COLLATE NOCASE IN ({value_placeholders})
            )
        """
        return clause, [*query.root_positions, *values]

    clause = f"""
        EXISTS (
            SELECT 1
            FROM {table} AS album_values
            WHERE album_values.album_id = albums.album_id
                AND album_values.{column} COLLATE NOCASE IN ({value_placeholders})
        )
    """
    return clause, list(values)


def album_flag_clause(
    query: AlbumListQuery,
    *,
    column: str,
    value: bool,
) -> tuple[str, list[object]]:
    if not query.root_positions:
        return f"albums.{column} = ?", [1 if value else 0]

    root_placeholders = placeholders_for(query.root_positions)
    has_selected_root_clause = f"""
        EXISTS (
            SELECT 1
            FROM library_album_roots AS album_roots
            WHERE album_roots.album_id = albums.album_id
                AND album_roots.root_position IN ({root_placeholders})
        )
    """
    flag_clause = f"""
        EXISTS (
            SELECT 1
            FROM library_album_roots AS album_roots
            WHERE album_roots.album_id = albums.album_id
                AND album_roots.root_position IN ({root_placeholders})
                AND album_roots.{column} = 1
        )
    """
    if value:
        return flag_clause, list(query.root_positions)
    return (
        f"({has_selected_root_clause}) AND NOT ({flag_clause})",
        [*query.root_positions, *query.root_positions],
    )


def album_search_clause(value: str) -> tuple[str, list[object]]:
    groups = parse_album_search_query(value)
    if not groups:
        return "", []

    params: list[object] = []
    group_clauses: list[str] = []
    for group in groups:
        factor_clauses: list[str] = []
        for factor in group:
            factor_clauses.append(album_search_factor_clause(factor))
            params.append(factor.match_query)
        if factor_clauses:
            group_clauses.append(
                " AND ".join(f"({clause})" for clause in factor_clauses)
            )

    if not group_clauses:
        return "", []
    return " OR ".join(f"({clause})" for clause in group_clauses), params


def album_search_factor_clause(factor: SearchFactor) -> str:
    clause = """
        EXISTS (
            SELECT 1
            FROM library_album_search
            WHERE library_album_search.album_id = albums.album_id
                AND library_album_search MATCH ?
        )
    """
    return f"NOT {clause}" if factor.negated else clause


def normalized_match_set(values: Iterable[str]) -> set[str]:
    return {normalize_match(value) for value in values if value and value.strip()}
