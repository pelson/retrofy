"""
Test walrus operator in comprehensions.
"""

import pytest


def test_walrus_list_comprehension():
    """Test walrus operator in list comprehensions."""
    data = ["  hello  ", "", "  world  "]
    result = [
        cleaned for item in data if (cleaned := item.strip()) and len(cleaned) > 0
    ]
    assert result == ["hello", "world"]


@pytest.mark.xfail(reason="retrofy bug", strict=True)
def test_walrus_dict_comprehension():
    """Test walrus operator in dict comprehensions."""
    data = ["Hello", "World", "Hi"]
    result = {
        key: length
        for item in data
        if (key := item.lower()) and (length := len(key)) > 2
    }
    assert result == {"hello": 5, "world": 5}
