from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .build import (
    build_master_taxonomy_report,
    build_musicbrainz_review_rows,
    export_taxonomy_tsv,
    format_musicbrainz_review_tsv,
    merge_musicbrainz_review,
    store_master_taxonomy_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.taxonomy",
        description="Build the packaged Kukicha taxonomy TSV.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_discogs = subparsers.add_parser(
        "build-discogs",
        help="Parse a Discogs masters XML dump into the taxonomy build database.",
    )
    build_discogs.add_argument("--discog-masters", required=True, type=Path)
    build_discogs.add_argument(
        "--source",
        required=True,
        type=source_name,
        help="Versioned source name to write into exported taxonomy rows.",
    )
    build_discogs.add_argument("--database", required=True, type=Path)
    build_discogs.add_argument("--json", action="store_true")
    build_discogs.set_defaults(func=run_build_discogs)

    review = subparsers.add_parser(
        "musicbrainz-review",
        help="Print a MusicBrainz genre review TSV against the build database.",
    )
    review.add_argument("--database", required=True, type=Path)
    review.add_argument("--terms", required=True, type=Path)
    review.add_argument(
        "--source",
        required=True,
        type=source_name,
        help="Versioned source name to write into the review TSV.",
    )
    review.set_defaults(func=run_musicbrainz_review)

    merge = subparsers.add_parser(
        "merge-review",
        help="Merge a reviewed MusicBrainz TSV into the build database.",
    )
    merge.add_argument("--database", required=True, type=Path)
    merge.add_argument("--review-file", required=True, type=Path)
    merge.add_argument("--json", action="store_true")
    merge.set_defaults(func=run_merge_review)

    export = subparsers.add_parser(
        "export",
        help="Export the authoritative runtime taxonomy TSV.",
    )
    export.add_argument("--database", required=True, type=Path)
    export.add_argument("--output", required=True, type=Path)
    export.add_argument("--json", action="store_true")
    export.set_defaults(func=run_export)

    return parser


def run_build_discogs(args: argparse.Namespace) -> int:
    progress = ProgressLogger()
    progress(f"building taxonomy tables from {args.discog_masters}")
    report = build_master_taxonomy_report(args.discog_masters, tick=progress.dot)
    store_master_taxonomy_report(args.database, report, source_name=args.source)
    payload = {
        "database": str(args.database.resolve()),
        "source": args.source,
        "discog_masters_source": str(args.discog_masters.resolve()),
        "masters_processed": report.masters_processed,
        "genres_in_taxonomy": len(report.genre_counts),
        "styles_in_taxonomy": len(report.style_counts),
    }
    emit(payload if args.json else format_build_summary(payload), pretty=not args.json)
    return 0


def run_musicbrainz_review(args: argparse.Namespace) -> int:
    rows = build_musicbrainz_review_rows(args.database, args.terms, args.source)
    emit(format_musicbrainz_review_tsv(rows), pretty=True)
    return 0


def run_merge_review(args: argparse.Namespace) -> int:
    try:
        summary = merge_musicbrainz_review(args.database, args.review_file)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    payload = summary.to_dict()
    emit(payload if args.json else format_merge_summary(payload), pretty=not args.json)
    return 0


def run_export(args: argparse.Namespace) -> int:
    try:
        rows = export_taxonomy_tsv(args.database, args.output)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    genres = {row.name.casefold() for row in rows if row.kind == "genre"}
    styles = {row.name.casefold() for row in rows if row.kind == "style"}
    payload = {
        "database": str(args.database.resolve()),
        "output": str(args.output.resolve()),
        "rows": len(rows),
        "genres": len(genres),
        "styles": len(styles),
    }
    emit(payload if args.json else format_export_summary(payload), pretty=not args.json)
    return 0


def format_build_summary(payload: dict[str, object]) -> str:
    return "\n".join(
        [
            f"database: {payload['database']}",
            f"source: {payload['source']}",
            f"discog masters source: {payload['discog_masters_source']}",
            f"masters processed: {payload['masters_processed']}",
            f"taxonomy genres: {payload['genres_in_taxonomy']}",
            f"taxonomy styles: {payload['styles_in_taxonomy']}",
        ]
    )


def format_merge_summary(payload: dict[str, object]) -> str:
    return "\n".join(
        [
            f"database: {payload['database']}",
            f"review file: {payload['review_file']}",
            f"rows read: {payload['rows_read']}",
            f"exact rows: {payload['exact_rows']}",
            f"new style rows: {payload['new_style_rows']}",
            f"ignored rows: {payload['ignored_rows']}",
            f"no-action rows: {payload['no_action_rows']}",
            f"add actions: {payload['add_actions']}",
            f"styles added: {payload['styles_added']}",
            f"styles updated: {payload['styles_updated']}",
            f"styles already present: {payload['styles_already_present']}",
            f"styles already in Discogs taxonomy: {payload['styles_already_discogs']}",
            f"source links added: {payload['source_links_added']}",
            f"source links updated: {payload['source_links_updated']}",
            f"source links already present: {payload['source_links_already_present']}",
        ]
    )


def format_export_summary(payload: dict[str, object]) -> str:
    return "\n".join(
        [
            f"database: {payload['database']}",
            f"output: {payload['output']}",
            f"rows: {payload['rows']}",
            f"genres: {payload['genres']}",
            f"styles: {payload['styles']}",
        ]
    )


def source_name(value: str) -> str:
    source = value.strip()
    if not source:
        raise argparse.ArgumentTypeError("source must not be empty")
    if any(character in source for character in "\t\r\n"):
        raise argparse.ArgumentTypeError("source must not contain tabs or newlines")
    return source


def emit(payload: object, *, pretty: bool) -> None:
    if isinstance(payload, str):
        print(payload)
        return
    indent = 2 if pretty else None
    print(json.dumps(payload, indent=indent, sort_keys=pretty))


class ProgressLogger:
    def __init__(self, *, dot_wrap: int = 55) -> None:
        self.dot_wrap = dot_wrap
        self.dot_column = 0
        self.has_open_dot_line = False

    def __call__(self, message: str) -> None:
        self._flush_dot_line()
        print(f"[progress] {message}", file=sys.stderr)

    def dot(self) -> None:
        print(".", end="", file=sys.stderr, flush=True)
        self.has_open_dot_line = True
        self.dot_column += 1
        if self.dot_column >= self.dot_wrap:
            self._flush_dot_line()

    def _flush_dot_line(self) -> None:
        if self.has_open_dot_line:
            print(file=sys.stderr)
            self.has_open_dot_line = False
            self.dot_column = 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
