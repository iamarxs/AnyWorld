"""Shared validation for untrusted WebSocket text fields."""


def clean_optional_text(value: object, field: str, maximum: int) -> str:
    """Return a stripped string within the length limit, or an empty string for None."""
    return clean_text(value, field, maximum, optional=True)


def clean_text(value: object, field: str, maximum: int, *, optional: bool = False) -> str:
    """Validate text, allowing None and blank strings only for optional fields."""
    if optional and value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"'{field}' must be a string")
    cleaned = value.strip()
    if field == "name" and any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
        raise ValueError("'name' cannot contain control characters")
    if (not optional and not cleaned) or len(cleaned) > maximum:
        length = f"at most {maximum}" if optional else f"1-{maximum}"
        raise ValueError(f"'{field}' must contain {length} characters")
    return cleaned
