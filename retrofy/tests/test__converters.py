import textwrap

import libcst as cst
import pytest

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

    def bar(a: typing.Optional[int]) -> typing.Union[str, float]:
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

    if typing.TYPE_CHECKING:
        c: typing.Union[str, int]
    """)
    module = cst.parse_module(test_case_source)
    result = _converters.convert_union(module)
    assert result.code == expected


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        ("int | None", "typing.Optional[int]"),
        ("None | int", "typing.Optional[int]"),
        ("int | str | None", "typing.Union[int, str, None]"),
        ("(int | str) | None", "typing.Union[int, str, None]"),
        ("int | (str | None)", "typing.Union[int, str, None]"),
        ("None | None", "typing.Union[None, None]"),
        (
            "dict[str, int | None] | None",
            "typing.Optional[dict[str, typing.Optional[int]]]",
        ),
        (
            "Callable[[int | None], str | bytes]",
            "Callable[[typing.Optional[int]], typing.Union[str, bytes]]",
        ),
        # ``|`` in Literal values / Annotated metadata is a bitwise-or.
        ("Literal[1 | 2]", "Literal[1 | 2]"),
        (
            "typing.Annotated[int | None, A | B]",
            "typing.Annotated[typing.Optional[int], A | B]",
        ),
        # String annotations are left alone.
        ('"int | None"', '"int | None"'),
        # Enclosing parentheses are preserved.
        ("(int | None)", "(typing.Optional[int])"),
    ],
)
def test_union_lowering(annotation, expected):
    module = cst.parse_module(f"x: {annotation}\n")
    result = _converters.convert_union(module)
    assert result.code.splitlines()[-1] == f"x: {expected}"


def test_union_lowering_preserves_comments():
    test_case_source = textwrap.dedent("""
    def f(
        a: (
            int  # the count
            | None
        ),
        b: int |  # why
           str,
    ) -> None: ...
    """)
    expected = textwrap.dedent("""
    import typing
    def f(
        a: (
            typing.Union[int,  # the count
            None]
        ),
        b: typing.Union[int,  # why
           str],
    ) -> None: ...
    """)
    module = cst.parse_module(test_case_source)
    result = _converters.convert_union(module)
    assert result.code == expected


def test_union_lowering_in_pep695_type_expressions():
    test_case_source = textwrap.dedent("""
    type MaybeInt = int | None
    type Pair[T: int | str] = tuple[T, T] | None

    def first[T: bytes | None](items: list[T]) -> T: ...
    """)
    expected = textwrap.dedent("""
    import typing
    MaybeInt = typing.Optional[int]
    T = typing.TypeVar("T", bound=typing.Union[int, str])
    Pair: typing.TypeAlias = typing.Optional[typing.Tuple[T, T]]
    T = typing.TypeVar("T", bound=typing.Optional[bytes])

    def first(items: typing.List[T]) -> T: ...
    """)
    assert _converters.convert(test_case_source) == expected


def test_convert():
    test_case_source = textwrap.dedent("""
    def bar(a: list[str]) -> list[str]:
        return a
    """)

    expected = textwrap.dedent("""
    import typing
    def bar(a: typing.List[str]) -> typing.List[str]:
        return a
    """)
    result = _converters.convert(test_case_source)
    assert result == expected
