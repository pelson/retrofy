"""
Tests for the example_project package.

"""

import example_project


def test_version():
    # Check tha the package has a __version__ attribute.
    assert example_project.__version__ is not None
