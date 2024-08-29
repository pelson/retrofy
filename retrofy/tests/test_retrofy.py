"""
Tests for the retrofy package.

"""

import retrofy


def test_version():
    # Check tha the package has a __version__ attribute.
    assert retrofy.__version__ is not None
