import pytest
import libcst as cst
import textwrap
from typing_to_the_future import _converters


def test_union():
    test_case_source = textwrap.dedent("""
    import foo
    
    def bar(a: int | None) -> str | float:
        c: unknown | int
        return ''
    """)

    expected = textwrap.dedent("""
    import typing
    import foo
    
    def bar(a: typing.Union[int, None]) -> typing.Union[str, float]:
        c: typing.Union[unknown, int]
        return ''
    """)
    module = cst.parse_module(test_case_source)
    result = _converters.convert_union(module)
    assert result.code == expected


@pytest.mark.xpass
def test_union__future__():
    test_case_source = textwrap.dedent("""
    from __future__ import annotations
    
    c: unknown | int
    """)

    expected = textwrap.dedent("""
    import typing
    from __future__ import annotations
    
    c: typing.Union[unknown, int]
    """)
    module = cst.parse_module(test_case_source)
    result = _converters.convert_union(module)
    assert result.code == expected
