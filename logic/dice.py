"""Dungeon Master dice mechanics."""

import secrets


def roll_d100() -> int:
    """Return a cryptographically unbiased integer from 0 through 100."""
    return secrets.randbelow(101)


def describe_roll(value: int) -> str:
    """Provide the fixed interpretation supplied to the DM."""
    if value < 11:
        return "catastrophic failure"
    if value < 25:
        return "failure with a serious consequence"
    if value < 35:
        return "failure or costly partial success"
    if value < 50:
        return "success"
    if value < 65:
        return "qualified success"
    if value < 75:
        return "qualified success"
    if value < 90:
        return "resounding success"
    if value < 101:
        return "perfect success"
    return "success"
