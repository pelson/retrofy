"""Tests for the PEP 585 stdlib-module import backports (collections / contextlib / re).

The collections.abc backport has its own test module.
"""

import collections
import contextlib
import re
import textwrap
import types
import typing
from typing import Any, Dict, Optional

import libcst as cst

from retrofy._converters import convert as retrofy_convert
from retrofy._transformations._backport_engine import transform
from retrofy._transformations.pep585_imports import (
    COLLECTIONS_CONFIG,
    CONTEXTLIB_CONFIG,
    RE_CONFIG,
)
from retrofy._transformations.pep585_imports import convert as convert_pep585


def _fake_sys(version: tuple) -> types.ModuleType:
    mod = types.ModuleType("sys")
    mod.version_info = version  # type: ignore[attr-defined]
    return mod


def _exec_ns(code: str, fake_sys: Optional[types.ModuleType] = None) -> Dict[str, Any]:
    ns: Dict[str, Any] = {"__builtins__": __builtins__}
    if fake_sys is not None:
        ns["sys"] = fake_sys
        code = "\n".join(ln for ln in code.splitlines() if ln.strip() != "import sys")
    exec(compile(code, "<test>", "exec"), ns)
    return ns


def _t_collections(src: str) -> str:
    return transform(textwrap.dedent(src), COLLECTIONS_CONFIG)


def _t_contextlib(src: str) -> str:
    return transform(textwrap.dedent(src), CONTEXTLIB_CONFIG)


def _t_re(src: str) -> str:
    return transform(textwrap.dedent(src), RE_CONFIG)


def _check_both_branches(
    original: str,
    converted: str,
    expected_bindings: Dict[str, Any],
) -> None:
    """Run the original on the real interpreter, then the converted on both branches."""
    _exec_ns(original)  # original must be valid 3.9+ code

    ns_new = _exec_ns(converted)
    for name, expected in expected_bindings.items():
        assert ns_new[name] is expected, f">=3.9 branch bound {name} wrong"

    ns_old = _exec_ns(converted, fake_sys=_fake_sys((3, 8, 0)))
    for name in expected_bindings:
        assert name in ns_old, f"<3.9 branch did not bind {name}"


# ---------------------------------------------------------------------------
# collections
# ---------------------------------------------------------------------------


def test_collections_deque_rename():
    """collections.deque is lowercase; fallback is typing.Deque."""
    src = textwrap.dedent("""
    from collections import deque

    d = deque
    """)
    converted = _t_collections(src)
    assert "if sys.version_info >= (3, 9):" in converted
    assert "from collections import deque" in converted
    # fallback emits ``from typing import Deque as deque`` so the binding name stays the same
    assert "from typing import Deque as deque" in converted

    _check_both_branches(src, converted, {"d": collections.deque})
    ns_old = _exec_ns(converted, fake_sys=_fake_sys((3, 8, 0)))
    assert ns_old["d"] is typing.Deque


def test_collections_multiple_names():
    src = textwrap.dedent("""
    from collections import deque, defaultdict, OrderedDict, Counter, ChainMap

    bindings = (deque, defaultdict, OrderedDict, Counter, ChainMap)
    """)
    converted = _t_collections(src)
    # source branch keeps original lowercase names
    assert (
        "from collections import deque, defaultdict, OrderedDict, Counter, ChainMap"
        in converted
    )
    # fallback rewrites only the case-changed ones
    assert (
        "from typing import Deque as deque, DefaultDict as defaultdict, OrderedDict, Counter, ChainMap"
        in converted
    )

    # >= 3.9 branch
    ns_new = _exec_ns(converted)
    assert ns_new["bindings"] == (
        collections.deque,
        collections.defaultdict,
        collections.OrderedDict,
        collections.Counter,
        collections.ChainMap,
    )
    # < 3.9 branch
    ns_old = _exec_ns(converted, fake_sys=_fake_sys((3, 8, 0)))
    assert ns_old["bindings"] == (
        typing.Deque,
        typing.DefaultDict,
        typing.OrderedDict,
        typing.Counter,
        typing.ChainMap,
    )


def test_collections_idempotent():
    src = "from collections import deque\n"
    once = _t_collections(src)
    twice = _t_collections(once)
    assert once == twice


def test_collections_unrelated_import_untouched():
    src = textwrap.dedent("""
    from collections import namedtuple
    """)
    out = _t_collections(src)
    assert "if sys.version_info" not in out
    assert "namedtuple" in out


# ---------------------------------------------------------------------------
# contextlib
# ---------------------------------------------------------------------------


def test_contextlib_abstractcontextmanager():
    src = textwrap.dedent("""
    from contextlib import AbstractContextManager

    cm = AbstractContextManager
    """)
    converted = _t_contextlib(src)
    assert "if sys.version_info >= (3, 9):" in converted
    assert "from contextlib import AbstractContextManager" in converted
    assert "from typing import ContextManager as AbstractContextManager" in converted

    ns_new = _exec_ns(converted)
    assert ns_new["cm"] is contextlib.AbstractContextManager
    ns_old = _exec_ns(converted, fake_sys=_fake_sys((3, 8, 0)))
    assert ns_old["cm"] is typing.ContextManager


def test_contextlib_async_variant():
    src = textwrap.dedent("""
    from contextlib import AbstractAsyncContextManager
    """)
    converted = _t_contextlib(src)
    assert (
        "from typing import AsyncContextManager as AbstractAsyncContextManager"
        in converted
    )


# ---------------------------------------------------------------------------
# re
# ---------------------------------------------------------------------------


def test_re_pattern_and_match():
    src = textwrap.dedent("""
    from re import Pattern, Match

    p = Pattern
    m = Match
    """)
    converted = _t_re(src)
    assert "from re import Pattern, Match" in converted
    # no rename for Pattern/Match — same names in typing
    assert "from typing import Pattern, Match" in converted

    ns_new = _exec_ns(converted)
    assert ns_new["p"] is re.Pattern
    assert ns_new["m"] is re.Match
    ns_old = _exec_ns(converted, fake_sys=_fake_sys((3, 8, 0)))
    assert ns_old["p"] is typing.Pattern
    assert ns_old["m"] is typing.Match


# ---------------------------------------------------------------------------
# Composed pipeline
# ---------------------------------------------------------------------------


def test_pep585_imports_chain_through_convert():
    """All three configs run via the pep585_imports.convert(module) entry point."""
    src = textwrap.dedent("""
    from collections import deque
    from contextlib import AbstractContextManager
    from re import Pattern
    """)
    mod = cst.parse_module(src)
    out = convert_pep585(mod).code
    assert "from collections import deque" in out
    assert "from typing import Deque as deque" in out
    assert "from contextlib import AbstractContextManager" in out
    assert "from typing import ContextManager as AbstractContextManager" in out
    assert "from re import Pattern" in out
    assert "from typing import Pattern" in out


def test_full_pipeline_integration():
    src = textwrap.dedent("""
    from collections import deque
    from collections.abc import Mapping
    from re import Pattern
    from typing import Literal

    def f(d: deque, m: Mapping[str, int], p: Pattern[str], mode: Literal["a"]):
        return d, m, p, mode
    """)
    out = retrofy_convert(src)
    # Each rewrite block must be present:
    assert "from collections import deque" in out
    assert "from collections.abc import Mapping" in out
    assert "from re import Pattern" in out
    assert "from typing import Literal" in out
    # ... and each fallback:
    assert "from typing import Deque as deque" in out
    assert "from typing import Mapping" in out  # collections.abc fallback
    assert "from typing import Pattern" in out
    assert "from typing_extensions import Literal" in out
