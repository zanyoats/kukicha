from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

COMPACT_COUNT_SUFFIXES = (
    (1_000, "k"),
    (1_000_000, "M"),
    (1_000_000_000, "B"),
    (1_000_000_000_000, "T"),
)
MAX_COMPACT_COUNT = 999_000_000_000_000


def safe_ints(values: object) -> list[int]:
    if not isinstance(values, list):
        return []
    ints: list[int] = []
    for value in values:
        try:
            ints.append(int(value))
        except (TypeError, ValueError):
            continue
    return ints

def optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def placeholders_for(values: list[object] | tuple[object, ...]) -> str:
    return ", ".join("?" for _ in values)

def clamp_int(value: object, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = minimum
    return min(max(parsed, minimum), maximum)

def plural(count_value: int, singular: str, plural_value: str) -> str:
    return singular if count_value == 1 else plural_value


def format_compact_count(value: object) -> str:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return str(value)
    if count < 0:
        return f"-{format_compact_count(abs(count))}"
    if count < 1_000:
        return str(count)
    if count > MAX_COMPACT_COUNT:
        return "infinity"

    unit_index = 0
    for index, (unit, _suffix) in enumerate(COMPACT_COUNT_SUFFIXES):
        if count >= unit:
            unit_index = index
    while unit_index < len(COMPACT_COUNT_SUFFIXES):
        unit, suffix = COMPACT_COUNT_SUFFIXES[unit_index]
        rendered = compact_count_for_unit(count, unit)
        if Decimal(rendered) < Decimal("1000"):
            return f"{trim_decimal_text(rendered)}{suffix}"
        unit_index += 1
    return "infinity"


def compact_count_for_unit(count: int, unit: int) -> Decimal:
    scaled = Decimal(count) / Decimal(unit)
    if scaled >= Decimal("100"):
        places = Decimal("1")
    elif scaled >= Decimal("10"):
        places = Decimal("0.1")
    else:
        places = Decimal("0.01")
    return scaled.quantize(places, rounding=ROUND_HALF_UP)


def trim_decimal_text(value: Decimal) -> str:
    return format(value, "f").rstrip("0").rstrip(".")


def format_count_label(count_value: int, singular: str, plural_value: str) -> str:
    return f"{format_compact_count(count_value)} {plural(count_value, singular, plural_value)}"
