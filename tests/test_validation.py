"""Required and optional text retain the same boundary checks and errors."""

import pytest

from logic.validation import clean_optional_text, clean_text


@pytest.mark.parametrize("optional", [False, True])
def test_text_validation_boundaries(optional):
    clean = clean_optional_text if optional else clean_text
    assert clean("  Ada  ", "name", 3) == "Ada"
    for value in (None, "", "   "):
        if optional:
            assert clean(value, "message", 3) == ""
        else:
            message = (
                "'message' must be a string"
                if value is None
                else "'message' must contain 1-3 characters"
            )
            with pytest.raises(ValueError) as error:
                clean(value, "message", 3)
            assert str(error.value) == message
    for value, field, message in (
        (42, "message", "'message' must be a string"),
        (
            "abcd",
            "message",
            f"'message' must contain {'at most 3' if optional else '1-3'} characters",
        ),
        ("\tAda", "name", "'name' cannot contain control characters"),
        ("Ada\x7f", "name", "'name' cannot contain control characters"),
        ("\x85Ada", "name", "'name' cannot contain control characters"),
    ):
        with pytest.raises(ValueError) as error:
            clean(value, field, 3)
        assert str(error.value) == message
