"""Shared validation for untrusted WebSocket text fields."""


def clean_optional_text(value: object, field: str, maximum: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"'{field}' must be a string")
    cleaned = value.strip()
    if len(cleaned) > maximum:
        raise ValueError(f"'{field}' must contain at most {maximum} characters")
    return cleaned


def clean_text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"'{field}' must be a string")
    cleaned = value.strip()
    if not cleaned or len(cleaned) > maximum:
        raise ValueError(f"'{field}' must contain 1-{maximum} characters")
    return cleaned
