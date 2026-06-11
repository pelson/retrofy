"""Before/after tests for the PEP 810 (lazy imports) rewriter.

The expected output reflects PEP 810's runtime semantics:

* ``lazy import`` / ``lazy from`` are rewritten as runtime-helper assignments
  whose ``bind_name`` argument records the local name in module globals.
* Every read of a lazy-bound module global is wrapped with
  ``__lazy_resolve__(name)`` — a *function* call. The function returns its
  argument unchanged if it isn't a ``LazyProxy``, so a name that gets
  rebound later (e.g. by a plain ``import`` of the same top-level package)
  continues to work transparently.
"""

import textwrap
import warnings

import pytest

from retrofy._transformations.lazy_imports import (
    LazyImportSyntaxError,
    LazyModulesIgnoredWarning,
    transform_lazy_imports,
)

_RUNTIME_IMPORT = (
    "from ._retrofy.lazy_runtime import ("
    "lazy_import as __lazy_import__, "
    "lazy_import_as as __lazy_import_as__, "
    "lazy_from as __lazy_from__, "
    "resolve as __lazy_resolve__"
    ")"
)


def _norm(s: str) -> str:
    return textwrap.dedent(s).lstrip("\n")


def _assert_transform(src: str, expected: str) -> None:
    got = transform_lazy_imports(_norm(src))
    assert got == _norm(expected), (
        f"\n--- got ---\n{got}\n--- expected ---\n{_norm(expected)}"
    )


def test_passthrough_when_no_lazy() -> None:
    src = _norm(
        """
        import os
        x = 1
        """,
    )
    assert transform_lazy_imports(src) == src


def test_lazy_import_simple() -> None:
    _assert_transform(
        """
        lazy import numpy

        arr = numpy.array([1, 2, 3])
        """,
        f"""
        {_RUNTIME_IMPORT}
        numpy = __lazy_import__('numpy', 'numpy')

        arr = __lazy_resolve__(numpy).array([1, 2, 3])
        """,
    )


def test_lazy_import_as_alias() -> None:
    _assert_transform(
        """
        lazy import numpy as np

        arr = np.array([1, 2, 3])
        """,
        f"""
        {_RUNTIME_IMPORT}
        np = __lazy_import_as__('numpy', 'np')

        arr = __lazy_resolve__(np).array([1, 2, 3])
        """,
    )


def test_lazy_import_dotted_binds_top() -> None:
    _assert_transform(
        """
        lazy import xml.etree.ElementTree

        tree = xml.etree.ElementTree.parse('f.xml')
        """,
        f"""
        {_RUNTIME_IMPORT}
        xml = __lazy_import__('xml.etree.ElementTree', 'xml')

        tree = __lazy_resolve__(xml).etree.ElementTree.parse('f.xml')
        """,
    )


def test_mixed_lazy_and_eager_same_top_level() -> None:
    _assert_transform(
        """
        lazy import xml.etree.ElementTree
        import xml.dom.minidom

        tree = xml.etree.ElementTree.parse('f.xml')
        dom = xml.dom.minidom.parseString('<a/>')
        """,
        f"""
        {_RUNTIME_IMPORT}
        xml = __lazy_import__('xml.etree.ElementTree', 'xml')
        import xml.dom.minidom

        tree = __lazy_resolve__(xml).etree.ElementTree.parse('f.xml')
        dom = __lazy_resolve__(xml).dom.minidom.parseString('<a/>')
        """,
    )


def test_lazy_from_single_name() -> None:
    _assert_transform(
        """
        lazy from collections.abc import Mapping

        def f(x):
            return isinstance(x, Mapping)
        """,
        f"""
        {_RUNTIME_IMPORT}
        Mapping = __lazy_from__('collections.abc', 'Mapping', 'Mapping')

        def f(x):
            return isinstance(x, __lazy_resolve__(Mapping))
        """,
    )


def test_lazy_from_multiple_names_with_alias() -> None:
    _assert_transform(
        """
        lazy from typing import List, Dict as D

        x: List
        y: D
        """,
        f"""
        {_RUNTIME_IMPORT}
        List = __lazy_from__('typing', 'List', 'List')
        D = __lazy_from__('typing', 'Dict', 'D')

        x: __lazy_resolve__(List)
        y: __lazy_resolve__(D)
        """,
    )


def test_local_shadowing_is_not_rewritten() -> None:
    _assert_transform(
        """
        lazy import numpy as np

        def f(np):
            return np + 1

        outer = np.array([1])
        """,
        f"""
        {_RUNTIME_IMPORT}
        np = __lazy_import_as__('numpy', 'np')

        def f(np):
            return np + 1

        outer = __lazy_resolve__(np).array([1])
        """,
    )


def test_assignment_lhs_is_not_wrapped() -> None:
    src = _norm("lazy import numpy as np\n")
    out = transform_lazy_imports(src)
    assert "np = __lazy_import_as__('numpy', 'np')" in out
    assert "__lazy_resolve__(np) = " not in out


def test_rebind_in_module_still_wraps_reads() -> None:
    _assert_transform(
        """
        lazy import numpy as np

        np = 42
        print(np)
        """,
        f"""
        {_RUNTIME_IMPORT}
        np = __lazy_import_as__('numpy', 'np')

        np = 42
        print(__lazy_resolve__(np))
        """,
    )


def test_lazy_inside_function_is_rejected() -> None:
    src = _norm(
        """
        def f():
            lazy import numpy
        """,
    )
    with pytest.raises(LazyImportSyntaxError):
        transform_lazy_imports(src)


def test_lazy_inside_if_block_is_rejected() -> None:
    src = _norm(
        """
        if True:
            lazy import numpy
        """,
    )
    with pytest.raises(LazyImportSyntaxError):
        transform_lazy_imports(src)


def test_future_import_stays_first() -> None:
    _assert_transform(
        """
        from __future__ import annotations

        lazy import numpy as np

        x = np.array([])
        """,
        f"""
        from __future__ import annotations

        {_RUNTIME_IMPORT}
        np = __lazy_import_as__('numpy', 'np')

        x = __lazy_resolve__(np).array([])
        """,
    )


def test_module_docstring_stays_first() -> None:
    _assert_transform(
        '''
        """Module docstring."""

        lazy import numpy as np

        x = np.array([])
        ''',
        f'''
        """Module docstring."""

        {_RUNTIME_IMPORT}
        np = __lazy_import_as__('numpy', 'np')

        x = __lazy_resolve__(np).array([])
        ''',
    )


def test_semicolon_separated_lazy_statements() -> None:
    # Multiple ``lazy`` statements separated by ``;`` on a single
    # physical line. The rewriter treats ``;`` as a statement boundary
    # both when looking for ``lazy`` at the start and when collecting
    # the trailing tokens of the current ``lazy`` clause, so each
    # ``lazy`` is rewritten independently.
    _assert_transform(
        """
        lazy import json; lazy import os

        print(json, os)
        """,
        f"""
        {_RUNTIME_IMPORT}
        json = __lazy_import__('json', 'json'); os = __lazy_import__('os', 'os')

        print(__lazy_resolve__(json), __lazy_resolve__(os))
        """,
    )


def test_relative_lazy_from() -> None:
    # Relative ``lazy from`` imports need the calling module's
    # ``__package__`` so ``importlib.import_module`` can resolve them.
    _assert_transform(
        """
        lazy from . import sibling
        lazy from .pkg import helper as h

        sibling.f()
        h()
        """,
        f"""
        {_RUNTIME_IMPORT}
        sibling = __lazy_from__('.', 'sibling', 'sibling', package=__package__)
        h = __lazy_from__('.pkg', 'helper', 'h', package=__package__)

        __lazy_resolve__(sibling).f()
        __lazy_resolve__(h)()
        """,
    )


def test_helper_names_avoid_collision_with_user_source() -> None:
    """If the user's source already binds a name that the rewriter
    would otherwise inject, all four helper names get the same numeric
    suffix so the generated code can't shadow user code."""
    src = _norm(
        """
        lazy import numpy as np

        # Pre-existing name that the rewriter would clobber.
        __lazy_import__ = 'user-bound'
        x = np.array([1])
        """,
    )
    out = transform_lazy_imports(src)
    # Suffixed forms used everywhere.
    assert "__lazy_import_2__" in out
    assert "__lazy_import_as_2__" in out
    assert "__lazy_resolve_2__" in out
    # Un-suffixed forms appear only as the user's own binding /
    # references — never as injected calls.
    assert "= __lazy_import__(" not in out
    assert "= __lazy_import_as__(" not in out
    assert "__lazy_resolve__(" not in out
    # User's literal binding is preserved verbatim.
    assert "__lazy_import__ = 'user-bound'" in out


def test_lazy_modules_declaration_warns_and_is_left_alone() -> None:
    """retrofy doesn't backport the declarative ``__lazy_modules__``
    form of PEP 810. A user declaration must surface as a warning and
    the source must be returned unchanged (the assignment is inert on
    older interpreters)."""
    src = _norm(
        """
        __lazy_modules__ = {"json"}

        import json
        x = json.dumps({})
        """,
    )
    with pytest.warns(LazyModulesIgnoredWarning, match="line 1"):
        out = transform_lazy_imports(src)
    assert out == src


def test_lazy_modules_inside_function_does_not_warn() -> None:
    """Only module-scope declarations carry the PEP 810 meaning;
    function-local writes are irrelevant and must not trigger the
    warning (otherwise common variable names get noisy)."""
    src = _norm(
        """
        def f():
            __lazy_modules__ = {"json"}
            return __lazy_modules__
        """,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error", LazyModulesIgnoredWarning)
        # Should not raise.
        transform_lazy_imports(src)


def test_lazy_modules_warning_with_lazy_keyword_still_rewrites() -> None:
    """If a file has both ``__lazy_modules__`` and explicit ``lazy``
    syntax, we still rewrite the ``lazy`` form and emit the warning
    for the declarative form."""
    src = _norm(
        """
        __lazy_modules__ = {"os"}

        lazy import json
        x = json.dumps({})
        """,
    )
    with pytest.warns(LazyModulesIgnoredWarning):
        out = transform_lazy_imports(src)
    # ``lazy import json`` still got rewritten.
    assert "__lazy_import__('json'," in out
    # The user's ``__lazy_modules__`` line survives verbatim.
    assert '__lazy_modules__ = {"os"}' in out


def test_multiple_lazy_statements() -> None:
    _assert_transform(
        """
        lazy import numpy as np
        lazy from collections.abc import Mapping, Iterable as Iter

        def f(x):
            if isinstance(x, Mapping):
                return np.array(list(x.values()))
            if isinstance(x, Iter):
                return list(x)
            return None
        """,
        f"""
        {_RUNTIME_IMPORT}
        np = __lazy_import_as__('numpy', 'np')
        Mapping = __lazy_from__('collections.abc', 'Mapping', 'Mapping')
        Iter = __lazy_from__('collections.abc', 'Iterable', 'Iter')

        def f(x):
            if isinstance(x, __lazy_resolve__(Mapping)):
                return __lazy_resolve__(np).array(list(x.values()))
            if isinstance(x, __lazy_resolve__(Iter)):
                return list(x)
            return None
        """,
    )
