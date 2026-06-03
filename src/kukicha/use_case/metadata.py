from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from ..discogs import file_album_id_from_album_id, normalize_release_variant


METADATA_PROVIDER_MUSICBRAINZ = "musicbrainz"
METADATA_PROVIDER_DISCOGS = "discogs"
METADATA_ENTITY_RELEASE = "release"
METADATA_ENTITY_RELEASE_GROUP = "release-group"
METADATA_ENTITY_MASTER = "master"
METADATA_PROVIDERS = {
    METADATA_PROVIDER_MUSICBRAINZ,
    METADATA_PROVIDER_DISCOGS,
}
METADATA_ENTITY_TYPES = {
    METADATA_ENTITY_RELEASE,
    METADATA_ENTITY_RELEASE_GROUP,
    METADATA_ENTITY_MASTER,
}
_UNCHANGED = object()


@dataclass(slots=True)
class AlbumMetadataLink:
    file_album_id: str
    provider: str
    entity_type: str
    entity_id: str
    related_entity_type: str | None = None
    related_entity_id: str | None = None

    @property
    def has_identifier(self) -> bool:
        return bool(self.provider and self.entity_type and self.entity_id)

    @property
    def album_id(self) -> str:
        return self.file_album_id

    @property
    def release_mbid(self) -> str | None:
        if self.provider != METADATA_PROVIDER_MUSICBRAINZ:
            return None
        return self.entity_id if self.entity_type == METADATA_ENTITY_RELEASE else None

    @release_mbid.setter
    def release_mbid(self, value: str | None) -> None:
        self.entity_type = METADATA_ENTITY_RELEASE if value else self.entity_type
        self.entity_id = clean_metadata_identifier(value) or self.entity_id

    @property
    def release_group_mbid(self) -> str | None:
        if self.provider != METADATA_PROVIDER_MUSICBRAINZ:
            return None
        if self.entity_type == METADATA_ENTITY_RELEASE_GROUP:
            return self.entity_id
        if self.related_entity_type == METADATA_ENTITY_RELEASE_GROUP:
            return self.related_entity_id
        return None

    @release_group_mbid.setter
    def release_group_mbid(self, value: str | None) -> None:
        cleaned = clean_metadata_identifier(value)
        if not cleaned:
            self.related_entity_type = None
            self.related_entity_id = None
            return
        if self.entity_type == METADATA_ENTITY_RELEASE_GROUP:
            self.entity_id = cleaned
            return
        self.related_entity_type = METADATA_ENTITY_RELEASE_GROUP
        self.related_entity_id = cleaned

    @property
    def discogs_release_id(self) -> str | None:
        if self.provider != METADATA_PROVIDER_DISCOGS:
            return None
        return self.entity_id if self.entity_type == METADATA_ENTITY_RELEASE else None

    @property
    def discogs_master_id(self) -> str | None:
        if self.provider != METADATA_PROVIDER_DISCOGS:
            return None
        if self.entity_type == METADATA_ENTITY_MASTER:
            return self.entity_id
        if self.related_entity_type == METADATA_ENTITY_MASTER:
            return self.related_entity_id
        return None


@dataclass(slots=True)
class AlbumMetadataTrackLink:
    path: str
    file_album_id: str
    provider: str
    entity_type: str
    entity_id: str
    related_entity_type: str | None = None
    related_entity_id: str | None = None

    @property
    def release_mbid(self) -> str | None:
        if self.provider != METADATA_PROVIDER_MUSICBRAINZ:
            return None
        return self.entity_id if self.entity_type == METADATA_ENTITY_RELEASE else None

    @property
    def release_group_mbid(self) -> str | None:
        if self.provider != METADATA_PROVIDER_MUSICBRAINZ:
            return None
        if self.entity_type == METADATA_ENTITY_RELEASE_GROUP:
            return self.entity_id
        if self.related_entity_type == METADATA_ENTITY_RELEASE_GROUP:
            return self.related_entity_id
        return None


def load_album_metadata_links(
    connection: sqlite3.Connection,
    *,
    provider: str | None = None,
) -> dict[str, tuple[AlbumMetadataLink, ...]]:
    provider_clause = ""
    params: list[object] = []
    if provider is not None:
        provider_clause = "WHERE provider = ?"
        params.append(provider)

    links: dict[str, list[AlbumMetadataLink]] = {}
    for row in connection.execute(
        f"""
        SELECT
            file_album_id,
            provider,
            entity_type,
            entity_id,
            related_entity_type,
            related_entity_id
        FROM album_metadata_links
        {provider_clause}
        ORDER BY file_album_id, provider, entity_type, entity_id
        """,
        params,
    ):
        file_album_id = str(row["file_album_id"])
        links.setdefault(file_album_id, []).append(album_metadata_link_from_row(row))
    return {file_album_id: tuple(items) for file_album_id, items in links.items()}


def album_metadata_link_for_album_id(
    connection: sqlite3.Connection,
    album_id: str,
    *,
    provider: str | None = None,
) -> AlbumMetadataLink | None:
    links = load_album_metadata_links(connection, provider=provider)
    file_album_id = file_album_id_from_album_id(album_id)
    candidates = list(links.get(file_album_id, ()))
    if file_album_id != album_id:
        candidates.extend(links.get(album_id, ()))

    release_variant = release_variant_from_album_id(album_id)
    if release_variant:
        direct_matches = [
            link for link in candidates
            if link.file_album_id == album_id
        ]
        if len(direct_matches) == 1:
            return direct_matches[0]

        fingerprint_matches = [
            link for link in candidates
            if metadata_link_release_variant(connection, link) == release_variant
        ]
        if len(fingerprint_matches) == 1:
            return fingerprint_matches[0]

    return candidates[0] if len(candidates) == 1 else None


def album_metadata_link_from_row(row: sqlite3.Row) -> AlbumMetadataLink:
    return AlbumMetadataLink(
        file_album_id=str(row["file_album_id"]),
        provider=str(row["provider"]),
        entity_type=str(row["entity_type"]),
        entity_id=str(row["entity_id"]),
        related_entity_type=clean_metadata_identifier(row["related_entity_type"]),
        related_entity_id=clean_metadata_identifier(row["related_entity_id"]),
    )


def release_variant_from_album_id(album_id: str) -> str | None:
    file_album_id = file_album_id_from_album_id(album_id)
    prefix = f"{file_album_id}::"
    if file_album_id == album_id or not album_id.startswith(prefix):
        return None
    release_variant = normalize_release_variant(album_id[len(prefix) :])
    if release_variant and len(release_variant) == 3 and all(
        character in "0123456789abcdef" for character in release_variant
    ):
        return release_variant
    return None


def metadata_link_release_variant(
    connection: sqlite3.Connection,
    link: AlbumMetadataLink,
) -> str | None:
    if link.provider != METADATA_PROVIDER_MUSICBRAINZ or not link.release_mbid:
        return None

    from .musicbrainz import load_cached_musicbrainz_entity, musicbrainz_release_fingerprint

    payload = load_cached_musicbrainz_entity(
        connection,
        entity_type=METADATA_ENTITY_RELEASE,
        mbid=link.release_mbid,
    )
    if payload is None:
        return None
    return musicbrainz_release_fingerprint(
        payload,
        fallback_release_mbid=link.release_mbid,
    )


def store_album_metadata_link(
    connection: sqlite3.Connection,
    file_album_id: str,
    *,
    provider: str,
    entity_type: str | None | object = _UNCHANGED,
    entity_id: str | None | object = _UNCHANGED,
    related_entity_type: str | None | object = _UNCHANGED,
    related_entity_id: str | None | object = _UNCHANGED,
) -> None:
    cleaned_file_album_id = str(file_album_id or "").strip()
    cleaned_provider = normalized_metadata_provider(provider)
    if not cleaned_file_album_id:
        return

    if entity_type is _UNCHANGED or entity_id is _UNCHANGED:
        existing = connection.execute(
            """
            SELECT entity_type, entity_id, related_entity_type, related_entity_id
            FROM album_metadata_links
            WHERE file_album_id = ? AND provider = ?
            ORDER BY entity_type, entity_id
            LIMIT 1
            """,
            (cleaned_file_album_id, cleaned_provider),
        ).fetchone()
    else:
        existing = None

    next_entity_type = (
        str(existing["entity_type"])
        if existing and entity_type is _UNCHANGED
        else clean_metadata_identifier(entity_type)
    )
    next_entity_id = (
        str(existing["entity_id"])
        if existing and entity_id is _UNCHANGED
        else clean_metadata_identifier(entity_id)
    )
    next_related_entity_type = (
        clean_metadata_identifier(existing["related_entity_type"])
        if existing and related_entity_type is _UNCHANGED
        else clean_metadata_identifier(related_entity_type)
    )
    next_related_entity_id = (
        clean_metadata_identifier(existing["related_entity_id"])
        if existing and related_entity_id is _UNCHANGED
        else clean_metadata_identifier(related_entity_id)
    )

    if not next_entity_type or not next_entity_id:
        connection.execute(
            """
            DELETE FROM album_metadata_links
            WHERE file_album_id = ? AND provider = ?
            """,
            (cleaned_file_album_id, cleaned_provider),
        )
        return

    next_entity_type = normalized_metadata_entity_type(next_entity_type)
    next_related_entity_type = (
        normalized_metadata_entity_type(next_related_entity_type)
        if next_related_entity_type
        else None
    )
    if not next_related_entity_type:
        next_related_entity_id = None

    connection.execute(
        """
        INSERT OR IGNORE INTO album_metadata_links (
            file_album_id,
            provider,
            entity_type,
            entity_id,
            related_entity_type,
            related_entity_id
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            cleaned_file_album_id,
            cleaned_provider,
            next_entity_type,
            next_entity_id,
            next_related_entity_type,
            next_related_entity_id,
        ),
    )


def load_album_metadata_track_links(
    connection: sqlite3.Connection,
    paths: Iterable[str],
    *,
    provider: str | None = None,
) -> dict[str, AlbumMetadataTrackLink]:
    requested_paths = tuple(dict.fromkeys(path for path in paths if path))
    if not requested_paths:
        return {}

    provider_clause = ""
    provider_params: list[object] = []
    if provider is not None:
        provider_clause = "AND provider = ?"
        provider_params.append(provider)

    links: dict[str, AlbumMetadataTrackLink] = {}
    batch_size = 500
    for index in range(0, len(requested_paths), batch_size):
        batch = requested_paths[index : index + batch_size]
        placeholders = ", ".join("?" for _path in batch)
        for row in connection.execute(
            f"""
            SELECT
                path,
                file_album_id,
                provider,
                entity_type,
                entity_id,
                related_entity_type,
                related_entity_id
            FROM album_metadata_track_links
            WHERE path IN ({placeholders})
                {provider_clause}
            """,
            [*batch, *provider_params],
        ):
            path = str(row["path"])
            links[path] = AlbumMetadataTrackLink(
                path=path,
                file_album_id=str(row["file_album_id"]),
                provider=str(row["provider"]),
                entity_type=str(row["entity_type"]),
                entity_id=str(row["entity_id"]),
                related_entity_type=clean_metadata_identifier(row["related_entity_type"]),
                related_entity_id=clean_metadata_identifier(row["related_entity_id"]),
            )
    return links


def store_album_metadata_track_link(
    connection: sqlite3.Connection,
    path: str,
    file_album_id: str,
    *,
    provider: str,
    entity_type: str | None,
    entity_id: str | None,
    related_entity_type: str | None = None,
    related_entity_id: str | None = None,
) -> None:
    cleaned_path = str(path or "").strip()
    cleaned_file_album_id = str(file_album_id or "").strip()
    cleaned_provider = normalized_metadata_provider(provider)
    cleaned_entity_type = clean_metadata_identifier(entity_type)
    cleaned_entity_id = clean_metadata_identifier(entity_id)
    cleaned_related_entity_type = clean_metadata_identifier(related_entity_type)
    cleaned_related_entity_id = clean_metadata_identifier(related_entity_id)

    if (
        not cleaned_path
        or not cleaned_file_album_id
        or not cleaned_entity_type
        or not cleaned_entity_id
    ):
        if cleaned_path:
            connection.execute(
                "DELETE FROM album_metadata_track_links WHERE path = ?",
                (cleaned_path,),
            )
        return

    cleaned_entity_type = normalized_metadata_entity_type(cleaned_entity_type)
    if cleaned_related_entity_type:
        cleaned_related_entity_type = normalized_metadata_entity_type(cleaned_related_entity_type)
    else:
        cleaned_related_entity_id = None

    connection.execute(
        """
        INSERT INTO album_metadata_track_links (
            path,
            file_album_id,
            provider,
            entity_type,
            entity_id,
            related_entity_type,
            related_entity_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            file_album_id = excluded.file_album_id,
            provider = excluded.provider,
            entity_type = excluded.entity_type,
            entity_id = excluded.entity_id,
            related_entity_type = excluded.related_entity_type,
            related_entity_id = excluded.related_entity_id
        """,
        (
            cleaned_path,
            cleaned_file_album_id,
            cleaned_provider,
            cleaned_entity_type,
            cleaned_entity_id,
            cleaned_related_entity_type,
            cleaned_related_entity_id,
        ),
    )


def delete_album_metadata_track_links(
    connection: sqlite3.Connection,
    paths: Iterable[str],
) -> None:
    requested_paths = tuple(dict.fromkeys(path for path in paths if path))
    if not requested_paths:
        return

    batch_size = 500
    for index in range(0, len(requested_paths), batch_size):
        batch = requested_paths[index : index + batch_size]
        placeholders = ", ".join("?" for _path in batch)
        connection.execute(
            f"DELETE FROM album_metadata_track_links WHERE path IN ({placeholders})",
            batch,
        )


def normalized_metadata_provider(value: str) -> str:
    provider = str(value or "").strip().casefold()
    if provider not in METADATA_PROVIDERS:
        raise ValueError(f"Unsupported metadata provider: {value}")
    return provider


def normalized_metadata_entity_type(value: str) -> str:
    entity_type = str(value or "").strip().casefold()
    if entity_type not in METADATA_ENTITY_TYPES:
        raise ValueError(f"Unsupported metadata entity type: {value}")
    return entity_type


def clean_metadata_identifier(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None
