from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path
from typing import Sequence


DEFAULT_SPEC_DIR = Path("docs/specs")
DEFAULT_MARGIN = "0.8in"
DEFAULT_PDF_ENGINES = (
    "typst",
    "xelatex",
    "lualatex",
    "pdflatex",
    "wkhtmltopdf",
    "weasyprint",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.specs_to_pdf",
        description="Convert docs/specs/*.md files to sibling PDF files.",
    )
    parser.add_argument(
        "--spec-dir",
        type=Path,
        default=DEFAULT_SPEC_DIR,
        help="Directory containing Markdown specs. Default: docs/specs",
    )
    parser.add_argument(
        "--pandoc",
        default="pandoc",
        help="Pandoc executable name or path. Default: pandoc",
    )
    parser.add_argument(
        "--pdf-engine",
        help=(
            "Pandoc PDF engine. Default: first available of "
            + ", ".join(DEFAULT_PDF_ENGINES)
        ),
    )
    parser.add_argument(
        "--margin",
        default=DEFAULT_MARGIN,
        help=f"PDF page margin passed to Pandoc geometry. Default: {DEFAULT_MARGIN}",
    )
    parser.add_argument(
        "--no-toc",
        action="store_true",
        help="Do not include a table of contents.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = repo_root()
    spec_dir = resolve_from_root(root, args.spec_dir)
    pandoc = resolve_pandoc(args.pandoc)
    pdf_engine = resolve_pdf_engine(args.pdf_engine)
    markdown_files = sorted(spec_dir.glob("*.md"), key=lambda path: path.name.casefold())

    if not markdown_files:
        raise SystemExit(f"no Markdown files found in {spec_dir}")

    for markdown_path in markdown_files:
        pdf_path = markdown_path.with_suffix(".pdf")
        command = pandoc_command(
            pandoc,
            markdown_path,
            pdf_path,
            margin=args.margin,
            include_toc=not args.no_toc,
            pdf_engine=pdf_engine,
        )
        print(
            "converting "
            f"{display_path(root, markdown_path)} -> {display_path(root, pdf_path)}"
        )
        subprocess.run(command, cwd=root, check=True)

    return 0


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_from_root(root: Path, path: Path) -> Path:
    if path.is_absolute():
        return path
    return root / path


def resolve_pandoc(value: str) -> str:
    if Path(value).is_absolute():
        if Path(value).exists():
            return value
        raise SystemExit(f"pandoc executable not found: {value}")
    resolved = shutil.which(value)
    if resolved is None:
        raise SystemExit(
            "pandoc executable not found. Install it with: brew install pandoc"
        )
    return resolved


def resolve_pdf_engine(value: str | None) -> str:
    if value:
        return value
    for candidate in DEFAULT_PDF_ENGINES:
        if shutil.which(candidate) is not None:
            return candidate
    raise SystemExit(
        "no Pandoc PDF engine found. Install one with either "
        "`brew install typst` or `brew install --cask mactex-no-gui`, "
        "then rerun this command."
    )


def pandoc_command(
    pandoc: str,
    markdown_path: Path,
    pdf_path: Path,
    *,
    margin: str,
    include_toc: bool,
    pdf_engine: str | None,
) -> list[str]:
    command = [
        pandoc,
        "-f",
        "gfm",
        str(markdown_path),
        "-o",
        str(pdf_path),
        "-V",
        f"geometry:margin={margin}",
    ]
    if include_toc:
        command.append("--toc")
    if pdf_engine:
        command.extend(("--pdf-engine", pdf_engine))
    return command


def display_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
