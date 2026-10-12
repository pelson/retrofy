"""
Documentation for the retrofy package

"""

# Import the version from the generated _version.py file. __version__ is part
# of the public API, and we therefore ignore the "unused" (F401) lint warning.
try:
    from ._version import __version__  # noqa: F401  # pylint: disable=import-error
except ImportError:
    # Building retrofy from a fresh checkout: its own build hooks import this
    # package before setuptools-scm writes _version.py.
    __version__ = "0+unknown"
