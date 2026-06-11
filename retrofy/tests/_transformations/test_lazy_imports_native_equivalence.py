"""Behavioural-equivalence tests for the PEP 810 (lazy imports) backport.

The match-statement transformer ([[test_match_statement.py:1810]]) sets the
pattern: for each case, run the *unconverted* source on a Python that
supports the feature natively, and the *converted* source on any
Python — both must produce the same value.

Here the unconverted source uses ``lazy`` soft-keyword syntax, which
is only parseable by CPython 3.15+. The converted source uses the
retrofy-injected ``__lazy_*__`` helpers and runs on every supported
Python.
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
    a relative import (``from ._retrofy.lazy_runtime import ...``);
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


@pytest.mark.skipif(
    sys.version_info < (3, 15),
    reason="``lazy`` soft keyword requires Python 3.15+",
)
def test_unconverted_module_not_loaded_before_use() -> None:
    """ASSUMPTION VALIDATION: native PEP 810 doesn't import the lazy
    target module until it's actually accessed."""
    # ``encodings.rot_13`` is rarely imported by anything else in a
    # test session — use it as the deferred-import canary.
    source = textwrap.dedent(
        """
        import sys
        lazy import encodings.rot_13

        was_loaded_before_use = 'encodings.rot_13' in sys.modules
        encodings.rot_13  # trigger reification
        was_loaded_after_use = 'encodings.rot_13' in sys.modules
        """,
    )
    ns: dict[str, Any] = {"__name__": "_lazy_imports_native_deferred"}
    sys.modules.pop("encodings.rot_13", None)
    exec(compile(source, "<native-deferred>", "exec"), ns)
    assert ns["was_loaded_before_use"] is False
    assert ns["was_loaded_after_use"] is True


def test_converted_module_not_loaded_before_use(tmp_path, monkeypatch) -> None:
    """EXECUTION VALIDATION: retrofy's converted source matches the
    deferred-import semantics — the lazy target stays out of
    ``sys.modules`` until something reads its name."""
    pkg_root = tmp_path / "synthcase"
    pkg_root.mkdir()
    (pkg_root / "__init__.py").write_text("")
    (pkg_root / "mod.py").write_text(
        textwrap.dedent(
            """
            import sys
            lazy import encodings.rot_13

            was_loaded_before_use = 'encodings.rot_13' in sys.modules
            encodings.rot_13
            was_loaded_after_use = 'encodings.rot_13' in sys.modules
            """,
        ).lstrip(),
    )

    monkeypatch.syspath_prepend(str(tmp_path))
    finder = MyMetaPathFinder(["synthcase"])
    monkeypatch.setattr(sys, "meta_path", [finder, *sys.meta_path])
    for name in list(sys.modules):
        if name == "synthcase" or name.startswith("synthcase."):
            del sys.modules[name]
    sys.modules.pop("encodings.rot_13", None)

    mod = importlib.import_module("synthcase.mod")
    assert mod.was_loaded_before_use is False
    assert mod.was_loaded_after_use is True


@pytest.mark.parametrize(["source", "expected"], CASES)
def test_converted_output_parses(source: str, expected: Any) -> None:  # noqa: ARG001
    """Smoke check: every case's converted source must be valid
    Python on whatever interpreter is running the tests. Cheap guard
    against the converter emitting syntax that depends on the source
    interpreter version."""
    converted = transform_lazy_imports(source)
    compile(converted, "<converted>", "exec")
