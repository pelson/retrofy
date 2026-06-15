"""Tests for the out-of-process editable converter path.

The subprocess flow exists for Python 3.7/3.8 hosts where libcst
won't install, but the *machinery* must work on every Python that
runs retrofy itself (we use the running interpreter as the converter
in these tests). These tests force the worker path on whichever
interpreter pytest is running under, by pointing the host-side
client at ``sys.executable``.
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import textwrap
from typing import Iterator
import warnings

import pytest

from retrofy import _editable_converter_client as client
from retrofy import _meta_hook_converter as meta_hook
from retrofy._editable_converter_client import (
    ENV_VAR,
    ConverterPythonNotFound,
    ConverterWorker,
    resolve_converter_python,
)

# ---------------------------------------------------------------------------
# Server subprocess: end-to-end via stdin/stdout protocol
# ---------------------------------------------------------------------------


def _converter_argv() -> list[str]:
    """Argv prefix for spawning a converter Python.

    Honours ``$RETROFY_CONVERTER_PYTHON`` so the same tests can be driven
    against a separate interpreter on hosts (3.7/3.8) where the running
    interpreter cannot itself install libcst. Falls back to
    ``sys.executable`` for the common 3.9+ case.
    """
    env = os.environ.get(ENV_VAR)
    if env:
        return shlex.split(env)
    return [sys.executable]


@pytest.fixture
def server() -> Iterator[subprocess.Popen]:
    """A fresh converter-server subprocess, pre-synchronised on its READY
    signal and cleanly QUIT on teardown.
    """
    proc = subprocess.Popen(
        [*_converter_argv(), "-m", "retrofy._editable_converter_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert proc.stdin is not None and proc.stdout is not None
    ready = proc.stdout.readline()
    assert ready.startswith("READY "), f"server failed to start: {ready!r}"
    try:
        yield proc
    finally:
        if proc.poll() is None:
            proc.stdin.write("QUIT\n")
            proc.stdin.flush()
            proc.wait(timeout=5)


def _send(server: subprocess.Popen, line: str) -> str:
    assert server.stdin is not None and server.stdout is not None
    server.stdin.write(f"{line}\n")
    server.stdin.flush()
    return server.stdout.readline().rstrip("\n")


def test_server_converts_a_file(server: subprocess.Popen, tmp_path: Path):
    src = tmp_path / "sample.py"
    src.write_text("x: int | None = None\n", encoding="utf-8")

    response = _send(server, str(src))
    assert response.startswith("OK ")
    out_path = Path(response[len("OK ") :])
    # Read while the server is still alive — its tmp dir is removed on exit.
    converted = out_path.read_text(encoding="utf-8")

    # PEP 604 ``X | None`` is lowered via ``typing.Union[..., None]``.
    assert "Union" in converted or "Optional" in converted


def test_server_reports_syntax_error_as_err(server: subprocess.Popen, tmp_path: Path):
    src = tmp_path / "broken.py"
    src.write_text("def f(:\n", encoding="utf-8")

    response = _send(server, str(src))
    assert response.startswith("ERR ")
    payload = json.loads(response[len("ERR ") :])
    # The server normalises tokenize.TokenError and libcst.ParserSyntaxError
    # into SyntaxError before encoding, so callers see a single, positioned
    # error type.
    assert payload["type"] == "SyntaxError"
    assert payload.get("lineno") is not None


# ---------------------------------------------------------------------------
# ConverterWorker: host-side client driving the running interpreter
# ---------------------------------------------------------------------------


@pytest.fixture
def worker_against_self(monkeypatch) -> Iterator[ConverterWorker]:
    """A worker whose subprocess uses the test-configured converter Python.

    Honours ``$RETROFY_CONVERTER_PYTHON`` (so CI can point at a 3.13
    venv on 3.7/3.8 hosts); otherwise falls back to ``sys.executable``.
    """
    argv = _converter_argv()
    monkeypatch.setattr(client, "resolve_converter_python", lambda: argv)
    # Ensure no leftover singleton bleeds across tests.
    monkeypatch.setattr(client, "_worker_singleton", None)
    worker = ConverterWorker()
    yield worker
    proc = worker._proc
    if proc is not None and proc.poll() is None:
        assert proc.stdin is not None
        proc.stdin.write("QUIT\n")
        proc.stdin.flush()
        proc.wait(timeout=5)


def test_worker_converts_a_file(worker_against_self, tmp_path: Path):
    src = tmp_path / "mod.py"
    src.write_text("y: list[int] | None = None\n", encoding="utf-8")

    converted = worker_against_self.convert(str(src))
    # PEP 604 lowering routes through ``typing.Union[..., None]``.
    assert "Union" in converted


def test_worker_raises_syntax_error_with_filename(worker_against_self, tmp_path: Path):
    src = tmp_path / "broken.py"
    src.write_text("def f(:\n", encoding="utf-8")

    with pytest.raises(SyntaxError) as exc:
        worker_against_self.convert(str(src))
    assert exc.value.filename == str(src)


# ---------------------------------------------------------------------------
# resolve_converter_python: env var, fallbacks, error
# ---------------------------------------------------------------------------


def test_resolve_uses_env_var(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "/some/python -X dev")
    assert resolve_converter_python() == ["/some/python", "-X", "dev"]


def test_resolve_empty_env_var_is_error(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "   ")
    with pytest.raises(ConverterPythonNotFound):
        resolve_converter_python()


def test_resolve_raises_when_nothing_available(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.setattr(client, "_DEFAULT_VENV_PYTHON", Path("/nonexistent/python"))
    monkeypatch.setattr(client.shutil, "which", lambda name: None)
    with pytest.raises(ConverterPythonNotFound) as exc:
        resolve_converter_python()
    assert "converter venv" in str(
        exc.value,
    ).lower() or "RETROFY_CONVERTER_PYTHON" in str(exc.value)


def test_resolve_uv_fallback_warns_once(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.setattr(client, "_DEFAULT_VENV_PYTHON", Path("/nonexistent/python"))
    monkeypatch.setattr(
        client.shutil,
        "which",
        lambda name: "/fake/uv" if name == "uv" else None,
    )
    monkeypatch.setattr(client, "_uv_warning_emitted", False)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert resolve_converter_python() == [
            "/fake/uv",
            "run",
            "--with",
            "retrofy",
            "python",
        ]
        resolve_converter_python()  # second call; should not re-warn

    uv_warnings = [w for w in caught if "uv run" in str(w.message)]
    assert len(uv_warnings) == 1


# ---------------------------------------------------------------------------
# End-to-end: meta-hook routed through the worker subprocess
# ---------------------------------------------------------------------------


def test_meta_hook_uses_worker_when_forced(monkeypatch, tmp_path: Path):
    """Force the worker path and import a real module through it.

    Covers the user's requirement that the editable subprocess mode
    works on every Python retrofy supports — we drive it here on the
    running interpreter rather than waiting for a 3.7/3.8 host.
    """
    pkg_dir = tmp_path / "fakepkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "uses_pep604.py").write_text(
        textwrap.dedent(
            """
            from typing import Optional  # noqa: F401  (proves we re-import after lowering)

            VALUE: int | None = 7
            """,
        ),
        encoding="utf-8",
    )

    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(meta_hook, "_HOST_NEEDS_WORKER", True)
    argv = _converter_argv()
    monkeypatch.setattr(client, "resolve_converter_python", lambda: argv)
    monkeypatch.setattr(client, "_worker_singleton", None)

    meta_hook.register_hook(["fakepkg"])
    try:
        # Make sure no stale import survives.
        for name in [
            n for n in sys.modules if n == "fakepkg" or n.startswith("fakepkg.")
        ]:
            del sys.modules[name]

        mod = importlib.import_module("fakepkg.uses_pep604")
        assert mod.VALUE == 7
        # The on-the-fly loader should report the original path so
        # tracebacks point at the user's source, not the tmp scratch.
        assert mod.__file__ == str(pkg_dir / "uses_pep604.py")
    finally:
        # Drop the finder we installed so we don't leak into other tests.
        sys.meta_path[:] = [
            f for f in sys.meta_path if not isinstance(f, meta_hook.MyMetaPathFinder)
        ]
        for name in [
            n for n in sys.modules if n == "fakepkg" or n.startswith("fakepkg.")
        ]:
            del sys.modules[name]
        # Shut down any spawned worker.
        if (
            client._worker_singleton is not None
            and client._worker_singleton._proc is not None
        ):
            proc = client._worker_singleton._proc
            if proc.poll() is None:
                assert proc.stdin is not None
                proc.stdin.write("QUIT\n")
                proc.stdin.flush()
                proc.wait(timeout=5)
            client._worker_singleton = None
