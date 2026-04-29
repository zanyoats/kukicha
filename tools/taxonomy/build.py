from __future__ import annotations

import csv
import io
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Callable

from kukicha.taxonomy_data import TaxonomyTsvRow, format_taxonomy_tsv
from kukicha.text import normalize_text

from .discogs import iter_discogs_masters, unique_casefold


BUILD_DATABASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS taxonomy_stats (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    masters_processed INTEGER NOT NULL DEFAULT 0,
    masters_with_genres INTEGER NOT NULL DEFAULT 0,
    masters_with_styles INTEGER NOT NULL DEFAULT 0,
    discogs_source TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS taxonomy_genre_counts (
    genre TEXT PRIMARY KEY,
    count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS taxonomy_style_counts (
    style TEXT PRIMARY KEY,
    count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS taxonomy_genre_style_counts (
    genre TEXT NOT NULL,
    style TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (genre, style)
);

CREATE TABLE IF NOT EXISTS taxonomy_style_pair_counts (
    left_style TEXT NOT NULL,
    right_style TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (left_style, right_style)
);

CREATE TABLE IF NOT EXISTS taxonomy_style_parents (
    style TEXT PRIMARY KEY,
    style_count INTEGER NOT NULL,
    parent_genre TEXT,
    parent_count INTEGER NOT NULL,
    parent_style_share REAL NOT NULL,
    parent_genre_share REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS taxonomy_style_parent_candidates (
    style TEXT NOT NULL,
    rank INTEGER NOT NULL,
    genre TEXT NOT NULL,
    count INTEGER NOT NULL,
    style_share REAL NOT NULL,
    genre_share REAL NOT NULL,
    PRIMARY KEY (style, rank),
    FOREIGN KEY (style) REFERENCES taxonomy_style_parents (style) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS taxonomy_external_styles (
    style TEXT PRIMARY KEY COLLATE NOCASE,
    parent_genre TEXT NOT NULL,
    source TEXT NOT NULL,
    source_term TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_taxonomy_external_styles_parent_genre
    ON taxonomy_external_styles (parent_genre);
CREATE INDEX IF NOT EXISTS idx_taxonomy_external_styles_source
    ON taxonomy_external_styles (source);

CREATE TABLE IF NOT EXISTS taxonomy_term_sources (
    source TEXT NOT NULL,
    source_term TEXT NOT NULL COLLATE NOCASE,
    canonical TEXT NOT NULL,
    canonical_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    PRIMARY KEY (source, source_term, canonical_kind)
);
CREATE INDEX IF NOT EXISTS idx_taxonomy_term_sources_canonical
    ON taxonomy_term_sources (canonical_kind, canonical);
CREATE INDEX IF NOT EXISTS idx_taxonomy_term_sources_source
    ON taxonomy_term_sources (source);
"""


def connect_database(path: Path, *, create: bool = True) -> sqlite3.Connection:
    if not create and not path.exists():
        raise FileNotFoundError(path)

    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.executescript(BUILD_DATABASE_SCHEMA)
    migrate_build_database_schema(connection)
    connection.execute(
        """
        INSERT OR IGNORE INTO taxonomy_stats (
            singleton,
            masters_processed,
            masters_with_genres,
            masters_with_styles,
            discogs_source
        ) VALUES (1, 0, 0, 0, '')
        """
    )
    seed_discogs_term_sources(connection)
    seed_external_term_sources(connection)
    return connection


def migrate_build_database_schema(connection: sqlite3.Connection) -> None:
    taxonomy_stats_columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(taxonomy_stats)")
    }
    if "discogs_source" not in taxonomy_stats_columns:
        connection.execute(
            "ALTER TABLE taxonomy_stats ADD COLUMN discogs_source TEXT NOT NULL DEFAULT ''"
        )


def clear_taxonomy(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM taxonomy_term_sources WHERE status = 'native'")
    connection.execute("DELETE FROM taxonomy_style_parent_candidates")
    connection.execute("DELETE FROM taxonomy_style_parents")
    connection.execute("DELETE FROM taxonomy_style_pair_counts")
    connection.execute("DELETE FROM taxonomy_genre_style_counts")
    connection.execute("DELETE FROM taxonomy_style_counts")
    connection.execute("DELETE FROM taxonomy_genre_counts")
    connection.execute(
        """
        UPDATE taxonomy_stats
        SET masters_processed = 0,
            masters_with_genres = 0,
            masters_with_styles = 0,
            discogs_source = ''
        WHERE singleton = 1
        """
    )


def seed_discogs_term_sources(connection: sqlite3.Connection) -> None:
    discogs_source = read_discogs_source(connection)
    if not discogs_source:
        return
    connection.execute(
        """
        INSERT OR IGNORE INTO taxonomy_term_sources (
            source,
            source_term,
            canonical,
            canonical_kind,
            status
        )
        SELECT
            ?,
            genre,
            genre,
            'genre',
            'native'
        FROM taxonomy_genre_counts
        """,
        (discogs_source,),
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO taxonomy_term_sources (
            source,
            source_term,
            canonical,
            canonical_kind,
            status
        )
        SELECT
            ?,
            style,
            style,
            'style',
            'native'
        FROM taxonomy_style_counts
        """,
        (discogs_source,),
    )


def read_discogs_source(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        "SELECT discogs_source FROM taxonomy_stats WHERE singleton = 1"
    ).fetchone()
    if row is None:
        return ""
    return str(row["discogs_source"]).strip()


def validate_source_name(source_name: str) -> str:
    error = source_name_error(source_name)
    if error:
        raise ValueError(f"source {error}")
    return source_name.strip()


def source_name_error(source_name: str) -> str:
    source_name = source_name.strip()
    if not source_name:
        return "is required"
    if any(character in source_name for character in "\t\r\n"):
        return "must not contain tabs or newlines"
    return ""


def seed_external_term_sources(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO taxonomy_term_sources (
            source,
            source_term,
            canonical,
            canonical_kind,
            status
        )
        SELECT
            source,
            source_term,
            style,
            'style',
            'added'
        FROM taxonomy_external_styles
        """
    )


@dataclass(slots=True)
class InferredStyleParent:
    style: str
    style_count: int
    parent_genre: str | None
    parent_count: int
    parent_style_share: float
    parent_genre_share: float
    candidate_genres: list[tuple[str, int, float, float]]


@dataclass(slots=True)
class MasterTaxonomyReport:
    masters_processed: int
    masters_with_genres: int
    masters_with_styles: int
    genre_counts: list[tuple[str, int]]
    style_counts: list[tuple[str, int]]
    genre_style_counts: list[tuple[str, str, int]]
    style_pair_counts: list[tuple[str, str, int]]
    inferred_style_parents: list[InferredStyleParent]


@dataclass(slots=True)
class MusicBrainzReviewRow:
    source: str
    term: str
    canonical: str
    canonical_kind: str
    status: str
    action: str


@dataclass(slots=True)
class MusicBrainzMergeSummary:
    database: str
    review_file: str
    rows_read: int = 0
    exact_rows: int = 0
    new_style_rows: int = 0
    ignored_rows: int = 0
    no_action_rows: int = 0
    add_actions: int = 0
    styles_added: int = 0
    styles_updated: int = 0
    styles_already_present: int = 0
    styles_already_discogs: int = 0
    source_links_added: int = 0
    source_links_updated: int = 0
    source_links_already_present: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "database": self.database,
            "review_file": self.review_file,
            "rows_read": self.rows_read,
            "exact_rows": self.exact_rows,
            "new_style_rows": self.new_style_rows,
            "ignored_rows": self.ignored_rows,
            "no_action_rows": self.no_action_rows,
            "add_actions": self.add_actions,
            "styles_added": self.styles_added,
            "styles_updated": self.styles_updated,
            "styles_already_present": self.styles_already_present,
            "styles_already_discogs": self.styles_already_discogs,
            "source_links_added": self.source_links_added,
            "source_links_updated": self.source_links_updated,
            "source_links_already_present": self.source_links_already_present,
        }


MUSICBRAINZ_REVIEW_HEADERS = [
    "source",
    "term",
    "canonical",
    "canonical_kind",
    "status",
    "action",
]
OPTIONAL_MUSICBRAINZ_REVIEW_HEADERS = {"canonical_kind"}
MUSICBRAINZ_REVIEW_STATUSES = {"exact", "new_style"}
CANONICAL_KINDS = {"genre", "style"}


def build_master_taxonomy_report(
    source: Path,
    *,
    tick: Callable[[], None] | None = None,
) -> MasterTaxonomyReport:
    genre_counter: Counter[str] = Counter()
    style_counter: Counter[str] = Counter()
    genre_style_counter: Counter[tuple[str, str]] = Counter()
    style_pair_counter: Counter[tuple[str, str]] = Counter()
    masters_processed = 0
    masters_with_genres = 0
    masters_with_styles = 0

    for master in iter_discogs_masters(source):
        masters_processed += 1
        unique_genres = unique_casefold(master.genres)
        unique_styles = unique_casefold(master.styles)

        if unique_genres:
            masters_with_genres += 1
        if unique_styles:
            masters_with_styles += 1

        genre_counter.update(unique_genres)
        style_counter.update(unique_styles)

        for genre in unique_genres:
            for style in unique_styles:
                genre_style_counter[(genre, style)] += 1

        for left, right in combinations(unique_styles, 2):
            ordered = tuple(sorted((left, right), key=str.casefold))
            style_pair_counter[ordered] += 1

        if tick and masters_processed % 100000 == 0:
            tick()

    return MasterTaxonomyReport(
        masters_processed=masters_processed,
        masters_with_genres=masters_with_genres,
        masters_with_styles=masters_with_styles,
        genre_counts=sorted_counter(genre_counter),
        style_counts=sorted_counter(style_counter),
        genre_style_counts=sorted_triples(genre_style_counter),
        style_pair_counts=sorted_triples(style_pair_counter),
        inferred_style_parents=infer_style_parents(
            style_counter=style_counter,
            genre_counter=genre_counter,
            genre_style_counter=genre_style_counter,
        ),
    )


def store_master_taxonomy_report(
    index_path: Path,
    report: MasterTaxonomyReport,
    *,
    source_name: str,
) -> None:
    source_name = validate_source_name(source_name)
    with connect_database(index_path) as connection:
        clear_taxonomy(connection)
        connection.execute(
            """
            UPDATE taxonomy_stats
            SET masters_processed = ?,
                masters_with_genres = ?,
                masters_with_styles = ?,
                discogs_source = ?
            WHERE singleton = 1
            """,
            (
                report.masters_processed,
                report.masters_with_genres,
                report.masters_with_styles,
                source_name,
            ),
        )
        connection.executemany(
            "INSERT INTO taxonomy_genre_counts (genre, count) VALUES (?, ?)",
            report.genre_counts,
        )
        connection.executemany(
            "INSERT INTO taxonomy_style_counts (style, count) VALUES (?, ?)",
            report.style_counts,
        )
        connection.executemany(
            """
            INSERT INTO taxonomy_genre_style_counts (genre, style, count)
            VALUES (?, ?, ?)
            """,
            report.genre_style_counts,
        )
        connection.executemany(
            """
            INSERT INTO taxonomy_style_pair_counts (left_style, right_style, count)
            VALUES (?, ?, ?)
            """,
            report.style_pair_counts,
        )
        connection.executemany(
            """
            INSERT INTO taxonomy_style_parents (
                style,
                style_count,
                parent_genre,
                parent_count,
                parent_style_share,
                parent_genre_share
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item.style,
                    item.style_count,
                    item.parent_genre,
                    item.parent_count,
                    item.parent_style_share,
                    item.parent_genre_share,
                )
                for item in report.inferred_style_parents
            ],
        )
        connection.executemany(
            """
            INSERT INTO taxonomy_style_parent_candidates (
                style,
                rank,
                genre,
                count,
                style_share,
                genre_share
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item.style,
                    rank,
                    genre,
                    count,
                    style_share,
                    genre_share,
                )
                for item in report.inferred_style_parents
                for rank, (genre, count, style_share, genre_share) in enumerate(
                    item.candidate_genres
                )
            ],
        )
        seed_discogs_term_sources(connection)
        connection.commit()


def build_musicbrainz_review_rows(
    database: Path,
    terms_path: Path,
    source_name: str,
) -> list[MusicBrainzReviewRow]:
    source_name = validate_source_name(source_name)
    terms = read_musicbrainz_terms(terms_path)
    with connect_database(database, create=False) as connection:
        canonical_lookup = load_discogs_canonical_lookup(connection)

    rows: list[MusicBrainzReviewRow] = []
    for term in terms:
        match = first_lookup_match(canonical_lookup, normalized_taxonomy_keys(term))
        if match:
            canonical, canonical_kind = match
            rows.append(
                MusicBrainzReviewRow(
                    source=source_name,
                    term=term,
                    canonical=canonical,
                    canonical_kind=canonical_kind,
                    status="exact",
                    action="none",
                )
            )
            continue
        rows.append(
            MusicBrainzReviewRow(
                source=source_name,
                term=term,
                canonical="",
                canonical_kind="",
                status="new_style",
                action="ignore",
            )
        )
    return rows


def format_musicbrainz_review_tsv(rows: list[MusicBrainzReviewRow]) -> str:
    output = io.StringIO()
    writer = csv.writer(output, delimiter="\t", lineterminator="\n")
    writer.writerow(MUSICBRAINZ_REVIEW_HEADERS)
    for row in rows:
        writer.writerow(
            [
                row.source,
                row.term,
                row.canonical,
                row.canonical_kind,
                row.status,
                row.action,
            ]
        )
    return output.getvalue().rstrip("\n")


def merge_musicbrainz_review(
    database: Path,
    review_file: Path,
) -> MusicBrainzMergeSummary:
    review_rows = read_musicbrainz_review_rows(review_file)
    summary = MusicBrainzMergeSummary(
        database=str(database.resolve()),
        review_file=str(review_file.resolve()),
    )

    with connect_database(database, create=False) as connection:
        genre_lookup = {
            str(row["genre"]).casefold(): str(row["genre"])
            for row in connection.execute("SELECT genre FROM taxonomy_genre_counts")
        }
        style_lookup = {
            str(row["style"]).casefold(): str(row["style"])
            for row in connection.execute("SELECT style FROM taxonomy_style_counts")
        }
        discogs_terms = {
            *(
                key
                for genre in genre_lookup.values()
                for key in normalized_taxonomy_keys(genre)
            ),
            *(
                key
                for style in style_lookup.values()
                for key in normalized_taxonomy_keys(style)
            ),
        }
        external_styles = {
            str(row["style"]).casefold(): (
                str(row["style"]),
                str(row["parent_genre"]),
                str(row["source"]),
                str(row["source_term"]),
            )
            for row in connection.execute(
                """
                SELECT style, parent_genre, source, source_term
                FROM taxonomy_external_styles
                """
            )
        }

        invalid_rows: list[str] = []
        for line_number, row in review_rows:
            summary.rows_read += 1
            source = row.get("source", "").strip()
            term = row.get("term", "")
            canonical = row.get("canonical", "")
            canonical_kind = row.get("canonical_kind", "").casefold()
            status = row.get("status", "").casefold()
            action = row.get("action", "")

            if (
                not source
                and not term
                and not canonical
                and not canonical_kind
                and not status
                and not action
            ):
                continue
            source_error = source_name_error(source)
            if source_error:
                invalid_rows.append(f"line {line_number}: source {source_error}")
                continue
            if not term:
                invalid_rows.append(f"line {line_number}: term is required")
                continue
            if status not in MUSICBRAINZ_REVIEW_STATUSES:
                invalid_rows.append(
                    f"line {line_number}: status must be one of "
                    f"{sorted(MUSICBRAINZ_REVIEW_STATUSES)}"
                )
                continue

            if status == "exact":
                summary.exact_rows += 1
            else:
                summary.new_style_rows += 1

            normalized_action = action.casefold()
            if status == "exact":
                if normalized_action == "ignore":
                    summary.ignored_rows += 1
                    continue
                if normalized_action != "none":
                    invalid_rows.append(
                        f"line {line_number}: exact rows must use action none or ignore"
                    )
                    continue
                canonical_kind = infer_canonical_kind(
                    canonical=canonical,
                    canonical_kind=canonical_kind,
                    genre_lookup=genre_lookup,
                    style_lookup=style_lookup,
                )
                if not canonical:
                    invalid_rows.append(f"line {line_number}: exact rows require canonical")
                    continue
                if canonical_kind not in CANONICAL_KINDS:
                    invalid_rows.append(
                        f"line {line_number}: canonical_kind must be genre or style"
                    )
                    continue
                canonical_value = resolve_existing_canonical(
                    canonical=canonical,
                    canonical_kind=canonical_kind,
                    genre_lookup=genre_lookup,
                    style_lookup=style_lookup,
                )
                if canonical_value is None:
                    invalid_rows.append(
                        f"line {line_number}: canonical {canonical!r} is not a Discogs "
                        f"{canonical_kind}"
                    )
                    continue
                source_result = upsert_taxonomy_term_source(
                    connection,
                    source=source,
                    source_term=term,
                    canonical=canonical_value,
                    canonical_kind=canonical_kind,
                    status="exact",
                )
                update_source_link_summary(summary, source_result)
                summary.no_action_rows += 1
                continue

            if normalized_action == "ignore":
                summary.ignored_rows += 1
                continue
            if normalized_action == "none":
                summary.no_action_rows += 1
                continue
            if not normalized_action.startswith("add/"):
                invalid_rows.append(
                    f"line {line_number}: action must be none, ignore, or add/<genre>"
                )
                continue

            parent_value = action.split("/", 1)[1].strip()
            parent_genre = genre_lookup.get(parent_value.casefold())
            if not parent_genre:
                invalid_rows.append(
                    f"line {line_number}: unknown Discogs parent genre {parent_value!r}"
                )
                continue
            if canonical_kind and canonical_kind != "style":
                invalid_rows.append(
                    f"line {line_number}: new_style add rows must use canonical_kind style"
                )
                continue

            style = canonical or title_case_musicbrainz_style(term)
            style_key = style.casefold()
            summary.add_actions += 1
            if any(key in discogs_terms for key in normalized_taxonomy_keys(style)):
                summary.styles_already_discogs += 1
                continue

            existing = external_styles.get(style_key)
            desired = (style, parent_genre, source, term)
            if existing == desired:
                summary.styles_already_present += 1
                source_result = upsert_taxonomy_term_source(
                    connection,
                    source=source,
                    source_term=term,
                    canonical=style,
                    canonical_kind="style",
                    status="added",
                )
                update_source_link_summary(summary, source_result)
                continue
            if existing:
                connection.execute(
                    """
                    UPDATE taxonomy_external_styles
                    SET style = ?,
                        parent_genre = ?,
                        source = ?,
                        source_term = ?
                    WHERE style = ? COLLATE NOCASE
                    """,
                    (*desired, existing[0]),
                )
                external_styles[style_key] = desired
                summary.styles_updated += 1
                source_result = upsert_taxonomy_term_source(
                    connection,
                    source=source,
                    source_term=term,
                    canonical=style,
                    canonical_kind="style",
                    status="added",
                )
                update_source_link_summary(summary, source_result)
                continue

            connection.execute(
                """
                INSERT INTO taxonomy_external_styles (
                    style,
                    parent_genre,
                    source,
                    source_term
                ) VALUES (?, ?, ?, ?)
                """,
                desired,
            )
            external_styles[style_key] = desired
            summary.styles_added += 1

            source_result = upsert_taxonomy_term_source(
                connection,
                source=source,
                source_term=term,
                canonical=style,
                canonical_kind="style",
                status="added",
            )
            update_source_link_summary(summary, source_result)

        if invalid_rows:
            details = "\n".join(invalid_rows[:20])
            suffix = "" if len(invalid_rows) <= 20 else f"\n... {len(invalid_rows) - 20} more"
            raise ValueError(f"invalid review file:\n{details}{suffix}")

        connection.commit()

    return summary


def infer_canonical_kind(
    *,
    canonical: str,
    canonical_kind: str,
    genre_lookup: dict[str, str],
    style_lookup: dict[str, str],
) -> str:
    if canonical_kind:
        return canonical_kind
    key = canonical.casefold()
    if key in genre_lookup:
        return "genre"
    if key in style_lookup:
        return "style"
    return ""


def resolve_existing_canonical(
    *,
    canonical: str,
    canonical_kind: str,
    genre_lookup: dict[str, str],
    style_lookup: dict[str, str],
) -> str | None:
    if canonical_kind == "genre":
        return genre_lookup.get(canonical.casefold())
    if canonical_kind == "style":
        return style_lookup.get(canonical.casefold())
    return None


def upsert_taxonomy_term_source(
    connection,
    *,
    source: str,
    source_term: str,
    canonical: str,
    canonical_kind: str,
    status: str,
) -> str:
    existing = connection.execute(
        """
        SELECT canonical, canonical_kind, status
        FROM taxonomy_term_sources
        WHERE source = ?
            AND source_term = ? COLLATE NOCASE
            AND canonical_kind = ?
        """,
        (source, source_term, canonical_kind),
    ).fetchone()
    desired = (canonical, canonical_kind, status)
    if existing is None:
        connection.execute(
            """
            INSERT INTO taxonomy_term_sources (
                source,
                source_term,
                canonical,
                canonical_kind,
                status
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (source, source_term, canonical, canonical_kind, status),
        )
        return "inserted"

    current = (
        str(existing["canonical"]),
        str(existing["canonical_kind"]),
        str(existing["status"]),
    )
    if current == desired:
        return "unchanged"

    connection.execute(
        """
        UPDATE taxonomy_term_sources
        SET canonical = ?,
            canonical_kind = ?,
            status = ?
        WHERE source = ? AND source_term = ? COLLATE NOCASE
            AND canonical_kind = ?
        """,
        (canonical, canonical_kind, status, source, source_term, canonical_kind),
    )
    return "updated"


def update_source_link_summary(summary: MusicBrainzMergeSummary, result: str) -> None:
    if result == "inserted":
        summary.source_links_added += 1
    elif result == "updated":
        summary.source_links_updated += 1
    elif result == "unchanged":
        summary.source_links_already_present += 1


def read_musicbrainz_terms(source: Path) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for line in source.read_text().splitlines():
        term = line.strip()
        if not term:
            continue
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)
    return terms


def load_discogs_canonical_lookup(connection) -> dict[str, tuple[str, str]]:
    lookup: dict[str, tuple[str, str]] = {}
    for row in connection.execute("SELECT genre FROM taxonomy_genre_counts ORDER BY lower(genre)"):
        genre = str(row["genre"])
        for key in normalized_taxonomy_keys(genre):
            lookup.setdefault(key, (genre, "genre"))
    for row in connection.execute("SELECT style FROM taxonomy_style_counts ORDER BY lower(style)"):
        style = str(row["style"])
        for key in normalized_taxonomy_keys(style):
            lookup.setdefault(key, (style, "style"))
    return lookup


def normalized_taxonomy_keys(value: str) -> list[str]:
    normalized = normalize_text(value)
    compact = normalized.replace(" ", "")
    keys = [value.strip().casefold(), normalized, compact]
    seen: dict[str, str] = {}
    for key in keys:
        if key:
            seen.setdefault(key, key)
    return list(seen.values())


def first_lookup_match(
    lookup: dict[str, tuple[str, str]],
    keys: list[str],
) -> tuple[str, str] | None:
    for key in keys:
        match = lookup.get(key)
        if match:
            return match
    return None


def title_case_musicbrainz_style(term: str) -> str:
    return term.title()


def read_musicbrainz_review_rows(review_file: Path) -> list[tuple[int, dict[str, str]]]:
    text = review_file.read_text()
    if not text.strip():
        return []

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters="\t,")
    except csv.Error:
        dialect = csv.excel_tab if "\t" in text.splitlines()[0] else csv.excel

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    fieldnames = {field.strip() for field in (reader.fieldnames or [])}
    required = set(MUSICBRAINZ_REVIEW_HEADERS) - OPTIONAL_MUSICBRAINZ_REVIEW_HEADERS
    missing = sorted(required - fieldnames)
    if missing:
        raise ValueError(f"review file missing required columns: {', '.join(missing)}")

    rows: list[tuple[int, dict[str, str]]] = []
    for offset, raw_row in enumerate(reader, start=2):
        row = {
            key.strip(): (value or "").strip()
            for key, value in raw_row.items()
            if key is not None
        }
        rows.append((offset, row))
    return rows


def export_taxonomy_tsv(database: Path, output: Path) -> list[TaxonomyTsvRow]:
    rows = build_taxonomy_tsv_rows(database)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(format_taxonomy_tsv(rows))
    return rows


def build_taxonomy_tsv_rows(database: Path) -> list[TaxonomyTsvRow]:
    with connect_database(database, create=False) as connection:
        discogs_source = read_discogs_source(connection)
        if not discogs_source:
            raise ValueError(
                "build database missing Discogs source; rerun build-discogs with --source"
            )
        genre_counts = {
            str(row["genre"]): int(row["count"])
            for row in connection.execute(
                "SELECT genre, count FROM taxonomy_genre_counts ORDER BY lower(genre)"
            )
        }
        style_counts = {
            str(row["style"]): int(row["count"])
            for row in connection.execute(
                "SELECT style, count FROM taxonomy_style_counts ORDER BY lower(style)"
            )
        }
        style_parents = {
            str(row["style"]): str(row["parent_genre"])
            for row in connection.execute(
                """
                SELECT style, parent_genre
                FROM taxonomy_style_parents
                WHERE parent_genre IS NOT NULL AND parent_genre != ''
                ORDER BY lower(style)
                """
            )
        }
        external_style_parents = {
            str(row["style"]): str(row["parent_genre"])
            for row in connection.execute(
                """
                SELECT style, parent_genre
                FROM taxonomy_external_styles
                ORDER BY lower(style)
                """
            )
        }
        source_rows = [
            (
                str(row["source"]),
                str(row["source_term"]),
                str(row["canonical"]),
                str(row["canonical_kind"]),
                str(row["status"]),
            )
            for row in connection.execute(
                """
                SELECT source, source_term, canonical, canonical_kind, status
                FROM taxonomy_term_sources
                WHERE status <> 'native'
                ORDER BY lower(canonical_kind), lower(canonical), lower(source), lower(source_term)
                """
            )
        ]

    rows = [
        TaxonomyTsvRow(
            kind="genre",
            name=genre,
            source=discogs_source,
            source_term=genre,
            status="native",
            count=str(count),
        )
        for genre, count in genre_counts.items()
    ]
    rows.extend(
        TaxonomyTsvRow(
            kind="style",
            name=style,
            parent=style_parents.get(style, ""),
            source=discogs_source,
            source_term=style,
            status="native",
            count=str(count),
        )
        for style, count in style_counts.items()
    )
    for source, source_term, canonical, canonical_kind, status in source_rows:
        parent = ""
        if canonical_kind == "style":
            parent = style_parents.get(canonical, external_style_parents.get(canonical, ""))
        rows.append(
            TaxonomyTsvRow(
                kind=canonical_kind,
                name=canonical,
                parent=parent,
                source=source,
                source_term=source_term,
                status=status,
                count=str(style_counts.get(canonical) or genre_counts.get(canonical) or ""),
            )
        )
    return sorted(
        rows,
        key=lambda row: (
            0 if row.kind == "genre" else 1,
            row.name.casefold(),
            0 if row.status == "native" and row.source == discogs_source else 1,
            row.source.casefold(),
            row.source_term.casefold(),
        ),
    )


def sorted_counter(counter: Counter[str]) -> list[tuple[str, int]]:
    return sorted(counter.items(), key=lambda item: (-item[1], item[0].casefold()))


def sorted_triples(counter: Counter[tuple[str, str]]) -> list[tuple[str, str, int]]:
    return sorted(
        ((left, right, count) for (left, right), count in counter.items()),
        key=lambda item: (-item[2], item[0].casefold(), item[1].casefold()),
    )


def infer_style_parents(
    *,
    style_counter: Counter[str],
    genre_counter: Counter[str],
    genre_style_counter: Counter[tuple[str, str]],
) -> list[InferredStyleParent]:
    candidates_by_style: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for (genre, style), count in genre_style_counter.items():
        candidates_by_style[style].append((genre, count))

    inferred: list[InferredStyleParent] = []
    for style, style_count in sorted(
        style_counter.items(),
        key=lambda item: (-item[1], item[0].casefold()),
    ):
        candidates = [
            (
                genre,
                count,
                count / style_count,
                (count / genre_counter[genre]) if genre_counter[genre] else 0.0,
            )
            for genre, count in candidates_by_style.get(style, [])
        ]
        candidates.sort(key=lambda item: (-item[1], -item[3], item[0].casefold()))

        if candidates:
            parent_genre, parent_count, parent_style_share, parent_genre_share = candidates[0]
        else:
            parent_genre = None
            parent_count = 0
            parent_style_share = 0.0
            parent_genre_share = 0.0

        inferred.append(
            InferredStyleParent(
                style=style,
                style_count=style_count,
                parent_genre=parent_genre,
                parent_count=parent_count,
                parent_style_share=parent_style_share,
                parent_genre_share=parent_genre_share,
                candidate_genres=candidates,
            )
        )
    return inferred
