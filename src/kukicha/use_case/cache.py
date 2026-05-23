from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CacheTableGroup:
    key: str
    section: str
    label: str
    table_names: tuple[str, ...]

    @property
    def display_label(self) -> str:
        return f"{self.section} {self.label}"


CACHE_TABLE_GROUPS = (
    CacheTableGroup(
        key="musicbrainz-entities",
        section="MusicBrainz",
        label="Entities",
        table_names=("musicbrainz_entity_cache",),
    ),
    CacheTableGroup(
        key="musicbrainz-cover-artwork-metadata",
        section="MusicBrainz",
        label="Cover Artwork + Metadata",
        table_names=(
            "cover_art_archive_entity_cache",
            "cover_art_archive_image_cache",
        ),
    ),
    CacheTableGroup(
        key="itunes-cover-artwork",
        section="iTunes",
        label="Cover Artwork",
        table_names=("itunes_lookup_image_cache",),
    ),
)
CACHE_TABLE_GROUP_BY_KEY = {group.key: group for group in CACHE_TABLE_GROUPS}
