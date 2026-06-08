"""Tests for the retrofy pytest plugin.

Exercises the plugin via pytester so we drive a real pytest session.
The plain test below verifies that a test file with no retrofy-touched
syntax still gets full pytest assert introspection — i.e. the plugin's
``_rewrite_test`` patch falls through cleanly to the original on no-op
convert. Behavioural tests that demonstrate the convert-pipe path on
syntax that retrofy actually rewrites are tracked separately so they
can land once the relevant converter is on main.
"""

import pytest

pytest_plugins = ["pytester"]


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
