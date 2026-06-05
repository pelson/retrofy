"""Before/after tests for the PEP 810 (lazy imports) rewriter.

The expected output reflects PEP 810's runtime semantics:

* ``lazy import`` / ``lazy from`` are rewritten as runtime-helper assignments
  whose ``bind_name`` argument records the local name in module globals.
* Every read of a lazy-bound module global is wrapped with
  ``_retrofy_resolve(name)`` — a *function* call. The function returns its
  argument unchanged if it isn't a ``LazyProxy``, so a name that gets
  rebound later (e.g. by a plain ``import`` of the same top-level package)
  continues to work transparently.
"""

import textwrap

import pytest

from retrofy._transformations.pep810 import (
    LazyImportSyntaxError,
    transform_lazy_imports,
)

_RUNTIME_IMPORT = (
    "from retrofy._lazy_runtime import ("
    "lazy_import as _retrofy_lazy_import, "
    "lazy_import_as as _retrofy_lazy_import_as, "
    "lazy_from as _retrofy_lazy_from, "
    "resolve as _retrofy_resolve"
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
        numpy = _retrofy_lazy_import('numpy', 'numpy')

        arr = _retrofy_resolve(numpy).array([1, 2, 3])
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
        np = _retrofy_lazy_import_as('numpy', 'np')

        arr = _retrofy_resolve(np).array([1, 2, 3])
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
        xml = _retrofy_lazy_import('xml.etree.ElementTree', 'xml')

        tree = _retrofy_resolve(xml).etree.ElementTree.parse('f.xml')
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
        xml = _retrofy_lazy_import('xml.etree.ElementTree', 'xml')
        import xml.dom.minidom

        tree = _retrofy_resolve(xml).etree.ElementTree.parse('f.xml')
        dom = _retrofy_resolve(xml).dom.minidom.parseString('<a/>')
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
        Mapping = _retrofy_lazy_from('collections.abc', 'Mapping', 'Mapping')

        def f(x):
            return isinstance(x, _retrofy_resolve(Mapping))
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
        List = _retrofy_lazy_from('typing', 'List', 'List')
        D = _retrofy_lazy_from('typing', 'Dict', 'D')

        x: _retrofy_resolve(List)
        y: _retrofy_resolve(D)
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
        np = _retrofy_lazy_import_as('numpy', 'np')

        def f(np):
            return np + 1

        outer = _retrofy_resolve(np).array([1])
        """,
    )


def test_assignment_lhs_is_not_wrapped() -> None:
    src = _norm("lazy import numpy as np\n")
    out = transform_lazy_imports(src)
    assert "np = _retrofy_lazy_import_as('numpy', 'np')" in out
    assert "_retrofy_resolve(np) = " not in out


def test_rebind_in_module_still_wraps_reads() -> None:
    _assert_transform(
        """
        lazy import numpy as np

        np = 42
        print(np)
        """,
        f"""
        {_RUNTIME_IMPORT}
        np = _retrofy_lazy_import_as('numpy', 'np')

        np = 42
        print(_retrofy_resolve(np))
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
        np = _retrofy_lazy_import_as('numpy', 'np')

        x = _retrofy_resolve(np).array([])
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
        np = _retrofy_lazy_import_as('numpy', 'np')

        x = _retrofy_resolve(np).array([])
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
        json = _retrofy_lazy_import('json', 'json'); os = _retrofy_lazy_import('os', 'os')

        print(_retrofy_resolve(json), _retrofy_resolve(os))
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
        sibling = _retrofy_lazy_from('.', 'sibling', 'sibling', package=__package__)
        h = _retrofy_lazy_from('.pkg', 'helper', 'h', package=__package__)

        _retrofy_resolve(sibling).f()
        _retrofy_resolve(h)()
        """,
    )


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
        np = _retrofy_lazy_import_as('numpy', 'np')
        Mapping = _retrofy_lazy_from('collections.abc', 'Mapping', 'Mapping')
        Iter = _retrofy_lazy_from('collections.abc', 'Iterable', 'Iter')

        def f(x):
            if isinstance(x, _retrofy_resolve(Mapping)):
                return _retrofy_resolve(np).array(list(x.values()))
            if isinstance(x, _retrofy_resolve(Iter)):
                return list(x)
            return None
        """,
    )
