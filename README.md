# retrofy

A tool which takes modern Python typing code, and makes it
compatible with older Python versions.

The idea is to be able to maintain the modern typing in your
repository, and then as part of the build stage, convert the
code to the older form. You should continue to test your project against older
versions (e.g. in CI) for full confidence in the compatibility.

## Build-time transformation

`retrofy` includes the ability to customise the build to
transform Python files into the compatibility form when creating a wheel
using any PEP-517 build backend. This includes support for editable installs
(PEP-660), which transforms the code at import-time using standard import hook
machinery.

To setup a build-time conversion, add the following to `setup.py` (it can be
the only content of `setup.py` if `pyproject.toml` is used for metadata):

```
[build-system]
requires = ["multistage-build", "setuptools", "wheel", "setuptools_scm==7.*", "setuptools-ext"]
build-backend = "multistage_build:backend"

[tool.multistage-build]
build-backend = "setuptools.build_meta"
post-build-editable = [
    {hook-function="retrofy.wheel_modifier:compatibility_via_import_hook"},
]
post-build-wheel = [
    {hook-function="retrofy.wheel_modifier:compatibility_via_rewrite"},
]
```

## Available transformations

For all transformations, an `import typing` will be injected where necessary
and appropriate.

* `A | B` -> `typing.Union[A, B]`

## Transformations not yet implemented

* `A | None` -> `typing.Optional[A]`
