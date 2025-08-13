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

To setup a build-time conversion, add the `multistage_build` backend within
`pyproject.toml`, for example:

```
[build-system]
requires = ["multistage-build", "setuptools", "wheel", "setuptools_scm==7.*", "retrofy"]
build-backend = "multistage_build:backend"

[tool.multistage-build]
build-backend = "setuptools.build_meta"
```

## Python compatibility

`retrofy` can be used with Python 3.9+, and can produce code (and wheels) which are
compatible with Python 3.7.
It is imperative that you test the produced wheels with the target versions, as there
may be syntax which is not yet handled in retrofy, resulting in a SyntaxError on your
desired Python version.


## Available transformations

For all transformations, necessary imports (`typing`, `collections.abc`, etc.) will be injected where necessary
and appropriate.

* `A | B` -> `typing.Union[A, B]`

* PEP-572 - walrus operator

* PEP-636 - match statements (structural pattern matching):
  * Literal patterns: `case 42:` -> `if value == 42:`
  * Variable binding: `case x:` -> `x = value`
  * Sequence patterns: `case [x, y]:` -> `if isinstance(value, collections.abc.Sequence) and not isinstance(value, str) and len(value) == 2: x, y = value`
  * Mapping patterns: `case {"key": value}:` -> `if isinstance(value, dict) and "key" in value: value = value["key"]`
  * Class patterns: `case Point(x=0, y=y):` -> `if isinstance(value, Point) and value.x == 0: y = value.y`
  * Guard clauses: `case x if x > 0:` -> `if x > 0: x = value`
  * OR patterns: `case 1 | 2:` -> `if value in (1, 2):`
  * Wildcard patterns: `case _:` -> `else:`
  * Star patterns: `case [x, *rest]:` -> `if len(value) >= 1: x = value[0]; rest = value[1:]`
  * As patterns: `case [x, y] as point:` -> `if len(value) == 2: point = value; x, y = value`
  * Complex nested patterns with full recursive support

* PEP-695 - type statements, generic classes, and generic functions:
  * Type statements:
    * `type Point = tuple[float, float]` -> `Point = tuple[float, float]`
    * `type GenericPoint[T] = tuple[T, T]` -> `T = typing.TypeVar("T"); GenericPoint: typing.TypeAlias = tuple[T, T]`
    * `type BoundedPoint[T: int] = tuple[T, T]` -> `T = typing.TypeVar("T", bound=int); BoundedPoint: typing.TypeAlias = tuple[T, T]`
  * Generic classes:
    * `class ClassA[T]: ...` -> `from typing import Generic, TypeVar; T = TypeVar("T"); class ClassA(Generic[T]): ...`
    * `class ClassA[T: str]: ...` -> `T = TypeVar("T", bound=str); class ClassA(Generic[T]): ...`
  * Generic functions:
    * `def func[T](a: T) -> T: ...` -> `T = typing.TypeVar("T"); def func(a: T) -> T: ...`
    * `def func[T: str](a: T) -> T: ...` -> `T = typing.TypeVar("T", bound=str); def func(a: T) -> T: ...`

* dataclasses - the __match_args__ attribute is added to the class (necessary for match statement support)

## Transformations not yet implemented

* `A | None` -> `typing.Optional[A]`
