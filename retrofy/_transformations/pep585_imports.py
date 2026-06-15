"""PEP 585 stdlib-module import backports (collections, contextlib, re).

PEP 585 made several stdlib classes runtime-subscriptable in Python 3.9 so
that ``collections.deque[int]``, ``re.Pattern[str]``, etc. work without
``typing.Deque``/``typing.Pattern``. This transformation rewrites those
imports so source written for 3.9+ still runs on 3.7/3.8 by falling back to
the equivalents in :mod:`typing`.

For the :mod:`collections.abc` part of PEP 585 see
:mod:`retrofy._transformations.collections_abc`.

Thin configuration wrappers over :mod:`retrofy._transformations._backport_engine`.
"""

from __future__ import annotations

import libcst as cst

from ._backport_engine import BackportConfig, BackportFeature, transform_module

_PY39 = (3, 9)


COLLECTIONS_CONFIG = BackportConfig(
    source_module="collections",
    fallback_module="typing",
    features=tuple(
        BackportFeature(name, _PY39, fallback_name=fb)
        for name, fb in (
            ("deque", "Deque"),
            ("defaultdict", "DefaultDict"),
            ("OrderedDict", None),
            ("Counter", None),
            ("ChainMap", None),
        )
    ),
)


CONTEXTLIB_CONFIG = BackportConfig(
    source_module="contextlib",
    fallback_module="typing",
    features=(
        BackportFeature(
            "AbstractContextManager",
            _PY39,
            fallback_name="ContextManager",
        ),
        BackportFeature(
            "AbstractAsyncContextManager",
            _PY39,
            fallback_name="AsyncContextManager",
        ),
    ),
)


RE_CONFIG = BackportConfig(
    source_module="re",
    fallback_module="typing",
    features=(
        BackportFeature("Pattern", _PY39),
        BackportFeature("Match", _PY39),
    ),
)


_ALL_CONFIGS = (COLLECTIONS_CONFIG, CONTEXTLIB_CONFIG, RE_CONFIG)


def convert(module: cst.Module) -> cst.Module:
    for config in _ALL_CONFIGS:
        module = transform_module(module, config)
    return module
