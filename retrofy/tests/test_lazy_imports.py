"""Before/after tests for the PEP 810 (lazy imports) rewriter.

The expected output reflects PEP 810's runtime semantics and the
static-analyser story we ship for issue #45:

* Each ``lazy import`` / ``lazy from`` is rewritten to a per-statement
  ``if <name>.TYPE_CHECKING: <real import>\\nelse: <runtime binding>``
  block. Static type checkers see the ``if`` branch (the real import)
  and infer proper types; the interpreter takes the ``else`` branch
  and gets the lazy proxy.
* Every read of a lazy-bound module global is wrapped with
  ``__lazy_reify__(name)`` — a *function* call. The function returns
  its argument unchanged if it isn't a ``LazyProxy``, so a name that
  gets rebound later continues to work transparently. Reads inside
  ``cst.Annotation`` nodes are exempt when the module has
  ``from __future__ import annotations`` (issue #45).
* The preamble adds ``from ._retrofy_rt.lazy_imports import ...`` and
  ``import typing as __lazy_typing__`` (or collapses the alias to
  plain ``typing`` when the source already imports it safely). The
  ``if`` header uses whichever name reaches the typing module.

Expected outputs are assembled by ``_expected(*sections)`` so that
multi-line block content doesn't fight ``textwrap.dedent`` — each
section is a self-contained string joined at column 0. The
``_RUNTIME_IMPORT`` marker in an expected string is replaced with the
matching preamble lines (typing alias + retrofy runtime import),
including only the helpers the body actually references.
"""

import textwrap
import warnings

import pytest

from retrofy._transformations.lazy_imports import (
    _BASE_HELPERS,
    LazyImportSyntaxError,
    LazyModulesIgnoredWarning,
    transform_lazy_imports,
)

_RUNTIME_IMPORT = "<<RUNTIME-IMPORT>>"


def _norm(s: str) -> str:
    return textwrap.dedent(s).lstrip("\n")


def _build_preamble(body: str) -> str:
    """Reconstruct the injected preamble for a body containing the
    given helper aliases. Adds ``import typing as __lazy_typing__``
    only when the body references the placeholder — the alias is
    collapsed to plain ``typing`` when the source already has a safe
    top-level ``import typing``.
    """
    lines = []
    if "__lazy_typing__" in body:
        lines.append("import typing as __lazy_typing__")
    aliases = [
        f"{role} as {_BASE_HELPERS[role]}"
        for role in _BASE_HELPERS
        if _BASE_HELPERS[role] in body
    ]
    lines.append("from ._retrofy_rt.lazy_imports import " + ", ".join(aliases))
    return "\n".join(lines)


def _block(
    tc_lines: list[str],
    rt_lines: list[str],
    tc_name: str = "__lazy_typing__",
) -> str:
    """Format a per-statement ``if <tc_name>.TYPE_CHECKING: ...\\n
    else: ...`` block matching the transformer's emit shape.
    """
    tc_body = "\n".join(f"    {line}" for line in tc_lines)
    rt_body = "\n".join(f"    {line}" for line in rt_lines)
    return f"if {tc_name}.TYPE_CHECKING:\n{tc_body}\nelse:\n{rt_body}"


def _expected(*sections: str) -> str:
    """Join expected-output sections at column 0, with a trailing
    newline. Sections are separated by exactly one ``\\n`` — pass an
    empty string as a section to insert a blank line.
    """
    return "\n".join(sections) + "\n"


def _assert_transform(src: str, expected: str) -> None:
    if _RUNTIME_IMPORT in expected:
        rest = expected.replace(_RUNTIME_IMPORT, "")
        expected = expected.replace(_RUNTIME_IMPORT, _build_preamble(rest))
    got = transform_lazy_imports(_norm(src))
    assert got == expected, f"\n--- got ---\n{got}\n--- expected ---\n{expected}"


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
        _expected(
            _RUNTIME_IMPORT,
            _block(["import numpy"], ["numpy = __lazy_import__('numpy', 'numpy')"]),
            "",
            "arr = __lazy_reify__(numpy).array([1, 2, 3])",
        ),
    )


def test_lazy_import_as_alias() -> None:
    _assert_transform(
        """
        lazy import numpy as np

        arr = np.array([1, 2, 3])
        """,
        _expected(
            _RUNTIME_IMPORT,
            _block(["import numpy as np"], ["np = __lazy_import_as__('numpy', 'np')"]),
            "",
            "arr = __lazy_reify__(np).array([1, 2, 3])",
        ),
    )


def test_lazy_import_dotted_binds_top() -> None:
    _assert_transform(
        """
        lazy import xml.etree.ElementTree

        tree = xml.etree.ElementTree.parse('f.xml')
        """,
        _expected(
            _RUNTIME_IMPORT,
            _block(
                ["import xml.etree.ElementTree"],
                ["xml = __lazy_import__('xml.etree.ElementTree', 'xml')"],
            ),
            "",
            "tree = __lazy_reify__(xml).etree.ElementTree.parse('f.xml')",
        ),
    )


def test_mixed_lazy_and_eager_same_top_level() -> None:
    _assert_transform(
        """
        lazy import xml.etree.ElementTree
        import xml.dom.minidom

        tree = xml.etree.ElementTree.parse('f.xml')
        dom = xml.dom.minidom.parseString('<a/>')
        """,
        _expected(
            _RUNTIME_IMPORT,
            _block(
                ["import xml.etree.ElementTree"],
                ["xml = __lazy_import__('xml.etree.ElementTree', 'xml')"],
            ),
            "import xml.dom.minidom",
            "",
            "tree = __lazy_reify__(xml).etree.ElementTree.parse('f.xml')",
            "dom = __lazy_reify__(xml).dom.minidom.parseString('<a/>')",
        ),
    )


def test_lazy_from_single_name() -> None:
    _assert_transform(
        """
        lazy from collections.abc import Mapping

        def f(x):
            return isinstance(x, Mapping)
        """,
        _expected(
            _RUNTIME_IMPORT,
            _block(
                ["from collections.abc import Mapping"],
                ["Mapping = __lazy_from__('collections.abc', 'Mapping', 'Mapping')"],
            ),
            "",
            "def f(x):",
            "    return isinstance(x, __lazy_reify__(Mapping))",
        ),
    )


def test_lazy_from_multiple_names_with_alias() -> None:
    # Without ``from __future__ import annotations`` the reads inside
    # annotations still get wrapped — see
    # ``test_annotations_not_wrapped_under_future_annotations`` for the
    # opposite behaviour.
    _assert_transform(
        """
        lazy from typing import List, Dict as D

        x: List
        y: D
        """,
        _expected(
            _RUNTIME_IMPORT,
            _block(
                ["from typing import List, Dict as D"],
                [
                    "List = __lazy_from__('typing', 'List', 'List')",
                    "D = __lazy_from__('typing', 'Dict', 'D')",
                ],
            ),
            "",
            "x: __lazy_reify__(List)",
            "y: __lazy_reify__(D)",
        ),
    )


def test_local_shadowing_is_not_rewritten() -> None:
    _assert_transform(
        """
        lazy import numpy as np

        def f(np):
            return np + 1

        outer = np.array([1])
        """,
        _expected(
            _RUNTIME_IMPORT,
            _block(["import numpy as np"], ["np = __lazy_import_as__('numpy', 'np')"]),
            "",
            "def f(np):",
            "    return np + 1",
            "",
            "outer = __lazy_reify__(np).array([1])",
        ),
    )


def test_assignment_lhs_is_not_wrapped() -> None:
    src = _norm("lazy import numpy as np\n")
    out = transform_lazy_imports(src)
    assert "np = __lazy_import_as__('numpy', 'np')" in out
    assert "__lazy_reify__(np) = " not in out


def test_rebind_in_module_still_wraps_reads() -> None:
    _assert_transform(
        """
        lazy import numpy as np

        np = 42
        print(np)
        """,
        _expected(
            _RUNTIME_IMPORT,
            _block(["import numpy as np"], ["np = __lazy_import_as__('numpy', 'np')"]),
            "",
            "np = 42",
            "print(__lazy_reify__(np))",
        ),
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
        _expected(
            "from __future__ import annotations",
            "",
            _RUNTIME_IMPORT,
            _block(["import numpy as np"], ["np = __lazy_import_as__('numpy', 'np')"]),
            "",
            "x = __lazy_reify__(np).array([])",
        ),
    )


def test_module_docstring_stays_first() -> None:
    _assert_transform(
        '''
        """Module docstring."""

        lazy import numpy as np

        x = np.array([])
        ''',
        _expected(
            '"""Module docstring."""',
            "",
            _RUNTIME_IMPORT,
            _block(["import numpy as np"], ["np = __lazy_import_as__('numpy', 'np')"]),
            "",
            "x = __lazy_reify__(np).array([])",
        ),
    )


def test_semicolon_separated_lazy_statements() -> None:
    # Multiple ``lazy`` statements separated by ``;`` on a single
    # physical line. Each block is multi-line, so the rewriter breaks
    # the semicolon into a newline — the semicolon-adjacent-to-``lazy``
    # edit consumes the ``;`` and any trailing whitespace so each
    # block starts on its own line.
    _assert_transform(
        """
        lazy import json; lazy import os

        print(json, os)
        """,
        _expected(
            _RUNTIME_IMPORT,
            _block(["import json"], ["json = __lazy_import__('json', 'json')"]),
            _block(["import os"], ["os = __lazy_import__('os', 'os')"]),
            "",
            "print(__lazy_reify__(json), __lazy_reify__(os))",
        ),
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
        _expected(
            _RUNTIME_IMPORT,
            _block(
                ["from . import sibling"],
                [
                    "sibling = __lazy_from__('.', 'sibling', 'sibling', package=__package__)",
                ],
            ),
            _block(
                ["from .pkg import helper as h"],
                ["h = __lazy_from__('.pkg', 'helper', 'h', package=__package__)"],
            ),
            "",
            "__lazy_reify__(sibling).f()",
            "__lazy_reify__(h)()",
        ),
    )


def test_helper_names_avoid_collision_with_user_source() -> None:
    """If the user's source already binds *any* name that one of the
    helpers would otherwise take, all four helper names get the same
    numeric suffix so the generated code can't shadow user code. The
    suffix is uniform across helpers — even helpers the body doesn't
    invoke — so a future edit that introduces a new lazy form into
    the source doesn't accidentally collide.
    """
    src = _norm(
        """
        lazy import numpy as np

        # Pre-existing name that the rewriter would clobber if we
        # used the un-suffixed ``__lazy_import_as__``.
        __lazy_import_as__ = 'user-bound'
        x = np.array([1])
        """,
    )
    out = transform_lazy_imports(src)
    # Helpers the body actually uses are emitted in the suffixed form.
    assert "__lazy_import_as_2__" in out
    assert "__lazy_reify_2__" in out
    # Un-suffixed helper forms must not appear as injected calls.
    assert "= __lazy_import_as__(" not in out
    assert "__lazy_reify__(" not in out
    # User's literal binding is preserved verbatim.
    assert "__lazy_import_as__ = 'user-bound'" in out


def test_emitted_import_contains_wheel_build_marker() -> None:
    """Pin the contract between the converter and the wheel-build hook.

    ``retrofy._pep517_hooks.compatibility_via_rewrite`` greps each
    converted module for a marker substring to decide whether to drop
    the ``_retrofy_rt/`` payload sub-package alongside it. If the
    converter ever emits the runtime import in a form that doesn't
    contain the marker, the wheel-build hook silently skips the
    payload drop and the installed wheel is unusable. Catch that here.
    """
    from retrofy._pep517_hooks import _LAZY_RUNTIME_IMPORT_MARKER

    out = transform_lazy_imports("lazy import json\n")
    assert _LAZY_RUNTIME_IMPORT_MARKER in out


def test_helper_names_suffix_is_uniform_across_helpers() -> None:
    """Collision on a name that is *not* otherwise emitted still
    forces all helpers to use the same suffix. Catches a regression
    where we'd suffix-collide only the colliding helper.
    """
    src = _norm(
        """
        lazy import numpy as np

        # ``__lazy_from__`` is not used by this module (no ``lazy
        # from`` statement), but a user binding still must force the
        # uniform suffix.
        __lazy_from__ = 'user-bound'
        x = np.array([1])
        """,
    )
    out = transform_lazy_imports(src)
    assert "__lazy_import_as_2__" in out
    assert "__lazy_reify_2__" in out
    assert "__lazy_from__ = 'user-bound'" in out


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


def test_annotations_not_wrapped_under_future_annotations() -> None:
    # Regression for issue #45. With ``from __future__ import annotations``
    # every annotation is a string at runtime — wrapping lazy-bound names
    # in ``__lazy_reify__(...)`` inside annotations is dead code AND
    # breaks mypy's [valid-type] check. Also verifies that a safely-
    # bound top-level ``import typing`` collapses the emit's typing
    # alias to the plain ``typing`` name.
    _assert_transform(
        """
        from __future__ import annotations
        import typing

        lazy from some_pkg import Foo

        def do_it(x: typing.Optional[Foo]) -> Foo: ...

        y: Foo
        """,
        _expected(
            "from __future__ import annotations",
            _RUNTIME_IMPORT,
            "import typing",
            "",
            _block(
                ["from some_pkg import Foo"],
                ["Foo = __lazy_from__('some_pkg', 'Foo', 'Foo')"],
                tc_name="typing",
            ),
            "",
            "def do_it(x: typing.Optional[Foo]) -> Foo: ...",
            "",
            "y: Foo",
        ),
    )


def test_annotation_and_runtime_use_under_future_annotations() -> None:
    # Non-annotation uses of the same lazy name still get wrapped —
    # only reads inside the annotation slot are left alone.
    _assert_transform(
        """
        from __future__ import annotations

        lazy from some_pkg import Foo

        def do_it(x: Foo) -> Foo:
            return Foo()
        """,
        _expected(
            "from __future__ import annotations",
            "",
            _RUNTIME_IMPORT,
            _block(
                ["from some_pkg import Foo"],
                ["Foo = __lazy_from__('some_pkg', 'Foo', 'Foo')"],
            ),
            "",
            "def do_it(x: Foo) -> Foo:",
            "    return __lazy_reify__(Foo)()",
        ),
    )


def test_annotations_still_wrapped_without_future_annotations() -> None:
    # Without ``from __future__ import annotations`` the annotation is
    # evaluated at definition time on pre-3.14 Pythons — wrapping is
    # required to preserve PEP 810's "referencing the name imports it"
    # semantics.
    _assert_transform(
        """
        lazy from typing import List

        x: List
        """,
        _expected(
            _RUNTIME_IMPORT,
            _block(
                ["from typing import List"],
                ["List = __lazy_from__('typing', 'List', 'List')"],
            ),
            "",
            "x: __lazy_reify__(List)",
        ),
    )


def test_typing_alias_collapsed_when_source_imports_typing() -> None:
    # The phase-1 emit uses a mangled ``__lazy_typing__`` placeholder
    # for the ``TYPE_CHECKING`` reference. Phase 3's libcst pass sees
    # that ``typing`` at module scope is exclusively bound to the
    # stdlib module via ``import typing`` and rewrites the placeholder
    # back to plain ``typing`` — no injected alias.
    _assert_transform(
        """
        import typing

        lazy from mod import X

        x = X
        """,
        _expected(
            _RUNTIME_IMPORT,
            "import typing",
            "",
            _block(
                ["from mod import X"],
                ["X = __lazy_from__('mod', 'X', 'X')"],
                tc_name="typing",
            ),
            "",
            "x = __lazy_reify__(X)",
        ),
    )


def test_typing_alias_kept_when_typing_shadowed_by_for_loop() -> None:
    # Top-level ``import typing`` + a subsequent ``for typing in ...``
    # at module scope means ``typing`` is no longer reliably the stdlib
    # module. The libcst pass falls back to the mangled alias so every
    # emitted TYPE_CHECKING reference is safe regardless of position.
    _assert_transform(
        """
        import typing

        for typing in []:
            pass

        lazy from mod import X

        x = X
        """,
        _expected(
            _RUNTIME_IMPORT,
            "import typing",
            "",
            "for typing in []:",
            "    pass",
            "",
            _block(
                ["from mod import X"],
                ["X = __lazy_from__('mod', 'X', 'X')"],
            ),
            "",
            "x = __lazy_reify__(X)",
        ),
    )


def test_typing_alias_kept_when_typing_import_is_aliased() -> None:
    # ``import typing as t`` doesn't bind the bare name ``typing``, so
    # phase 3 has to inject its own ``import typing as __lazy_typing__``
    # rather than trusting the user's aliased import.
    _assert_transform(
        """
        import typing as t

        lazy from mod import X

        x = X
        """,
        _expected(
            _RUNTIME_IMPORT,
            "import typing as t",
            "",
            _block(
                ["from mod import X"],
                ["X = __lazy_from__('mod', 'X', 'X')"],
            ),
            "",
            "x = __lazy_reify__(X)",
        ),
    )


def test_typing_alias_kept_when_only_from_typing_import() -> None:
    # ``from typing import X`` does not bind the name ``typing``; the
    # libcst pass must inject its own alias.
    _assert_transform(
        """
        from typing import Optional

        lazy from mod import X

        x = X
        """,
        _expected(
            _RUNTIME_IMPORT,
            "from typing import Optional",
            "",
            _block(
                ["from mod import X"],
                ["X = __lazy_from__('mod', 'X', 'X')"],
            ),
            "",
            "x = __lazy_reify__(X)",
        ),
    )


def test_typing_alias_suffix_bumps_on_collision() -> None:
    # If the user's source already binds ``__lazy_typing__``, the
    # ``typing`` alias must bump to ``__lazy_typing_2__`` etc. so we
    # can't clobber the user's binding.
    src = _norm(
        """
        __lazy_typing__ = 'user-bound'

        lazy from mod import X

        x = X
        """,
    )
    out = transform_lazy_imports(src)
    # Bumped alias appears both in the injected import and in the
    # emitted block's ``if`` header.
    assert "import typing as __lazy_typing_2__" in out
    assert "if __lazy_typing_2__.TYPE_CHECKING:" in out
    # Un-suffixed placeholder must not appear as an injected alias.
    assert "import typing as __lazy_typing__" not in out
    assert "if __lazy_typing__.TYPE_CHECKING:" not in out
    # User's literal binding is preserved verbatim.
    assert "__lazy_typing__ = 'user-bound'" in out


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
        _expected(
            _RUNTIME_IMPORT,
            _block(["import numpy as np"], ["np = __lazy_import_as__('numpy', 'np')"]),
            _block(
                ["from collections.abc import Mapping, Iterable as Iter"],
                [
                    "Mapping = __lazy_from__('collections.abc', 'Mapping', 'Mapping')",
                    "Iter = __lazy_from__('collections.abc', 'Iterable', 'Iter')",
                ],
            ),
            "",
            "def f(x):",
            "    if isinstance(x, __lazy_reify__(Mapping)):",
            "        return __lazy_reify__(np).array(list(x.values()))",
            "    if isinstance(x, __lazy_reify__(Iter)):",
            "        return list(x)",
            "    return None",
        ),
    )
