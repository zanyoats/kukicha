from __future__ import annotations

from io import BytesIO
from unicodedata import east_asian_width
from urllib.parse import quote
from xml.sax.saxutils import escape

PLAYLIST_COVER_TITLE_MAX_COLUMNS = 25
PLAYLIST_COVER_TITLE_ELLIPSIS = "..."


def playlist_cover_svg(title: str) -> str:
    cover_title = playlist_cover_title(title)
    escaped_title = escape(cover_title)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1000">
  <g transform="translate(0,120) scale(0.83)">
    <rect x="45" y="55" width="1110" height="650" rx="28" fill="#101010" stroke="#050505" stroke-width="8"/>

    <rect x="105" y="120" width="990" height="150" rx="10" fill="#f6f0e6" stroke="#222" stroke-width="4"/>
    <rect x="140" y="155" width="70" height="70" fill="#4b201e"/>
    <text x="175" y="208" text-anchor="middle" font-family="Arial" font-size="58" font-weight="700" fill="#fff">A</text>

    <line x1="250" y1="190" x2="1015" y2="190" stroke="#5b2525" stroke-width="3"/>
    <text x="250" y="180" text-anchor="start" font-family="Courier New, monospace" font-size="48" font-weight="700" fill="#111">{escaped_title}</text>

    <circle cx="355" cy="390" r="72" fill="#050505"/>
    <circle cx="355" cy="390" r="58" fill="#d8d8d8"/>
    <circle cx="845" cy="390" r="72" fill="#050505"/>
    <circle cx="845" cy="390" r="58" fill="#d8d8d8"/>

    <rect x="480" y="308" width="240" height="164" fill="#0b0b0b"/>

    <rect x="105" y="515" width="990" height="76" rx="8" fill="#dfca96"/>
    <text x="240" y="570" font-family="Arial" font-size="47" font-weight="700" fill="#111">TDK</text>
    <text x="955" y="570" font-family="Arial" font-size="52" fill="#6ca449" font-weight="700">90</text>
  </g>
</svg>"""


def playlist_cover_png(title: str, *, size: int = 800) -> bytes:
    from PIL import Image, ImageDraw

    image_size = max(128, min(1200, int(size or 800)))
    scale = image_size / 1000
    image = Image.new("RGB", (image_size, image_size), "#f6f0e6")
    draw = ImageDraw.Draw(image)

    def px(value: float) -> int:
        return round(value * scale)

    def rectangle(
        bounds: tuple[float, float, float, float],
        *,
        fill: str,
        outline: str | None = None,
        width: float = 1,
        radius: float = 0,
    ) -> None:
        xy = tuple(px(value) for value in bounds)
        if radius:
            draw.rounded_rectangle(
                xy,
                radius=px(radius),
                fill=fill,
                outline=outline,
                width=max(1, px(width)),
            )
        else:
            draw.rectangle(xy, fill=fill, outline=outline, width=max(1, px(width)))

    title_text = playlist_cover_title(title)
    title_font = _playlist_cover_font(px(50), bold=True)
    label_font = _playlist_cover_font(px(60), bold=True)
    small_font = _playlist_cover_font(px(46), bold=True)

    rectangle((40, 155, 960, 705), fill="#101010", outline="#050505", width=8, radius=24)
    rectangle((90, 210, 910, 345), fill="#f6f0e6", outline="#222222", width=4, radius=10)
    rectangle((118, 242, 178, 302), fill="#4b201e")
    draw.text((px(136), px(245)), "A", font=label_font, fill="#ffffff")
    draw.line((px(215), px(280), px(852), px(280)), fill="#5b2525", width=px(3))
    draw.text((px(215), px(232)), title_text, font=title_font, fill="#111111")

    for center_x in (335, 745):
        draw.ellipse(
            (px(center_x - 70), px(430), px(center_x + 70), px(570)),
            fill="#050505",
        )
        draw.ellipse(
            (px(center_x - 54), px(446), px(center_x + 54), px(554)),
            fill="#d8d8d8",
        )
    rectangle((438, 395, 640, 565), fill="#0b0b0b")
    rectangle((90, 610, 910, 675), fill="#dfca96", radius=8)
    draw.text((px(205), px(617)), "TDK", font=small_font, fill="#111111")
    draw.text((px(795), px(617)), "90", font=small_font, fill="#6ca449")

    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _playlist_cover_font(size: int, *, bold: bool = False) -> object:
    from PIL import ImageFont

    names = (
        ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf")
        if bold
        else ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf")
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def playlist_cover_title(title: str) -> str:
    normalized_title = " ".join((title or "Playlist").split()) or "Playlist"
    return _truncate_playlist_cover_title(normalized_title)


def _truncate_playlist_cover_title(title: str) -> str:
    if _text_columns(title) <= PLAYLIST_COVER_TITLE_MAX_COLUMNS:
        return title

    max_title_columns = PLAYLIST_COVER_TITLE_MAX_COLUMNS - len(
        PLAYLIST_COVER_TITLE_ELLIPSIS
    )
    truncated = []
    columns = 0
    for character in title:
        character_columns = _text_columns(character)
        if columns + character_columns > max_title_columns:
            break
        truncated.append(character)
        columns += character_columns
    return "".join(truncated).rstrip() + PLAYLIST_COVER_TITLE_ELLIPSIS


def _text_columns(value: str) -> int:
    return sum(
        2 if east_asian_width(character) in {"F", "W"} else 1 for character in value
    )


def playlist_cover_data_url(svg: str) -> str:
    return f"data:image/svg+xml;charset=utf-8,{quote(svg, safe='')}"
