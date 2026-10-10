"""Tests for the PEP 585 built-in generics backport (``list[int]`` -> ``typing.List[int]``)."""

import textwrap
import typing

import libcst as cst
import pytest

from retrofy._converters import convert as retrofy_convert
from retrofy._transformations.pep585_builtins import convert as convert_builtins


def _t(src: str) -> str:
    return convert_builtins(cst.parse_module(textwrap.dedent(src))).code


@pytest.mark.parametrize(
    ("builtin", "fallback"),
    [
        ("list", "List"),
        ("dict", "Dict"),
        ("tuple", "Tuple"),
        ("set", "Set"),
        ("frozenset", "FrozenSet"),
        ("type", "Type"),
    ],
)
def test_runtime_alias(builtin: str, fallback: str) -> None:
    result = _t(f"""
    Alias = {builtin}[int]
    """)
    assert result == textwrap.dedent(f"""
    import typing
    Alias = typing.{fallback}[int]
    """)


def test_nested() -> None:
    result = _t("""
    Alias = list[dict[str, tuple[int, ...]]]
    """)
    assert result == textwrap.dedent("""
    import typing
    Alias = typing.List[typing.Dict[str, typing.Tuple[int, ...]]]
    """)


def test_inside_other_generics() -> None:
    result = _t("""
    import typing

    JSON = typing.Union[None, list["JSON"], dict[str, "JSON"]]
    """)
    assert result == textwrap.dedent("""
    import typing

    JSON = typing.Union[None, typing.List["JSON"], typing.Dict[str, "JSON"]]
    """)


def test_annotations() -> None:
    result = _t("""
    def f(xs: list[int]) -> set[str]:
        return set()
    """)
    assert result == textwrap.dedent("""
    import typing
    def f(xs: typing.List[int]) -> typing.Set[str]:
        return set()
    """)


def test_unsubscripted_builtins_are_untouched() -> None:
    src = textwrap.dedent("""
    x = list()
    y = dict
    z = type(x)
    isinstance(x, list)
    w = mapping[list]
    """)
    assert _t(src) == src


def test_shadowed_builtin_is_untouched() -> None:
    src = textwrap.dedent("""
    from mylib import MyList as list

    Alias = list[int]
    """)
    assert _t(src) == src


def test_shadowed_in_function_scope_is_untouched() -> None:
    src = textwrap.dedent("""
    def f(dict):
        return dict[int]
    """)
    assert _t(src) == src


def test_import_after_future_import() -> None:
    result = _t("""
    from __future__ import annotations

    Alias = dict[str, int]
    """)
    assert result == textwrap.dedent("""
    from __future__ import annotations
    import typing

    Alias = typing.Dict[str, int]
    """)


def test_converted_aliases_are_equivalent() -> None:
    src = textwrap.dedent("""
    Alias = dict[str, tuple[int, ...]]
    """)
    ns: typing.Dict[str, typing.Any] = {}
    exec(compile(_t(src), "<test>", "exec"), ns)
    assert typing.get_origin(ns["Alias"]) is dict
    key, value = typing.get_args(ns["Alias"])
    assert key is str
    assert typing.get_origin(value) is tuple
    assert typing.get_args(value) == (int, ...)


def test_full_convert() -> None:
    result = retrofy_convert(
        textwrap.dedent("""
        Timeout = float | tuple[float, float] | None
        Document = dict[str, list[str]]
        """),
    )
    assert "typing.Tuple[float, float]" in result
    assert "Document = typing.Dict[str, typing.List[str]]" in result
