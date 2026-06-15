"""Transform ``collections.abc`` imports/usages so they fall back to ``typing`` on Python < 3.9.

Many ABCs (``Mapping``, ``Callable``, ``Sequence``, ...) became subscriptable as
generics in Python 3.9. To allow source code to use the modern
``from collections.abc import Mapping`` form while still running on 3.7/3.8,
this transformation rewrites such imports to a ``sys.version_info``-gated
fallback to the equivalents in :mod:`typing`. The only differently-named pair
is ``collections.abc.Set`` -> ``typing.AbstractSet``.

A thin configuration wrapper over :mod:`retrofy._transformations._backport_engine`.
"""

from __future__ import annotations

import libcst as cst

from ._backport_engine import (
    BackportConfig,
    BackportFeature,
    transform,
    transform_module,
)

_PY39 = (3, 9)

# Names that became subscriptable in collections.abc in Python 3.9 and that
# have an equivalent in typing. Only Set/AbstractSet differs by name.
_NAMES_WITH_FALLBACK: tuple[tuple[str, str | None], ...] = (
    ("Set", "AbstractSet"),
    ("AsyncGenerator", None),
    ("AsyncIterable", None),
    ("AsyncIterator", None),
    ("Awaitable", None),
    ("Callable", None),
    ("Collection", None),
    ("Container", None),
    ("Coroutine", None),
    ("Generator", None),
    ("Hashable", None),
    ("ItemsView", None),
    ("Iterable", None),
    ("Iterator", None),
    ("KeysView", None),
    ("Mapping", None),
    ("MappingView", None),
    ("MutableMapping", None),
    ("MutableSequence", None),
    ("MutableSet", None),
    ("Reversible", None),
    ("Sequence", None),
    ("Sized", None),
    ("ValuesView", None),
)

COLLECTIONS_ABC_CONFIG = BackportConfig(
    source_module="collections.abc",
    fallback_module="typing",
    features=tuple(
        BackportFeature(name, _PY39, fallback_name=fb)
        for name, fb in _NAMES_WITH_FALLBACK
    ),
)


def transform_collections_abc(source_code: str) -> str:
    return transform(source_code, COLLECTIONS_ABC_CONFIG)


def convert(module: cst.Module) -> cst.Module:
    return transform_module(module, COLLECTIONS_ABC_CONFIG)
