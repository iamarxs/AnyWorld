"""Narrative presentation normalization."""

import re


def name_resolution(name: str, result: str) -> str:
    """Ensure every per-player outcome explicitly identifies its player."""
    named_result = re.sub(
        r"^(?:the\s+|your\s+)?character\b", name, result, count=1, flags=re.IGNORECASE
    )
    named_result = re.sub(r"^(?:they|he|she)\b", name, named_result, count=1, flags=re.IGNORECASE)
    if name.casefold() not in named_result.casefold():
        return f"{name}: {named_result}"
    return named_result
