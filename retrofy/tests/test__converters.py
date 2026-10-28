import sys
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


def test_union_lowering_is_stable_across_unions():
    # Intermediate unions are discarded as enclosing ``|`` flatten them;
    # an unrelated node reusing such a node's ``id()`` must not be mistaken
    # for it (GH #81). The reuse is down to the allocator, hence the repeats.
    test_case_source = textwrap.dedent("""
    JSON = None | bool | int | float | str | list["JSON"] | dict[str, "JSON"]
    Timeout = float | tuple[float, float] | None
    """)
    expected = textwrap.dedent("""
    import typing
    JSON = typing.Union[None, bool, int, float, str, typing.List["JSON"], typing.Dict[str, "JSON"]]
    Timeout = typing.Union[float, typing.Tuple[float, float], None]
    """)
    results = {_converters.convert(test_case_source) for _ in range(50)}
    assert results == {expected}


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


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # isinstance / issubclass: a tuple, as typing.Union is rejected < 3.10.
        ("isinstance(x, int | str)", "isinstance(x, (int, str))"),
        ("isinstance(x, int | None)", "isinstance(x, (int, type(None)))"),
        ("issubclass(C, A | B | None)", "issubclass(C, (A, B, type(None)))"),
        ("isinstance(x, (int | str))", "isinstance(x, (int, str))"),
        ("isinstance(x, (int | str, bytes))", "isinstance(x, ((int, str), bytes))"),
        ("isinstance(x, Alias)", "isinstance(x, Alias)"),
        # Elsewhere, only when every operand is certainly a type.
        ("MaybeInt = int | None", "MaybeInt = typing.Optional[int]"),
        ("Num = int | float | None", "Num = typing.Union[int, float, None]"),
        ("Opt = typing.Any | None", "Opt = typing.Optional[typing.Any]"),
        ("Lst = list[int] | None", "Lst = typing.Optional[typing.List[int]]"),
        ("flags = READ | WRITE", "flags = READ | WRITE"),
        ("mixed = int | thing", "mixed = int | thing"),
        ("both = {1} | {2}", "both = {1} | {2}"),
        ("n = 1 | 2", "n = 1 | 2"),
    ],
)
def test_runtime_union_lowering(source, expected):
    result = _converters.convert(f"import typing\n{source}\n")
    assert result.splitlines()[-1] == expected


def test_runtime_union_lowering_respects_shadowing():
    test_case_source = textwrap.dedent("""
    def no_builtin_type(type, x):
        return isinstance(x, int | None)

    def no_builtin_int(int):
        return int | None
    """)
    expected = textwrap.dedent("""
    def no_builtin_type(type, x):
        return isinstance(x, (int, None.__class__))

    def no_builtin_int(int):
        return int | None
    """)
    assert _converters.convert(test_case_source) == expected


_ISINSTANCE_UNION_SOURCE = textwrap.dedent("""
def check(x):
    return (
        isinstance(x, int | None),
        isinstance(x, str | bytes | None),
        isinstance(x, (float | None, bytes)),
        issubclass(type(x), int | str),
    )
""")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, (True, False, False, True)),
        ("a", (False, True, False, True)),
        (None, (True, True, True, False)),
        (1.5, (False, False, True, False)),
        (b"b", (False, True, True, False)),
        (True, (True, False, False, True)),
    ],
)
def test_runtime_union_isinstance_equivalence(value, expected):
    converted: dict = {}
    exec(_converters.convert(_ISINSTANCE_UNION_SOURCE), converted)
    assert converted["check"](value) == expected
    if sys.version_info >= (3, 10):
        # The unconverted source only runs on 3.10+.
        native: dict = {}
        exec(_ISINSTANCE_UNION_SOURCE, native)
        assert native["check"](value) == expected


@pytest.mark.parametrize(
    ("source", "module"),
    [
        pytest.param(
            '"""Doc."""\nimport sys\nfrom collections.abc import Mapping\n',
            "sys",
            id="sys-for-version-check",
        ),
        pytest.param(
            '"""Doc."""\nimport typing\n\nx: list[int] = []\n',
            "typing",
            id="typing-for-pep585",
        ),
        pytest.param(
            '"""Doc."""\nimport collections.abc\n\nx: collections.abc.Mapping[str, int]\n',
            "collections.abc",
            id="source-module-for-attribute-fallback",
        ),
    ],
)
def test_existing_import_kept_once_after_docstring(source, module):
    # A module docstring hid the existing import from the check, so it was
    # added again; re-converting then kept adding more (#79).
    once = _converters.convert(source)
    assert once.splitlines().count(f"import {module}") == 1, once
    assert _converters.convert(once) == once
