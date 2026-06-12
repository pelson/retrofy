"""Pytest plugin: cooperate with pytest's assertion rewriter when test
files use post-current-Python syntax that retrofy backports.

Without this plugin pytest's ``AssertionRewritingHook`` calls
``ast.parse`` on the raw source. Test files containing future syntax
that retrofy backports fail to parse before pytest even gets to the
asserts, and even where parsing succeeds retrofy's editable-install
meta-path loader claims the import first, bypassing pytest's rewriter
entirely (so ``assert x == y`` shows no introspection diff).

The plugin does two things during ``pytest_configure`` /
``pytest_sessionstart``:

1. Monkey-patch ``_pytest.assertion.rewrite._rewrite_test`` so the
   source bytes are piped through :func:`retrofy._converters.convert`
   before ``ast.parse``. Pytest's assertion rewriter then runs on the
   *converted* AST, producing bytecode whose asserts have full
   introspection over the transformed expressions.

2. Reorder ``sys.meta_path`` so pytest's ``AssertionRewritingHook``
   sits in front of retrofy's :class:`MyMetaPathFinder`. Without this,
   the retrofy hook (registered by the ``.pth`` script for editable
   installs, or by a project conftest) claims test modules first and
   replaces the loader, so pytest never sees them.

The converted source is also injected into ``linecache.cache`` (with
``mtime=None`` so ``checkcache`` won't evict it). Pytest's source
display, traceback machinery, and ``inspect.findsource`` all read from
``linecache``, so they see lines whose numbering matches the compiled
bytecode. For unchanged user code (asserts, function bodies) the
displayed text is identical to what's on disk; only lines near a
rewriter's injection point differ from the user's authored source.
"""

from __future__ import annotations

import ast
import linecache
import os
from pathlib import Path
import sys

from ._converters import convert


def _stash_converted_source(filename: str, converted: str) -> None:
    """Inject converted source into ``linecache.cache`` so pytest's
    source-line lookup (``inspect.findsource`` → ``linecache.getlines``)
    returns lines whose numbering matches the compiled bytecode.

    Without this, a failing assert at converted line N (originating
    from user line N - shift) shows ``>   ???`` in the traceback because
    pytest indexes the on-disk file by the shifted bytecode lineno and
    falls off the end.

    Setting ``mtime`` to ``None`` makes ``linecache.checkcache`` skip
    this entry rather than evicting it when the on-disk mtime/size
    don't match.
    """
    lines = converted.splitlines(keepends=True)
    linecache.cache[filename] = (len(converted), None, lines, filename)


def _read_and_convert(fn) -> str | None:
    """Return the retrofy-converted source for *fn*, or ``None`` if the
    file can't be read, decoded, converted, or is unchanged by convert.
    """
    try:
        source_bytes = Path(fn).read_bytes()
    except OSError:
        return None
    try:
        source_text = source_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None
    try:
        converted = convert(source_text)
    except Exception:
        return None
    if converted == source_text:
        return None
    return converted


def pytest_configure(config):
    from _pytest.assertion import rewrite as _r

    if getattr(_r._rewrite_test, "_retrofy_patched", False):
        return

    orig_rewrite_test = _r._rewrite_test

    def _rewrite_test(fn, cfg):
        converted = _read_and_convert(fn)
        if converted is None:
            return orig_rewrite_test(fn, cfg)
        stat = os.stat(fn)
        strfn = str(fn)
        tree = ast.parse(converted, filename=strfn)
        _r.rewrite_asserts(tree, converted.encode("utf-8"), strfn, cfg)
        co = compile(tree, strfn, "exec", dont_inherit=True)
        _stash_converted_source(strfn, converted)
        return stat, co

    _rewrite_test._retrofy_patched = True
    _r._rewrite_test = _rewrite_test

    # The pyc-cached path in ``AssertionRewritingHook.exec_module``
    # skips ``_rewrite_test`` entirely, so the linecache stash above
    # wouldn't run. Wrap ``exec_module`` to pre-stash the converted
    # source on every load.
    orig_exec_module = _r.AssertionRewritingHook.exec_module

    def exec_module(self, module):
        spec = module.__spec__
        if spec is not None and spec.origin:
            converted = _read_and_convert(spec.origin)
            if converted is not None:
                _stash_converted_source(spec.origin, converted)
        return orig_exec_module(self, module)

    exec_module._retrofy_patched = True
    _r.AssertionRewritingHook.exec_module = exec_module


def pytest_sessionstart(session):
    from _pytest.assertion.rewrite import AssertionRewritingHook

    from ._meta_hook_converter import MyMetaPathFinder, register_runtime_synthesiser

    # Converted test source emits ``from ._retrofy.lazy_runtime import
    # ...``; tests don't live under a registered retrofy hook prefix,
    # so install a permissive synthesiser at the end of the meta path
    # to serve the payload for any user package.
    register_runtime_synthesiser()

    rewriter_idx = None
    retrofy_idx = None
    for i, finder in enumerate(sys.meta_path):
        if rewriter_idx is None and isinstance(finder, AssertionRewritingHook):
            rewriter_idx = i
        elif retrofy_idx is None and isinstance(finder, MyMetaPathFinder):
            retrofy_idx = i
    if rewriter_idx is None or retrofy_idx is None:
        return
    if retrofy_idx < rewriter_idx:
        retrofy_hook = sys.meta_path.pop(retrofy_idx)
        sys.meta_path.insert(rewriter_idx, retrofy_hook)
