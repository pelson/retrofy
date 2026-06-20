"""Runtime support for retrofy's PEP 810 (lazy imports) backport.

This module is the canonical payload that retrofy drops into every
converted package as ``<pkg>/_retrofy/lazy_runtime.py``. Converted
modules emit ``from ._retrofy.lazy_runtime import (...)`` so wheel
installs never reach back to ``retrofy.*`` at runtime.

The rewriter in :mod:`retrofy._transformations.lazy_imports` turns
``lazy import`` and ``lazy from`` statements into calls to
:func:`lazy_import`, :func:`lazy_import_as`, and :func:`lazy_from`,
and wraps every read of a lazy-bound name with :func:`reify`
(aliased as ``__lazy_reify__`` in the converted module).

:func:`reify` matches PEP 810 runtime semantics:

* If the argument is not a :class:`LazyProxy`, it is returned unchanged —
  so a name rebound by a later eager ``import`` (or any other assignment)
  keeps working transparently.
* On first reification, the caller's global slot for the proxy's bind
  name is replaced with the real object, matching PEP 810's
  zero-overhead-after-first-use property.
"""

from __future__ import annotations

import importlib
import sys
import threading
from typing import Any, Callable, Optional

_MISSING = object()


class LazyProxy:
    __slots__ = ("_loader", "_lock", "_resolved", "_display_name", "_bind_name")

    def __init__(
        self,
        loader: Callable[[], Any],
        display_name: str,
        bind_name: str,
    ) -> None:
        object.__setattr__(self, "_loader", loader)
        object.__setattr__(self, "_lock", threading.Lock())
        object.__setattr__(self, "_resolved", _MISSING)
        object.__setattr__(self, "_display_name", display_name)
        object.__setattr__(self, "_bind_name", bind_name)

    def _reify(self) -> Any:
        resolved = object.__getattribute__(self, "_resolved")
        if resolved is not _MISSING:
            return resolved
        lock = object.__getattribute__(self, "_lock")
        with lock:
            resolved = object.__getattribute__(self, "_resolved")
            if resolved is not _MISSING:
                return resolved
            loader = object.__getattribute__(self, "_loader")
            resolved = loader()
            object.__setattr__(self, "_resolved", resolved)
            return resolved

    # Instance-level forwarding acts as a safety net for proxies that
    # escape the rewritten module. Inside the rewritten module every read
    # is wrapped with ``reify()`` so this path is normally unused.
    def __getattr__(self, name: str) -> Any:
        if name in LazyProxy.__slots__:
            raise AttributeError(name)
        return getattr(self._reify(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._reify(), name, value)

    def __delattr__(self, name: str) -> None:
        delattr(self._reify(), name)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._reify()(*args, **kwargs)

    def __repr__(self) -> str:
        resolved = object.__getattribute__(self, "_resolved")
        name = object.__getattribute__(self, "_display_name")
        if resolved is _MISSING:
            return f"<lazy {name!r} (unresolved)>"
        return repr(resolved)


def reify(obj: Any) -> Any:
    """The wrap-every-use helper. See module docstring."""
    if not isinstance(obj, LazyProxy):
        return obj
    resolved = obj._reify()
    bind_name = object.__getattribute__(obj, "_bind_name")
    try:
        frame = sys._getframe(1)
    except ValueError:
        return resolved
    globs = frame.f_globals
    if globs.get(bind_name) is obj:
        globs[bind_name] = resolved
    return resolved


def lazy_import(
    name: str,
    bind_name: str,
    *,
    package: Optional[str] = None,
) -> LazyProxy:
    """Backport of ``lazy import name`` (or ``lazy import a.b`` — which
    binds the top-level package ``a``)."""

    def _load() -> Any:
        importlib.import_module(name, package)
        return sys.modules[name.partition(".")[0]]

    return LazyProxy(_load, name, bind_name)


def lazy_import_as(
    name: str,
    bind_name: str,
    *,
    package: Optional[str] = None,
) -> LazyProxy:
    """Backport of ``lazy import foo.bar as alias`` — the alias binds to
    ``foo.bar`` rather than to ``foo``."""

    def _load() -> Any:
        return importlib.import_module(name, package)

    return LazyProxy(_load, name, bind_name)


def lazy_from(
    module: str,
    attr: str,
    bind_name: str,
    *,
    package: Optional[str] = None,
) -> LazyProxy:
    """Backport of ``lazy from module import attr``."""

    def _load() -> Any:
        mod = importlib.import_module(module, package)
        return getattr(mod, attr)

    return LazyProxy(_load, f"{module}.{attr}", bind_name)
