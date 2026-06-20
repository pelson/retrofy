"""Behavioural-equivalence tests for the PEP 810 (lazy imports) backport.

The match-statement transformer ([[test_match_statement.py:1810]]) sets the
pattern: for each case, run the *unconverted* source on a Python that
supports the feature natively, and the *converted* source on any
Python — both must produce the same value.

Here the unconverted source uses ``lazy`` soft-keyword syntax, which
is only parseable by CPython 3.15+. The converted source uses the
retrofy-injected ``__lazy_*__`` helpers and runs on every supported
Python.

Note on ``_exec_native``: the unconverted source is fed straight to
``compile()``, so the running interpreter must understand the modern
syntax. Retrofy is **not** a runtime dependency — we are not
teaching the interpreter about new syntax, just round-tripping the
source through ``ast``/``compile``. That's why these cases are
skipped below 3.15. The retrofy docs should call this out for users
who reach for ``exec(open(...).read())`` and find that retrofy-
backported syntax is still unparseable: retrofy only intervenes at
build time or via the editable-install import hook.
"""

from __future__ import annotations

import importlib
import sys
import textwrap
from typing import Any

import pytest

from retrofy._meta_hook_converter import MyMetaPathFinder
from retrofy._transformations.lazy_imports import transform_lazy_imports

# Cases use stdlib-only modules so the tests stay hermetic. Each case
# is (source_body, expected_result_value). The body must bind the
# answer to a top-level name ``result`` — putting the call *inside* the
# source means the retrofy converter sees and wraps every read of a
# lazy binding, which mirrors how user code actually invokes PEP 810
# (the wraps only protect reads inside the converted module).
CASES = [
    pytest.param(
        textwrap.dedent(
            """
            lazy import json

            result = json.dumps({"x": 1})
            """,
        ),
        '{"x": 1}',
        id="lazy_import_simple",
    ),
    pytest.param(
        textwrap.dedent(
            """
            lazy import json as j

            result = j.dumps({"a": 2})
            """,
        ),
        '{"a": 2}',
        id="lazy_import_as_alias",
    ),
    pytest.param(
        textwrap.dedent(
            """
            lazy import xml.etree.ElementTree

            result = xml.etree.ElementTree.fromstring("<a/>").tag
            """,
        ),
        "a",
        id="lazy_import_dotted_binds_top",
    ),
    pytest.param(
        textwrap.dedent(
            """
            lazy from collections.abc import Mapping

            result = isinstance({}, Mapping)
            """,
        ),
        True,
        id="lazy_from_isinstance_true",
    ),
    pytest.param(
        textwrap.dedent(
            """
            lazy from collections.abc import Mapping

            result = isinstance([], Mapping)
            """,
        ),
        False,
        id="lazy_from_isinstance_false",
    ),
    pytest.param(
        textwrap.dedent(
            """
            lazy from collections.abc import Mapping as M

            result = isinstance({}, M)
            """,
        ),
        True,
        id="lazy_from_alias",
    ),
]


def _exec_native(source: str) -> Any:
    """Run the *unconverted* lazy source in a fresh module namespace
    and return ``result``.

    Only callable on Python 3.15+ — earlier interpreters can't parse
    ``lazy`` syntax.
    """
    namespace: dict[str, Any] = {"__name__": "_lazy_imports_native_case"}
    compiled = compile(source, "<native-case>", "exec")
    exec(compiled, namespace)
    return namespace["result"]


def _exec_converted(source: str, tmp_path, monkeypatch) -> Any:
    """Convert *source* with retrofy, exec it as a real module under a
    synthesised package, and return its ``result`` attribute.

    A package context is required because the converted source emits
    a relative import (``from ._retrofy_rt.lazy_imports import ...``);
    the meta-path finder synthesises that runtime on demand.
    """
    pkg_root = tmp_path / "synthcase"
    pkg_root.mkdir()
    (pkg_root / "__init__.py").write_text("")
    (pkg_root / "mod.py").write_text(source)

    monkeypatch.syspath_prepend(str(tmp_path))
    finder = MyMetaPathFinder(["synthcase"])
    monkeypatch.setattr(sys, "meta_path", [finder, *sys.meta_path])
    for name in list(sys.modules):
        if name == "synthcase" or name.startswith("synthcase."):
            del sys.modules[name]

    mod = importlib.import_module("synthcase.mod")
    return mod.result


@pytest.mark.parametrize(["source", "expected"], CASES)
@pytest.mark.skipif(
    sys.version_info < (3, 15),
    reason="``lazy`` soft keyword requires Python 3.15+",
)
def test_unconverted_source_native_behaviour(
    source: str,
    expected: Any,
) -> None:
    """ASSUMPTION VALIDATION: on Python 3.15+, the *unconverted* lazy
    source must behave as expected. Pins our model of PEP 810 to the
    interpreter's actual behaviour."""
    assert _exec_native(source) == expected


@pytest.mark.parametrize(["source", "expected"], CASES)
def test_converted_source_behaviour(
    source: str,
    expected: Any,
    tmp_path,
    monkeypatch,
) -> None:
    """EXECUTION VALIDATION: the converted source must behave the same
    way on every supported Python."""
    assert _exec_converted(source, tmp_path, monkeypatch) == expected


# sys.modules-trace source: covers both halves of the deferred-import
# property in one source.
#
# 1. Before any touch of the lazy binding, ``sys.modules`` is empty
#    for the target — module load hasn't happened.
# 2. First touch reifies the binding and populates ``sys.modules``.
# 3. Dropping the target from ``sys.modules`` does NOT reset the
#    binding (PEP 810 replaces the global slot with the real module
#    on first read).
# 4. Subsequent touches use the cached local reference; they do not
#    silently re-import or re-populate ``sys.modules``.
#
# A real attribute (``token_bytes``) is exercised rather than a
# dunder like ``__name__`` — native PEP 810 may serve some dunders
# from a precomputed descriptor without importing the target.
# Calling ``token_bytes(...)`` forces a real attribute resolution
# that has to go through the loaded module.
_SYS_MODULES_MUTATION_SRC = textwrap.dedent(
    """
    import sys

    lazy import secrets as secrets_mod

    in_modules_before_first_touch = 'secrets' in sys.modules

    first_len = len(secrets_mod.token_bytes(4))
    in_modules_after_first_touch = 'secrets' in sys.modules

    sys.modules.pop('secrets', None)
    in_modules_after_pop = 'secrets' in sys.modules

    second_len = len(secrets_mod.token_bytes(4))
    in_modules_after_second_touch = 'secrets' in sys.modules
    """,
)

_SYS_MODULES_MUTATION_EXPECTED = {
    "in_modules_before_first_touch": False,
    "first_len": 4,
    "in_modules_after_first_touch": True,
    "in_modules_after_pop": False,
    "second_len": 4,
    # Critical: a stale local reference must NOT cause sys.modules to
    # be silently repopulated. Matches plain ``import x; del
    # sys.modules['x']; x.attr`` — the local name still works but
    # sys.modules stays empty.
    "in_modules_after_second_touch": False,
}


@pytest.mark.skipif(
    sys.version_info < (3, 15),
    reason="``lazy`` soft keyword requires Python 3.15+",
)
def test_unconverted_sys_modules_mutation_semantics() -> None:
    """ASSUMPTION VALIDATION: pins our model of what PEP 810 does when
    user code mutates ``sys.modules`` after a lazy binding has been
    reified."""
    sys.modules.pop("encodings.rot_13", None)
    ns: dict[str, Any] = {"__name__": "_lazy_imports_native_sysmod"}
    exec(compile(_SYS_MODULES_MUTATION_SRC, "<native-sysmod>", "exec"), ns)
    for key, value in _SYS_MODULES_MUTATION_EXPECTED.items():
        assert ns[key] == value, (key, ns[key], value)


def test_converted_sys_modules_mutation_matches_native(
    tmp_path,
    monkeypatch,
) -> None:
    """EXECUTION VALIDATION: retrofy's converted output must match the
    native ``sys.modules`` mutation semantics observation-for-
    observation. A regression to "secretly re-import on stale local
    reference" would diverge from PEP 810 and from plain ``import``
    semantics."""
    pkg_root = tmp_path / "synthcase"
    pkg_root.mkdir()
    (pkg_root / "__init__.py").write_text("")
    (pkg_root / "mod.py").write_text(_SYS_MODULES_MUTATION_SRC.lstrip())

    monkeypatch.syspath_prepend(str(tmp_path))
    finder = MyMetaPathFinder(["synthcase"])
    monkeypatch.setattr(sys, "meta_path", [finder, *sys.meta_path])
    for name in list(sys.modules):
        if name == "synthcase" or name.startswith("synthcase."):
            del sys.modules[name]
    sys.modules.pop("encodings.rot_13", None)

    mod = importlib.import_module("synthcase.mod")
    for key, value in _SYS_MODULES_MUTATION_EXPECTED.items():
        assert getattr(mod, key) == value, (key, getattr(mod, key), value)


@pytest.mark.parametrize(["source", "expected"], CASES)
def test_converted_output_parses(source: str, expected: Any) -> None:  # noqa: ARG001
    """Smoke check: every case's converted source must be valid
    Python on whatever interpreter is running the tests. Cheap guard
    against the converter emitting syntax that depends on the source
    interpreter version."""
    converted = transform_lazy_imports(source)
    compile(converted, "<converted>", "exec")
