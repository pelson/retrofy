# typing-to-the-future

A tool which takes modern Python typing code, and makes it
compatible with older Python versions.

The idea is to be able to maintain the modern typing in your
repository, and then as part of the build stage, convert the
code to the older form.

## Build-time transformation

`typing-to-the-future` includes a custom `setuptools` build_py command to
transform Python files into the compatibility form when creating a wheel
using any PEP-517 build backend. Currently, both editable mode and sdist
conversions are not supported (for sdists, this isn't such a concern, since
the conversion will take place when the sdist is being installed as a wheel).

To setup a build-time conversion, add the following to `setup.py` (it can be
the only content of `setup.py` if `pyproject.toml` is used for metadata):

```
from typing_to_the_future.build_cmd import cmd_class
from setuptools import setup

setup(
   cmdclass=cmd_class(),
)
```

## Available transformations

For all transformations, an `import typing` will be injected where necessary
and appropriate.

* `A | B` -> `typing.Union[A, B]`

## Transformations not yet implemented

* `A | None` -> `typing.Optional[A]`
