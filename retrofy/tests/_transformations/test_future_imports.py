"""
Test that typing imports are placed correctly relative to __future__ imports.
"""

import textwrap

import libcst as cst

from retrofy import _converters


def test_typing_import_after_future_annotations():
    """Test that typing import is placed after __future__ imports."""
    source = textwrap.dedent("""
    from __future__ import annotations

    def func(x: int | str) -> bool:
        return True
    """)

    expected = textwrap.dedent("""
    from __future__ import annotations
    import typing

    def func(x: typing.Union[int, str]) -> bool:
        return True
    """)

    module = cst.parse_module(source)
    result = _converters.convert_union(module)
    assert result.code == expected


def test_typing_import_after_multiple_future_imports():
    """Test typing import placement with multiple __future__ imports."""
    source = textwrap.dedent("""
    from __future__ import annotations
    from __future__ import unicode_literals

    def func(x: int | str) -> bool:
        return True
    """)

    expected = textwrap.dedent("""
    from __future__ import annotations
    from __future__ import unicode_literals
    import typing

    def func(x: typing.Union[int, str]) -> bool:
        return True
    """)

    module = cst.parse_module(source)
    result = _converters.convert_union(module)
    assert result.code == expected


def test_typing_import_with_existing_imports():
    """Test typing import placement with existing imports after __future__."""
    source = textwrap.dedent("""
    from __future__ import annotations

    import sys
    from dataclasses import dataclass

    def func(x: int | str) -> bool:
        return True
    """)

    expected = textwrap.dedent("""
    from __future__ import annotations
    import typing

    import sys
    from dataclasses import dataclass

    def func(x: typing.Union[int, str]) -> bool:
        return True
    """)

    module = cst.parse_module(source)
    result = _converters.convert_union(module)
    assert result.code == expected


def test_typing_import_with_docstring():
    """Test typing import placement with module docstring."""
    source = textwrap.dedent('''
    """Module docstring."""

    from __future__ import annotations

    def func(x: int | str) -> bool:
        return True
    ''')

    expected = textwrap.dedent('''
    """Module docstring."""

    from __future__ import annotations
    import typing

    def func(x: typing.Union[int, str]) -> bool:
        return True
    ''')

    module = cst.parse_module(source)
    result = _converters.convert_union(module)
    assert result.code == expected


def test_no_future_imports():
    """Test that typing import is placed at the top when no __future__ imports."""
    source = textwrap.dedent("""
    import sys

    def func(x: int | str) -> bool:
        return True
    """)

    expected = textwrap.dedent("""
    import typing
    import sys

    def func(x: typing.Union[int, str]) -> bool:
        return True
    """)

    module = cst.parse_module(source)
    result = _converters.convert_union(module)
    assert result.code == expected
