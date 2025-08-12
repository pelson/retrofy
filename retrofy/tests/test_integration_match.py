"""Integration tests for match statement conversion through the main convert function."""

import textwrap

from retrofy._converters import convert


def test_match_statement_integration():
    """Test that match statements are converted through the main convert function."""
    source = textwrap.dedent("""
    def handle_value(value):
        match value:
            case 42:
                return "answer"
            case [x, y]:
                return f"pair: {x}, {y}"
            case _:
                return "other"
    """)

    expected = textwrap.dedent("""
    import collections.abc
    def handle_value(value):
        if value == 42:
            return "answer"
        elif isinstance(value, collections.abc.Sequence) and not isinstance(value, (str, collections.abc.Mapping)) and len(value) == 2:
            x, y = value
            return f"pair: {x}, {y}"
        else:
            return "other"
    """)

    result = convert(source)
    assert result == expected


def test_match_statement_with_collections_import():
    """Test that match statements requiring collections.abc import work correctly."""
    source = textwrap.dedent("""
    def process_items(items):
        match items:
            case []:
                return "empty"
            case [x, 0]:
                return f"has zero: {x}"
            case _:
                return "other"
    """)

    expected = textwrap.dedent("""
    import collections.abc
    def process_items(items):
        if isinstance(items, collections.abc.Sequence) and not isinstance(items, (str, collections.abc.Mapping)) and len(items) == 0:
            return "empty"
        elif isinstance(items, collections.abc.Sequence) and not isinstance(items, (str, collections.abc.Mapping)) and len(items) == 2 and items[1] == 0:
            x = items[0]
            return f"has zero: {x}"
        else:
            return "other"
    """)

    result = convert(source)
    assert result == expected


def test_match_statement_with_existing_future_imports():
    """Test that collections.abc import is added after future imports."""
    source = textwrap.dedent("""
    from __future__ import annotations

    def process_data(data):
        match data:
            case []:
                return "empty"
            case _:
                return "not empty"
    """)

    expected = textwrap.dedent("""
    from __future__ import annotations
    import collections.abc

    def process_data(data):
        if isinstance(data, collections.abc.Sequence) and not isinstance(data, (str, collections.abc.Mapping)) and len(data) == 0:
            return "empty"
        else:
            return "not empty"
    """)

    result = convert(source)
    assert result == expected
