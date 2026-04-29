from __future__ import annotations

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
