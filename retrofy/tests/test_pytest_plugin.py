"""Tests for the retrofy pytest plugin.

Exercises the plugin via pytester so we drive a real pytest session
against a synthesised test file that contains post-current-Python
syntax (PEP 810 ``lazy import``). The plugin must:

* let pytest collect the file (the unpatched assertion rewriter would
  fail on ``ast.parse`` or — worse — silently bypass introspection);
* leave assertion introspection enabled (a failing ``assert a == b``
  must produce the dict/list diff that the rewriter generates, not the
  bare ``AssertionError`` you get with ``--assert=plain``);
* arrange for the converted source's ``from ._retrofy_rt.lazy_imports``
  import to resolve via the in-memory payload synthesiser, since
  there's no on-disk ``_retrofy_rt/`` next to a pytester test file.
"""

import textwrap

import pytest

pytest_plugins = ["pytester"]


def _make_lazy_pkg(pytester: pytest.Pytester, modname: str, body: str) -> None:
    """Drop *body* into a ``synthpkg.<modname>`` test file. Lazy
    converted code emits ``from ._retrofy_rt.lazy_imports`` — a relative
    import — so test files must live inside a package.
    """
    pkg = pytester.mkpydir("synthpkg")
    (pkg / f"{modname}.py").write_text(textwrap.dedent(body).lstrip())


def test_lazy_import_test_collects_and_introspects_asserts(
    pytester: pytest.Pytester,
):
    _make_lazy_pkg(
        pytester,
        "test_lazy",
        """
        lazy import json

        def test_failing():
            data = json.loads('{"a": 1, "b": 2}')
            expected = {"a": 1, "b": 3}
            assert data == expected
        """,
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(failed=1)
    # The assert-rewriter introspection diff is the signal that the
    # converted source went through pytest's rewriter (rather than
    # being executed as plain ``assert`` with no introspection).
    result.stdout.fnmatch_lines(
        ["*AssertionError: assert {'a': 1, 'b': 2} == {'a': 1, 'b': 3}*"],
    )
    # The traceback source-line preview must show the failing assert
    # line. Without the linecache stash, pytest looks up the on-disk
    # file using the shifted bytecode lineno and displays ``>   ???``
    # because the lineno is past EOF.
    result.stdout.fnmatch_lines(["*>*assert data == expected*"])


def test_passing_lazy_import_test(pytester: pytest.Pytester):
    _make_lazy_pkg(
        pytester,
        "test_lazy_pass",
        """
        lazy import json

        def test_dumps():
            assert json.dumps({"x": 1}) == '{"x": 1}'
        """,
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(passed=1)


@pytest.mark.parametrize("import_mode", ["prepend", "importlib"])
def test_lazy_from_in_package_conftest_collects(
    pytester: pytest.Pytester,
    import_mode: str,
):
    """A project whose ``conftest.py`` (inside a package) uses
    ``lazy from`` at module scope must still collect.

    Pytest imports conftests via the assertion rewriter, but the
    initial conftest load runs during ``pytest_load_initial_conftests``
    — *before* ``pytest_configure`` fires — so the plugin must install
    its ``_rewrite_test`` monkey-patch and register the ``_retrofy_rt``
    runtime synthesiser at plugin *import* time, not at
    ``pytest_configure`` / ``pytest_sessionstart``.
    """
    pkg = pytester.mkpydir("synthpkg")
    (pkg / "jvm.py").write_text("x = 1\n")
    (pkg / "conftest.py").write_text(
        textwrap.dedent(
            """
            lazy from synthpkg.jvm import x

            def _force():
                return x
            """,
        ).lstrip(),
    )
    (pkg / "test_lazy_conftest.py").write_text(
        textwrap.dedent(
            """
            from synthpkg.jvm import x

            def test_x():
                assert x == 1
            """,
        ).lstrip(),
    )
    # Pass the package dir explicitly so pytest treats the conftest as
    # an initial conftest (loaded during ``pytest_load_initial_conftests``).
    result = pytester.runpytest_subprocess(
        "synthpkg",
        f"--import-mode={import_mode}",
    )
    result.assert_outcomes(passed=1)


def test_plain_test_file_unaffected(pytester: pytest.Pytester):
    """A test file with no retrofy-touched syntax must still get full
    pytest assert introspection — the plugin must not break the normal
    path.
    """
    pytester.makepyfile(
        test_plain="""
        def test_failing():
            assert {'a': 1} == {'a': 2}
        """,
    )
    result = pytester.runpytest_subprocess()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        ["*AssertionError: assert {'a': 1} == {'a': 2}*"],
    )
