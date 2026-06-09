"""Tests for retrofy's meta-path finder, with a focus on the
synthesised ``_retrofy`` runtime payload that converted code imports
from (``from ._retrofy.lazy_runtime import ...``).

The on-the-fly converter rewrites modules at import time, so there is
no ``_retrofy/`` directory on disk to import from in editable /
pytest contexts — :class:`MyMetaPathFinder` serves the payload sources
straight from retrofy's own install.
"""

from __future__ import annotations

import importlib
import sys
import textwrap

from retrofy._meta_hook_converter import (
    MyMetaPathFinder,
    _payload_source,
    _PayloadLoader,
)


def test_payload_source_returns_init_for_empty_name():
    payload = _payload_source("")
    assert payload is not None
    source, filename = payload
    assert isinstance(source, bytes)
    assert "<retrofy-payload:__init__.py>" == filename


def test_payload_source_returns_module():
    payload = _payload_source("lazy_runtime")
    assert payload is not None
    source, _ = payload
    assert b"class LazyProxy" in source


def test_payload_source_unknown_returns_none():
    assert _payload_source("does_not_exist") is None


def test_payload_spec_for_registered_package_returns_package_spec():
    finder = MyMetaPathFinder(["fakepkg"])
    spec = finder._payload_spec("fakepkg._retrofy")
    assert spec is not None
    assert spec.submodule_search_locations == []
    assert isinstance(spec.loader, _PayloadLoader)


def test_payload_spec_for_registered_package_returns_module_spec():
    finder = MyMetaPathFinder(["fakepkg"])
    spec = finder._payload_spec("fakepkg._retrofy.lazy_runtime")
    assert spec is not None
    assert spec.submodule_search_locations is None
    assert isinstance(spec.loader, _PayloadLoader)


def test_payload_spec_for_nested_subpackage():
    finder = MyMetaPathFinder(["fakepkg"])
    spec = finder._payload_spec("fakepkg.sub.deeper._retrofy.lazy_runtime")
    assert spec is not None
    assert isinstance(spec.loader, _PayloadLoader)


def test_payload_spec_returns_none_for_unregistered_prefix():
    finder = MyMetaPathFinder(["fakepkg"])
    assert finder._payload_spec("other._retrofy") is None
    assert finder._payload_spec("other._retrofy.lazy_runtime") is None


def test_payload_spec_returns_none_for_unknown_submodule():
    finder = MyMetaPathFinder(["fakepkg"])
    assert finder._payload_spec("fakepkg._retrofy.does_not_exist") is None


def test_payload_spec_returns_none_for_nested_under_retrofy():
    # Payload tree is flat; ``_retrofy.lazy_runtime.x`` shouldn't
    # resolve.
    finder = MyMetaPathFinder(["fakepkg"])
    assert (
        finder._payload_spec(
            "fakepkg._retrofy.lazy_runtime.something",
        )
        is None
    )


def test_end_to_end_lazy_import_via_meta_hook(tmp_path, monkeypatch):
    """Set up a synthetic on-disk package using ``lazy import``,
    register the hook, and verify the module imports and executes.

    The payload is not on disk; the meta-path finder must synthesise
    ``synthpkg._retrofy.lazy_runtime`` for the converted import to
    resolve.
    """
    pkg_root = tmp_path / "synthpkg"
    pkg_root.mkdir()
    (pkg_root / "__init__.py").write_text("")
    (pkg_root / "lazyuser.py").write_text(
        textwrap.dedent(
            """
            lazy import json

            def loads(s):
                return json.loads(s)
            """,
        ).lstrip(),
    )

    monkeypatch.syspath_prepend(str(tmp_path))
    finder = MyMetaPathFinder(["synthpkg"])
    monkeypatch.setattr(sys, "meta_path", [finder, *sys.meta_path])
    # Make sure no cached version of the test packages is in play.
    for name in list(sys.modules):
        if name == "synthpkg" or name.startswith("synthpkg."):
            del sys.modules[name]

    mod = importlib.import_module("synthpkg.lazyuser")
    assert mod.loads('{"a": 1}') == {"a": 1}

    # The synthesised runtime package is importable too.
    rt = importlib.import_module("synthpkg._retrofy.lazy_runtime")
    assert hasattr(rt, "LazyProxy")
