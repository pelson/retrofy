"""PEP 617 (3.10, parsed by 3.9): parenthesised context managers, lowered
to the unparenthesised form with line numbers and comments preserved."""

import ast
import io
import textwrap
import tokenize

import libcst as cst
import pytest

from retrofy import _converters


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            "with (a as x, b as y):\n    pass\n",
            "with a as x, b as y:\n    pass\n",
            id="one-line",
        ),
        pytest.param(
            "with (a, b):\n    pass\n",
            "with a, b:\n    pass\n",
            id="no-targets",
        ),
        pytest.param(
            "async def f():\n    async with (\n        a as x,\n        b as y\n    ):\n        pass\n",
            "async def f():\n    async with (\n        a) as x, (\n        b) as (y\n    ):\n        pass\n",
            id="async-multi-line",
        ),
        pytest.param(
            "with (\n    open(a) as fa,  # first\n\n    open(\n        b,\n    ) as fb,\n):\n    pass\n",
            "with (\n    open(a)) as fa, (  # first\n\n    open(\n        b,\n    )) as (fb\n):\n    pass\n",
            id="comments-blank-line-and-trailing-comma",
        ),
        pytest.param(
            "with (\n    open(a)\n    as fa,  # c1\n    open(b) as\n        fb  # c2\n):\n    pass\n",
            "with (\n    open(a)\n    ) as fa, (  # c1\n    open(b)) as (\n        fb  # c2\n):\n    pass\n",
            id="as-on-next-line",
        ),
        pytest.param(
            "with (  # opening\n    a as x\n    # closing comment\n):\n    pass\n",
            "with (  # opening\n    a) as (x\n    # closing comment\n):\n    pass\n",
            id="comments-at-the-parentheses",
        ),
        pytest.param(
            "with (\n    pair() as (p, q),\n    other(),\n):\n    pass\n",
            "with (\n    pair()) as (p, q), (\n    other()\n):\n    pass\n",
            id="tuple-target",
        ),
        pytest.param(
            "with (\n    a\n    + b as x,\n):\n    pass\n",
            "with (\n    a\n    + b) as (x\n):\n    pass\n",
            id="item-spanning-lines",
        ),
        pytest.param(
            "with (open(a)) as f, b:\n    pass\n",
            "with (open(a)) as f, b:\n    pass\n",
            id="parenthesised-item-unchanged",
        ),
    ],
)
def test_pep617_with(source, expected):
    result = _converters.convert_pep617_with(cst.parse_module(source))
    assert result.code == expected
    # Line numbers (for tracebacks) and comments are all kept.
    assert result.code.count("\n") == source.count("\n")
    assert _comments(result.code) == _comments(source)


def _comments(code: str) -> list[str]:
    tokens = tokenize.generate_tokens(io.StringIO(code).readline)
    return [token.string for token in tokens if token.type == tokenize.COMMENT]


def test_line_numbers_preserved():
    source = textwrap.dedent("""
    with (
        a as x,  # comment
        b as y,
    ):
        marker()
    """)
    converted = _converters.convert(source)

    def marker_line(code):
        return next(
            node.lineno
            for node in ast.walk(ast.parse(code))
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "marker"
        )

    assert marker_line(converted) == marker_line(source)


def test_context_managers_enter_and_exit_in_order():
    source = textwrap.dedent("""
    import contextlib

    events = []

    @contextlib.contextmanager
    def cm(name):
        events.append(("enter", name))
        yield name
        events.append(("exit", name))

    with (
        cm("a") as a,
        cm("b") as b,
    ):
        events.append(("body", a, b))
    """)
    native: dict = {}
    exec(source, native)
    converted: dict = {}
    exec(_converters.convert(source), converted)
    assert converted["events"] == native["events"]
