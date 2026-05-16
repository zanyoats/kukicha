from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .library_sources import SOURCE_KIND_LOCAL, SOURCE_KIND_S3


@dataclass(frozen=True, slots=True)
class AudioResource:
    kind: str
    path: str
    source_json: str = "{}"
    object_key: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None

    @property
    def name(self) -> str:
        if self.kind == SOURCE_KIND_S3 and self.object_key:
            return Path(self.object_key).name
        return Path(self.path).name

    @property
    def local_path(self) -> Path:
        return Path(self.path)


def local_audio_resource(path: Path | str) -> AudioResource:
    return AudioResource(kind=SOURCE_KIND_LOCAL, path=str(path))
