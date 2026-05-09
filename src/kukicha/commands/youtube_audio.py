from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yt_dlp
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import DownloadError

from ..player_config import PlayerServerOptions, load_player_options
from ..player_errors import PlayerConfigError


STRICT_AUDIO_FORMAT = "bestaudio[vcodec=none][acodec!=none]"
CHAPTER_TIMESTAMP_PATTERN = r"\d+(?::\d{2}){1,2}(?:\.\d+)?"
CHAPTER_LINE_RE = re.compile(
    rf"^\s*(?P<timestamp>{CHAPTER_TIMESTAMP_PATTERN})(?:(?:\s*-\s*)|\s+)(?P<title>.+?)\s*$"
)
CHAPTER_TIMESTAMP_ONLY_RE = re.compile(
    rf"^\s*(?P<timestamp>{CHAPTER_TIMESTAMP_PATTERN})\s*-?\s*$"
)


@dataclass(frozen=True, slots=True)
class YoutubeAudioTools:
    ffmpeg: str
    ffprobe: str
    deno: str


@dataclass(frozen=True, slots=True)
class YoutubeAudioDownloadResult:
    output_dir: Path
    files_written: int
    media_id: str
    title: str
    mode: str
    chapters_reported: int
    items_reported: int | None = None


class ManualChaptersPP(PostProcessor):
    def __init__(
        self,
        chapters: Sequence[dict[str, Any]],
        downloader: yt_dlp.YoutubeDL | None = None,
    ) -> None:
        super().__init__(downloader)
        self._chapters = tuple(chapter.copy() for chapter in chapters)

    def run(self, information: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        information = information.copy()
        information["chapters"] = [chapter.copy() for chapter in self._chapters]
        return [], information


def run_youtube_download_audio(args: argparse.Namespace) -> int:
    try:
        options = load_player_options(args.config)
        result = download_youtube_audio(
            args.url,
            options=options,
            verbose=args.verbose,
            chapters_file=args.chapters_file,
            status=print,
        )
    except PlayerConfigError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except DownloadError as error:
        print(f"yt-dlp failed: {error}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print("External command failed.", file=sys.stderr)
        print("Command:", " ".join(error.cmd), file=sys.stderr)
        if error.stderr:
            print(error.stderr, file=sys.stderr)
        return 1
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(f"Done. Final audio files written to: {result.output_dir}")
    return 0


def add_youtube_download_audio_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "yt-download-audio",
        help="Download audio-only YouTube video or playlist files.",
    )
    add_youtube_download_audio_arguments(parser)
    parser.set_defaults(func=run_youtube_download_audio)
    return parser


def add_youtube_download_audio_arguments(
    parser: argparse.ArgumentParser,
) -> None:
    parser.add_argument("url", help="YouTube video or playlist URL")
    parser.add_argument(
        "--chapters-file",
        type=Path,
        help=(
            "Path to a manual chapter file for video URLs, with one line "
            "per chapter: 'TIMESTAMP Title'."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable yt-dlp verbose logging.",
    )


def build_standalone_parser(
    argv: Sequence[str] | None = None,
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yt-download-audio",
        description=(
            "Strictly download audio-only YouTube media, split video URLs by "
            "chapters, and download playlist URLs as one file per item."
        ),
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        help="Path to the TOML config file.",
    )
    add_youtube_download_audio_arguments(parser)
    parser.set_defaults(func=run_youtube_download_audio)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = build_standalone_parser(arguments)
    args = parser.parse_args(arguments)
    return args.func(args)


def download_youtube_audio(
    url: str,
    *,
    options: PlayerServerOptions,
    verbose: bool = False,
    chapters_file: Path | None = None,
    manual_chapters: Sequence[dict[str, Any]] | None = None,
    status: Callable[[str], None] | None = None,
) -> YoutubeAudioDownloadResult:
    if chapters_file is not None and manual_chapters is not None:
        raise ValueError("chapters_file and manual_chapters cannot both be provided")

    output_base = require_youtube_download_path(options)
    tools = resolve_youtube_audio_tools(options)
    prepare_youtube_download_path(output_base)

    info = extract_youtube_info(
        url,
        tools=tools,
        verbose=verbose,
    )
    if is_playlist_info(info):
        if chapters_file is not None or manual_chapters is not None:
            raise RuntimeError("--chapters-file cannot be used with playlist URLs")
        return download_youtube_playlist_audio(
            url,
            info=info,
            output_base=output_base,
            tools=tools,
            verbose=verbose,
            status=status,
        )

    chapters = (
        parse_chapters_file(chapters_file)
        if chapters_file is not None
        else manual_chapters
    )
    return download_youtube_video_audio_chapters(
        url,
        info=info,
        output_base=output_base,
        tools=tools,
        verbose=verbose,
        manual_chapters=chapters,
        status=status,
    )


def download_youtube_video_audio_chapters(
    url: str,
    *,
    info: dict[str, Any],
    output_base: Path,
    tools: YoutubeAudioTools,
    verbose: bool,
    manual_chapters: Sequence[dict[str, Any]] | None = None,
    status: Callable[[str], None] | None = None,
) -> YoutubeAudioDownloadResult:
    video_id = info.get("id") or "unknown-id"
    title = info.get("title") or "untitled"
    reported_chapters = info.get("chapters") or []
    chapters = (
        [chapter.copy() for chapter in manual_chapters]
        if manual_chapters is not None
        else reported_chapters
    )

    if not chapters:
        raise RuntimeError("yt-dlp did not report any chapters for this video")

    final_album_dir = output_base / safe_path_component(
        f"{title} [{video_id}]",
        fallback=video_id,
    )

    with tempfile.TemporaryDirectory(prefix="kukicha-youtube-audio-") as tempdir:
        temp_root = Path(tempdir)
        stage_root = temp_root / "stage"
        ytdlp_temp_dir = temp_root / "yt-dlp"

        emit = status or (lambda _message: None)
        emit(f"Video: {title} [{video_id}]")
        emit(f"Chapters reported by yt-dlp: {len(reported_chapters)}")
        if manual_chapters is not None:
            emit(f"Chapters supplied from file: {len(chapters)}")
        emit(f"Using Deno: {tools.deno}")
        emit("Using yt-dlp remote component: ejs:github")
        emit(f"Temporary stage directory: {stage_root}")
        emit(f"Final output directory: {final_album_dir}")

        download_and_split_chapters(
            url,
            stage_root=stage_root,
            temp_dir=ytdlp_temp_dir,
            tools=tools,
            verbose=verbose,
            manual_chapters=chapters if manual_chapters is not None else None,
        )

        chapter_files = find_stage_chapter_files(stage_root, tools=tools)
        if len(chapter_files) != len(chapters):
            raise RuntimeError(
                f"expected {len(chapters)} chapter file(s), "
                f"but found {len(chapter_files)} staged audio file(s)"
            )

        copy_stage_audio_files(
            chapter_files,
            output_dir=final_album_dir,
            tools=tools,
            status=emit,
        )

    return YoutubeAudioDownloadResult(
        output_dir=final_album_dir,
        files_written=len(chapter_files),
        media_id=video_id,
        title=title,
        mode="video",
        chapters_reported=len(reported_chapters),
    )


def download_youtube_playlist_audio(
    url: str,
    *,
    info: dict[str, Any],
    output_base: Path,
    tools: YoutubeAudioTools,
    verbose: bool,
    status: Callable[[str], None] | None = None,
) -> YoutubeAudioDownloadResult:
    playlist_id = info.get("id") or "unknown-playlist"
    title = info.get("title") or "untitled"
    item_count = playlist_item_count(info)
    if item_count == 0:
        raise RuntimeError("yt-dlp did not report any items for this playlist")

    final_playlist_dir = output_base / safe_path_component(
        f"{title} [{playlist_id}]",
        fallback=playlist_id,
    )

    with tempfile.TemporaryDirectory(prefix="kukicha-youtube-audio-") as tempdir:
        temp_root = Path(tempdir)
        stage_root = temp_root / "stage"
        ytdlp_temp_dir = temp_root / "yt-dlp"

        emit = status or (lambda _message: None)
        emit(f"Playlist: {title} [{playlist_id}]")
        if item_count is not None:
            emit(f"Playlist items reported by yt-dlp: {item_count}")
        emit(f"Using Deno: {tools.deno}")
        emit("Using yt-dlp remote component: ejs:github")
        emit(f"Temporary stage directory: {stage_root}")
        emit(f"Final output directory: {final_playlist_dir}")

        download_playlist_audio_items(
            url,
            stage_root=stage_root,
            temp_dir=ytdlp_temp_dir,
            tools=tools,
            verbose=verbose,
        )

        item_files = find_stage_playlist_files(stage_root, tools=tools)
        if item_count is not None and len(item_files) != item_count:
            raise RuntimeError(
                f"expected {item_count} playlist item file(s), "
                f"but found {len(item_files)} staged audio file(s)"
            )
        if not item_files:
            raise RuntimeError("yt-dlp did not stage any playlist audio files")

        copy_stage_audio_files(
            item_files,
            output_dir=final_playlist_dir,
            tools=tools,
            status=emit,
        )

    return YoutubeAudioDownloadResult(
        output_dir=final_playlist_dir,
        files_written=len(item_files),
        media_id=playlist_id,
        title=title,
        mode="playlist",
        chapters_reported=0,
        items_reported=item_count,
    )


def is_playlist_info(info: dict[str, Any]) -> bool:
    media_type = info.get("_type")
    return (
        media_type in {"playlist", "multi_video"}
        or info.get("entries") is not None
    )


def playlist_item_count(info: dict[str, Any]) -> int | None:
    entries = info.get("entries")
    if entries is not None and not isinstance(entries, (str, bytes)):
        try:
            return len(entries)
        except TypeError:
            pass

    for key in ("playlist_count", "n_entries"):
        value = info.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdecimal():
            return int(value)

    return None


def parse_chapters_file(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"chapters file not found: {path}")
    if not path.is_file():
        raise RuntimeError(f"chapters file is not a file: {path}")

    chapters: list[dict[str, Any]] = []
    previous_start: float | int | None = None

    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if CHAPTER_TIMESTAMP_ONLY_RE.match(line):
            raise ValueError(f"{path}: chapter line {line_number} is missing a title")

        match = CHAPTER_LINE_RE.match(line)
        if not match:
            raise ValueError(
                f"{path}: chapter line {line_number} must use 'TIMESTAMP Title'"
            )

        try:
            start_time = parse_chapter_timestamp(match.group("timestamp"))
        except ValueError as error:
            raise ValueError(f"{path}: chapter line {line_number}: {error}") from error

        title = match.group("title").strip()
        if not title:
            raise ValueError(f"{path}: chapter line {line_number} is missing a title")

        if previous_start is not None and start_time <= previous_start:
            raise ValueError(
                f"{path}: chapter line {line_number} timestamp must be greater "
                "than the previous chapter"
            )

        chapters.append({"start_time": start_time, "title": title})
        previous_start = start_time

    if not chapters:
        raise ValueError(f"chapters file did not contain any chapters: {path}")

    for index, chapter in enumerate(chapters[:-1]):
        chapter["end_time"] = chapters[index + 1]["start_time"]

    return chapters


def parse_chapter_timestamp(value: str) -> float | int:
    if not re.fullmatch(CHAPTER_TIMESTAMP_PATTERN, value):
        raise ValueError(f"invalid timestamp: {value}")

    parts = value.split(":")
    if len(parts) == 2:
        minutes_text, seconds_text = parts
        hours = 0
        minutes = int(minutes_text)
    elif len(parts) == 3:
        hours_text, minutes_text, seconds_text = parts
        hours = int(hours_text)
        minutes = int(minutes_text)
        if minutes >= 60:
            raise ValueError(f"invalid timestamp minutes: {value}")
    else:
        raise ValueError(f"invalid timestamp: {value}")

    seconds = float(seconds_text) if "." in seconds_text else int(seconds_text)
    if seconds >= 60:
        raise ValueError(f"invalid timestamp seconds: {value}")

    total = (hours * 3600) + (minutes * 60) + seconds
    if isinstance(total, float) and total.is_integer():
        return int(total)
    return total


def require_youtube_download_path(options: PlayerServerOptions) -> Path:
    if options.youtube_download_path is None:
        raise PlayerConfigError("YoutubeDownloadPath must be set in the config file")
    return options.youtube_download_path


def prepare_youtube_download_path(path: Path) -> None:
    if path.exists() and not path.is_dir():
        raise NotADirectoryError(f"YoutubeDownloadPath is not a directory: {path}")
    path.mkdir(parents=True, exist_ok=True)


def resolve_youtube_audio_tools(options: PlayerServerOptions) -> YoutubeAudioTools:
    ffmpeg = resolve_ffmpeg_path(options)
    ffprobe = resolve_ffprobe_path(ffmpeg, configured_ffmpeg=options.ffmpeg_path)
    deno = require_on_path("deno")
    check_deno_version(deno)
    return YoutubeAudioTools(ffmpeg=ffmpeg, ffprobe=ffprobe, deno=deno)


def resolve_ffmpeg_path(options: PlayerServerOptions) -> str:
    if options.ffmpeg_path is None:
        return require_on_path("ffmpeg")
    return require_executable_path(options.ffmpeg_path, label="ffmpeg")


def resolve_ffprobe_path(ffmpeg: str, *, configured_ffmpeg: Path | None) -> str:
    if configured_ffmpeg is not None:
        sibling = Path(ffmpeg).with_name("ffprobe")
        if sibling.exists():
            return require_executable_path(sibling, label="ffprobe")
    return require_on_path("ffprobe")


def require_executable_path(path: Path, *, label: str) -> str:
    if not path.exists():
        raise RuntimeError(f"{label} path does not exist: {path}")
    if not path.is_file():
        raise RuntimeError(f"{label} path is not a file: {path}")
    if not os.access(path, os.X_OK):
        raise RuntimeError(f"{label} path is not executable: {path}")
    return str(path)


def require_on_path(program: str) -> str:
    path = shutil.which(program)
    if path is None:
        raise RuntimeError(f"{program!r} was not found on PATH")
    return path


def run(
    cmd: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def parse_version_tuple(text: str) -> tuple[int, ...] | None:
    match = re.search(r"\b(\d+)\.(\d+)(?:\.(\d+))?\b", text)
    if not match:
        return None

    parts = [int(part) for part in match.groups(default="0")]
    return tuple(parts)


def check_deno_version(deno_path: str) -> None:
    result = run([deno_path, "--version"])
    version = parse_version_tuple(result.stdout)

    if version is None:
        raise RuntimeError("could not determine Deno version from `deno --version`")

    if version < (2, 0, 0):
        raise RuntimeError(f"Deno 2.0.0 or newer is required. Found: {version}")


def safe_path_component(value: str, *, fallback: str = "untitled") -> str:
    value = value.strip()
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", " ", value)
    value = value.strip(" .")
    return value or fallback


def ffprobe_json(path: Path, *, tools: YoutubeAudioTools) -> dict[str, Any]:
    result = run(
        [
            tools.ffprobe,
            "-v",
            "error",
            "-show_entries",
            "stream=index,codec_type,codec_name",
            "-of",
            "json",
            str(path),
        ]
    )
    return json.loads(result.stdout)


def audio_codec(path: Path, *, tools: YoutubeAudioTools) -> str:
    data = ffprobe_json(path, tools=tools)
    streams = data.get("streams", [])

    for stream in streams:
        if stream.get("codec_type") == "audio":
            codec = stream.get("codec_name")
            if codec:
                return codec

    raise RuntimeError(f"No audio stream found in: {path}")


def has_audio_stream(path: Path, *, tools: YoutubeAudioTools) -> bool:
    try:
        audio_codec(path, tools=tools)
        return True
    except Exception:
        return False


def assert_audio_only(path: Path, *, tools: YoutubeAudioTools) -> None:
    data = ffprobe_json(path, tools=tools)
    streams = data.get("streams", [])

    if not streams:
        raise RuntimeError(f"No streams found in output file: {path}")

    non_audio_streams = [
        stream for stream in streams if stream.get("codec_type") != "audio"
    ]

    if non_audio_streams:
        raise RuntimeError(f"Output is not audio-only: {path}")


def extension_for_codec(codec: str) -> str:
    codec = codec.lower()

    if codec == "opus":
        return "opus"
    if codec == "aac":
        return "m4a"
    if codec == "mp3":
        return "mp3"
    if codec == "vorbis":
        return "ogg"
    if codec == "flac":
        return "flac"
    if codec == "alac":
        return "m4a"

    return "mka"


def copy_audio_without_transcoding(
    input_path: Path,
    output_path: Path,
    *,
    tools: YoutubeAudioTools,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    run(
        [
            tools.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(input_path),
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-map_chapters",
            "-1",
            "-c:a",
            "copy",
            str(output_path),
        ]
    )


def copy_stage_audio_files(
    input_files: Sequence[Path],
    *,
    output_dir: Path,
    tools: YoutubeAudioTools,
    status: Callable[[str], None],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for input_path in input_files:
        in_codec = audio_codec(input_path, tools=tools)
        ext = extension_for_codec(in_codec)
        final_name = input_path.with_suffix(f".{ext}").name
        final_path = output_dir / final_name

        status(
            "Copying audio stream without transcoding: "
            f"{input_path.name} -> {final_path.name} [{in_codec}]"
        )

        copy_audio_without_transcoding(input_path, final_path, tools=tools)
        out_codec = audio_codec(final_path, tools=tools)
        if out_codec != in_codec:
            raise RuntimeError(
                f"Codec changed for {final_path}: "
                f"input={in_codec}, output={out_codec}"
            )

        assert_audio_only(final_path, tools=tools)


def youtube_ejs_opts(tools: YoutubeAudioTools) -> dict[str, Any]:
    return {
        "js_runtimes": {
            "deno": {
                "path": tools.deno,
            }
        },
        "remote_components": ["ejs:github"],
    }


def base_ydl_opts(
    tools: YoutubeAudioTools,
    *,
    verbose: bool,
    noplaylist: bool = True,
) -> dict[str, Any]:
    return {
        "format": STRICT_AUDIO_FORMAT,
        "noplaylist": noplaylist,
        "quiet": False,
        "no_warnings": False,
        "verbose": verbose,
        **youtube_ejs_opts(tools),
    }


def extract_youtube_info(
    url: str,
    *,
    tools: YoutubeAudioTools,
    verbose: bool,
) -> dict[str, Any]:
    ydl_opts = {
        **base_ydl_opts(tools, verbose=verbose, noplaylist=False),
        "skip_download": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


def download_and_split_chapters(
    url: str,
    *,
    stage_root: Path,
    temp_dir: Path,
    tools: YoutubeAudioTools,
    verbose: bool,
    manual_chapters: Sequence[dict[str, Any]] | None = None,
) -> None:
    stage_root.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    ydl_opts = {
        **base_ydl_opts(tools, verbose=verbose, noplaylist=True),
        "paths": {
            "home": str(stage_root),
            "temp": str(temp_dir),
        },
        "outtmpl": {
            "default": "source/source.%(ext)s",
            "chapter": "chapters/%(section_number)03d - %(section_title)s.%(ext)s",
        },
        "postprocessors": [
            {
                "key": "FFmpegSplitChapters",
                "force_keyframes": False,
            }
        ],
        "restrictfilenames": False,
        "overwrites": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        if manual_chapters is not None:
            ydl.add_post_processor(ManualChaptersPP(manual_chapters), when="pre_process")
        retcode = ydl.download([url])
        if retcode:
            raise RuntimeError(f"yt-dlp returned non-zero exit code: {retcode}")


def download_playlist_audio_items(
    url: str,
    *,
    stage_root: Path,
    temp_dir: Path,
    tools: YoutubeAudioTools,
    verbose: bool,
) -> None:
    stage_root.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    ydl_opts = {
        **base_ydl_opts(tools, verbose=verbose, noplaylist=False),
        "paths": {
            "home": str(stage_root),
            "temp": str(temp_dir),
        },
        "outtmpl": {
            "default": "items/%(playlist_index)03d - %(title)s.%(ext)s",
        },
        "restrictfilenames": False,
        "overwrites": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        retcode = ydl.download([url])
        if retcode:
            raise RuntimeError(f"yt-dlp returned non-zero exit code: {retcode}")


def find_stage_chapter_files(
    stage_root: Path,
    *,
    tools: YoutubeAudioTools,
) -> list[Path]:
    return find_stage_audio_files(stage_root / "chapters", tools=tools)


def find_stage_playlist_files(
    stage_root: Path,
    *,
    tools: YoutubeAudioTools,
) -> list[Path]:
    return find_stage_audio_files(stage_root / "items", tools=tools)


def find_stage_audio_files(
    audio_dir: Path,
    *,
    tools: YoutubeAudioTools,
) -> list[Path]:
    if not audio_dir.exists():
        return []

    files: list[Path] = []
    for path in audio_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.name.endswith((".part", ".ytdl", ".json", ".description")):
            continue
        if has_audio_stream(path, tools=tools):
            files.append(path)

    return sorted(files)
