from __future__ import annotations

import csv
import io
from dataclasses import dataclass

TAXONOMY_TSV_HEADERS = [
    "kind",
    "name",
    "parent",
    "source",
    "source_term",
    "status",
    "count",
]
TAXONOMY_KINDS = {"genre", "style"}


@dataclass(frozen=True, slots=True)
class TaxonomyTsvRow:
    kind: str
    name: str
    parent: str = ""
    source: str = ""
    source_term: str = ""
    status: str = ""
    count: str = ""

    def as_record(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "name": self.name,
            "parent": self.parent,
            "source": self.source,
            "source_term": self.source_term,
            "status": self.status,
            "count": self.count,
        }

def parse_taxonomy_tsv(text: str, *, source: str = "taxonomy.tsv") -> list[TaxonomyTsvRow]:
    if not text.strip():
        return []

    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    headers = set(reader.fieldnames or [])
    missing = [header for header in TAXONOMY_TSV_HEADERS[:2] if header not in headers]
    if missing:
        raise ValueError(f"{source} missing required columns: {', '.join(missing)}")

    rows: list[TaxonomyTsvRow] = []
    for line_number, raw_row in enumerate(reader, start=2):
        row = {key: (value or "").strip() for key, value in raw_row.items() if key is not None}
        kind = row.get("kind", "").casefold()
        name = row.get("name", "")
        if not kind and not name:
            continue
        if kind not in TAXONOMY_KINDS:
            raise ValueError(
                f"{source} line {line_number}: kind must be one of {sorted(TAXONOMY_KINDS)}"
            )
        if not name:
            raise ValueError(f"{source} line {line_number}: name is required")
        rows.append(
            TaxonomyTsvRow(
                kind=kind,
                name=name,
                parent=row.get("parent", ""),
                source=row.get("source", ""),
                source_term=row.get("source_term", "") or name,
                status=row.get("status", ""),
                count=row.get("count", ""),
            )
        )
    return rows


def format_taxonomy_tsv(rows: list[TaxonomyTsvRow]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=TAXONOMY_TSV_HEADERS,
        delimiter="\t",
        lineterminator="\n",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row.as_record())
    return output.getvalue().rstrip("\n") + "\n"
