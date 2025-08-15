"""
Test walrus operator short-circuiting behavior.
"""

import pytest


@pytest.mark.xfail(reason="retrofy bug", strict=True)
def test_short_circuit_and():
    """Test that short-circuiting prevents unnecessary function calls."""
    call_count = 0

    def expensive_func(x: str) -> str:
        nonlocal call_count
        call_count += 1
        return x.upper()

    data = ["", "hello", "world"]

    # This should only call expensive_func for non-empty strings
    result = [
        processed
        for item in data
        if (stripped := item.strip()) and (processed := expensive_func(stripped))
    ]

    assert result == ["HELLO", "WORLD"]
    assert call_count == 2  # Should not call expensive_func for empty string


@pytest.mark.xfail(reason="retrofy bug", strict=True)
def test_dict_comprehension_short_circuit():
    """Test short-circuiting in dict comprehension."""
    data = [{"name": "Alice", "value": 25}, {"name": "", "value": 30}]

    result = {
        name: value
        for item in data
        if (name := item.get("name")) and (value := item.get("value")) and value > 20
    }

    assert result == {"Alice": 25}
