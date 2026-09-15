"""Narrative presentation normalization."""

import re


def name_resolution(name: str, result: str) -> str:
    """Normalize exact names without adding labels already supplied by the UI."""
    escaped_name = re.escape(name)
    label = re.compile(
        rf"^\s*(?:{escaped_name}\s*:|\[{escaped_name}\]\s*:?)\s*",
        re.IGNORECASE,
    )
    # The model may nest labels copied from previous outcomes. Never guess at
    # misspellings: a similar word could be another player, NPC or ordinary noun.
    while match := label.match(result):
        remainder = result[match.end() :].strip()
        if not remainder:
            break
        result = remainder
    # Limit casing repair to the opening subject. Replacing throughout the prose
    # would also change ordinary words for players named e.g. Will or Player.
    result = re.sub(rf"^{escaped_name}(?=\s)", lambda _: name, result, flags=re.IGNORECASE)
    named_result = re.sub(
        r"^(?:the\s+|your\s+)?character\b",
        lambda _: name,
        result,
        count=1,
        flags=re.IGNORECASE,
    )
    named_result = re.sub(
        r"^(?:they|he|she)\b", lambda _: name, named_result, count=1, flags=re.IGNORECASE
    )
    return named_result
