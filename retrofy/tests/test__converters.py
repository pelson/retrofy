import textwrap

import libcst as cst

from retrofy import _converters


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


def test_union_with_docstring():
    test_case_source = textwrap.dedent("""
    '''Some module'''

    c: unknown | int
    """)
    expected = textwrap.dedent("""
    '''Some module'''
    import typing

    c: typing.Union[unknown, int]
    """)
    module = cst.parse_module(test_case_source)
    result = _converters.convert_union(module)
    assert result.code == expected


def test_union__future__():
    test_case_source = textwrap.dedent("""
    from __future__ import annotations

    c: unknown | int
    """)

    expected = textwrap.dedent("""
    from __future__ import annotations
    import typing

    c: typing.Union[unknown, int]
    """)
    module = cst.parse_module(test_case_source)
    result = _converters.convert_union(module)
    assert result.code == expected


def test_union__future___with_docstring():
    test_case_source = textwrap.dedent("""
    '''
    Some module
    '''

    from __future__ import annotations

    c: unknown | int
    """)

    expected = textwrap.dedent("""
    '''
    Some module
    '''

    from __future__ import annotations
    import typing

    c: typing.Union[unknown, int]
    """)
    module = cst.parse_module(test_case_source)
    result = _converters.convert_union(module)
    assert result.code == expected


def test_union__future__with_import_already():
    test_case_source = textwrap.dedent("""
    from __future__ import annotations
    import typing

    if typing.TYPE_CHECKING:
        c: str | int
    """)

    expected = textwrap.dedent("""
    from __future__ import annotations
    import typing
    import typing

    if typing.TYPE_CHECKING:
        c: typing.Union[str, int]
    """)
    module = cst.parse_module(test_case_source)
    result = _converters.convert_union(module)
    assert result.code == expected


def test_convert():
    test_case_source = textwrap.dedent("""
    def bar(a: list[str]) -> list[str]:
        return a
    """)

    expected = textwrap.dedent("""
    def bar(a: typing.List[str]) -> typing.List[str]:
        return a
    """)
    result = _converters.convert(test_case_source)
    assert result == expected
