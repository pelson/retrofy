"""
Documentation for the example_project package

"""
import sys

# Import the version from the generated _version.py file. __version__ is part
# of the public API, and we therefore ignore the "unused" (F401) lint warning.
from ._version import __version__  # noqa: F401  # pylint: disable=import-error

a = 0
while (a := a+1) < 4:
    print('I am the walrus')
print(sys.version_info)
