from __future__ import annotations

import pytest

from app.ids import DEFAULT_ID_LENGTH, ID_PATTERN, generate_id, is_valid_id


def test_generated_ids_match_telegram_startapp_rules() -> None:
    for _ in range(2000):
        value = generate_id()
        assert len(value) == DEFAULT_ID_LENGTH
        assert ID_PATTERN.fullmatch(value)


def test_generated_ids_are_unique() -> None:
    ids = {generate_id() for _ in range(20000)}
    assert len(ids) == 20000


@pytest.mark.parametrize("length", [1, 8, 64])
def test_custom_length(length: int) -> None:
    assert len(generate_id(length)) == length


@pytest.mark.parametrize("length", [0, 65, -1])
def test_invalid_length(length: int) -> None:
    with pytest.raises(ValueError):
        generate_id(length)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("abc_DEF-123", True),
        ("a" * 64, True),
        ("a" * 65, False),
        ("", False),
        ("com espaço", False),
        ("../etc", False),
        ("ação", False),
        (None, False),
        (123, False),
    ],
)
def test_is_valid_id(value: object, expected: bool) -> None:
    assert is_valid_id(value) is expected
