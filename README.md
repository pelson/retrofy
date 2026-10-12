# retrofy

A tool which takes modern Python code (typing syntax, `match` statements,
PEP 810 `lazy` imports, ...) and makes it compatible with older Python versions.

Try it in the browser: https://pelson.github.io/retrofy/playground/

The idea is to be able to maintain the modern typing in your
repository, and then as part of the build stage, convert the
code to the older form. You should continue to test your project against older
versions (e.g. in CI) for full confidence in the compatibility.

## Example

Write modern Python in your repository:

```python
def first[T](items: list[T]) -> T | None:
    return items[0] if items else None


def describe(value: int | str | None) -> str:
    match value:
        case None:
            return "nothing"
        case 0:
            return "zero"
        case _:
            return str(value)
```

and retrofy rewrites it, when the wheel is built, into code that runs on older
Pythons:

```python
import typing
T = typing.TypeVar("T")
def first(items: typing.List[T]) -> typing.Optional[T]:
    return items[0] if items else None


def describe(value: typing.Union[int, str, None]) -> str:
    if value is None:
        return "nothing"
    elif value == 0:
        return "zero"
    else:
        return str(value)
```

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

### Lowering `Requires-Python`

The built artefacts can install on an older Python than the source needs. Tell
retrofy which, with `[tool.retrofy] target-python`, and list `requires-python` as
dynamic so retrofy can supply it:

```toml
[build-system]
requires = ["multistage-build>=0.4", "setuptools", "retrofy"]
build-backend = "multistage_build:backend"

[project]
dynamic = ["requires-python"]

[tool.retrofy]
target-python = "3.9"           # what the built artefacts install onto
```

retrofy then supplies `requires-python = ">=3.9"` to setuptools for wheel, sdist and
editable builds, in memory (`pyproject.toml` is never modified). Tools that read
`pyproject.toml` directly see that the value is dynamic and ask the build backend,
as PEP 621 requires.

A static `requires-python` (your *source* target, e.g. `">=3.13"`) also works:
retrofy lowers `Requires-Python` in the built metadata, keeping any other clauses
such as `!=3.12.*`. But changing a static field in the build deviates from PEP 621,
and tools that read `pyproject.toml` directly (rather than asking the backend) will
see the source target.

Either way, the sdist is lowered too: its source is converted, its `pyproject.toml`
gets a static `requires-python = ">=3.9"`, and `retrofy` is dropped from its build
requirements, so it builds on 3.9 without retrofy.

### Editable installs

An editable install converts the code at import time (via a `.pth` import hook),
so it needs retrofy at runtime. Whether retrofy adds itself to the editable
install's dependencies follows `pyproject.toml`, as PEP 621 requires:

* `dependencies` listed in `[project] dynamic` (fully dynamic, or
  [PEP 808](https://peps.python.org/pep-0808/) partially dynamic, where your
  build backend supports it): retrofy adds `Requires-Dist: retrofy`.
* Static `dependencies`: retrofy leaves them alone (and says so in the build
  output), so install retrofy yourself in the environment of the editable
  install.

If retrofy is missing, importing the package fails with an `ImportError` which
says how to fix it. Interpreter startup, and other imports, are unaffected.

### Editable installs on Python 3.7/3.8

`libcst` does not install on 3.7/3.8, so retrofy delegates per-file conversion to
a separate Python (3.9+) that does have retrofy installed. Prepare one venv per
machine:

```
python3 -m venv ~/.cache/retrofy/converter-venv
~/.cache/retrofy/converter-venv/bin/pip install retrofy
```

Override the location with `RETROFY_CONVERTER_PYTHON` (a shell command, e.g.
`/opt/py3.13/bin/python`). If neither is present and `uv` is on `PATH`, retrofy
falls back to `uv run --with retrofy python`.

If retrofy cannot be installed into the 3.7/3.8 environment at all, run
`retrofy setup-editable` from a 3.9+ Python instead. It writes the editable import
hook (plus a bundled bootstrap, so no retrofy install is needed in the target) into
the target interpreter's site-packages:

```
retrofy setup-editable path/to/project --target-python /path/to/py3.7/bin/python
```

It does *not* install the project's dependencies into the target environment.

## Converting existing code

Converting in the project's own build (above) is best, because the project's CI
can then test the result on the old Pythons. For code whose build you don't
control, there are two commands, each writing a converted copy:

```
retrofy wheel some_pkg-1.0-py3-none-any.whl --target-python 3.9 -o converted/
retrofy files src/ -o converted-src/
```

`retrofy wheel` takes a pure-Python (`py3-none-any`) wheel and keeps its name and
version. It lowers the code, adds retrofy's small runtime where needed, lowers
`Requires-Python` to `--target-python`, and adds `typing_extensions` to the
dependencies when the code may need it. `retrofy files` converts the `.py` files in
a copy of a directory, copying other files as they are; the output directory must
be empty. Neither can check the converted code's behaviour, so test it on the
target Python before relying on it.

## pytest integration

retrofy registers a pytest plugin (via the `pytest11` entry point) which routes
test modules and `conftest.py` files through the converter before pytest's
assertion rewriter sees them. Tests can therefore use syntax newer than the
interpreter running them while keeping full `assert` introspection.


## Available transformations

For all transformations, necessary imports (`typing`, `collections.abc`, etc.) will be injected where necessary
and appropriate.

* PEP-604 - union types:
  * In annotations, `type` statement values and type parameter bounds (at any
    nesting depth):
    * `A | B | C` -> `typing.Union[A, B, C]`
    * `A | None` -> `typing.Optional[A]`
    * `list[int | None]` -> `typing.List[typing.Optional[int]]`
  * In `isinstance` / `issubclass`:
    `isinstance(x, A | B | None)` -> `isinstance(x, (A, B, type(None)))`
  * Elsewhere, when every operand is certainly a type (a built-in class, `None`
    or a `typing` construct): `MaybeInt = int | None` -> `MaybeInt = typing.Optional[int]`

* PEP-585 - built-in generics: `list[X]`, `dict[K, V]`, `tuple[...]`, `set[X]`,
  `frozenset[X]` and `type[X]` -> `typing.List[X]`, `typing.Dict[K, V]`, etc.
  (names which do not resolve to the built-in are left untouched)

* PEP-585 - stdlib generics. Imports of names which only became subscriptable in
  Python 3.9 are rewritten to a `sys.version_info`-gated fallback to `typing`:
  * `collections.abc` ABCs (`Mapping`, `Sequence`, `Callable`, ...;
    `collections.abc.Set` falls back to `typing.AbstractSet`)
  * `collections`: `deque`, `defaultdict`, `OrderedDict`, `Counter`, `ChainMap`
  * `contextlib`: `AbstractContextManager`, `AbstractAsyncContextManager`
  * `re`: `Pattern`, `Match`

* `typing` -> `typing_extensions` fallbacks, gated on `sys.version_info`, for
  `Literal`, `final`, `TypedDict` (3.8), `get_args`, `get_origin` (3.10), `Self`,
  `LiteralString`, `Never`, `assert_never`, `assert_type`, `reveal_type`,
  `dataclass_transform` (3.11) and `override` (3.12). With `target-python` below
  3.12 and `dependencies` listed in `[project] dynamic`, retrofy adds
  `Requires-Dist: typing_extensions>=4.4; python_version < "3.12"` to the wheel
  and editable metadata. With static `dependencies`, add it yourself. Either way,
  if `typing_extensions` is missing (or too old) at runtime, the import fails with
  an `ImportError` naming the package and the Python version that needs it.

* PEP-810 - lazy imports: `lazy import x` and `lazy from x import y` are rewritten
  to deferred imports which reify on first use. The small runtime this needs is
  copied into your built wheel as a private `_retrofy_rt` sub-package, so the
  installed package does not depend on retrofy. Names used only in annotations are
  wrapped so that type checkers (e.g. mypy) still see the real types.

* PEP-572 - walrus operator

* PEP-617 - parenthesised context managers: `with (a as x, b as y):` ->
  `with a as x, b as y:`. Line breaks and comments inside the parentheses move
  into parentheses around the individual items, so both line numbers and comments
  are kept.

* PEP-758 - `except` / `except*` with several exception types and no
  parentheses: `except A, B:` -> `except (A, B):`

* PEP-636 - match statements (structural pattern matching):
  * Literal patterns: `case 42:` -> `if value == 42:`
  * Variable binding: `case x:` -> `x = value`
  * Sequence patterns: `case [x, y]:` -> `if isinstance(value, collections.abc.Sequence) and not isinstance(value, str) and len(value) == 2: x, y = value`
  * Mapping patterns: `case {"key": value}:` -> `if isinstance(value, dict) and "key" in value: value = value["key"]`
  * Class patterns: `case Point(x=0, y=y):` -> `if isinstance(value, Point) and value.x == 0: y = value.y`
  * Built-in type patterns: `case int(n):` -> `if isinstance(value, int): n = value`
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

## Known limitations

* Outside annotations and `isinstance` / `issubclass`, a PEP-604 union such as
  `Alias = A | B` is left as-is unless every operand is certainly a type. Without
  knowing what `A` and `B` are, `|` may equally be a bitwise-or (e.g. enum flags)
  or a set union.
* A runtime union which is lowered becomes a `typing.Union`, which `isinstance`
  rejects before Python 3.10. So `MaybeInt = int | None` followed by
  `isinstance(x, MaybeInt)` still fails there; spell the union out in the
  `isinstance` call instead.
* String (forward-reference) annotations are not rewritten.
