"""Host-side helpers for talking to ``_editable_converter_server``.

Used by the editable meta-hook on Python 3.7/3.8 hosts (where libcst
won't install) to drive an out-of-process converter running on a
modern Python the user has nominated.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import List, Optional
import warnings

ENV_VAR = "RETROFY_CONVERTER_PYTHON"

_DEFAULT_VENV_PYTHON = Path("~/.cache/retrofy/converter-venv/bin/python").expanduser()

_RECIPE_HINT = (
    "Create the default converter venv with:\n"
    "    python3 -m venv ~/.cache/retrofy/converter-venv\n"
    "    ~/.cache/retrofy/converter-venv/bin/pip install retrofy\n"
    f"or set {ENV_VAR} to a command that runs a Python with retrofy installed."
)


class ConverterPythonNotFound(RuntimeError):
    """Raised when no usable converter Python interpreter can be resolved."""


_uv_warning_emitted = False


def _warn_uv_fallback() -> None:
    global _uv_warning_emitted
    if _uv_warning_emitted:
        return
    _uv_warning_emitted = True
    warnings.warn(
        "retrofy: no converter venv at "
        f"{_DEFAULT_VENV_PYTHON}; falling back to `uv run --with retrofy python`. "
        f"For a faster startup, prepare a persistent venv. {_RECIPE_HINT}",
        stacklevel=2,
    )


def resolve_converter_python() -> List[str]:
    """Return the argv prefix that launches a Python interpreter with
    retrofy importable.

    Resolution order:
      1. ``$RETROFY_CONVERTER_PYTHON`` — shell-split as the command.
      2. ``~/.cache/retrofy/converter-venv/bin/python`` if it exists.
      3. ``uv run --with retrofy python`` if ``uv`` is on PATH (warns once).

    Raises :class:`ConverterPythonNotFound` if none of the above apply.
    The caller appends ``["-m", "retrofy._editable_converter_server"]``
    (or any other module) to invoke retrofy.
    """
    env_value = os.environ.get(ENV_VAR)
    if env_value:
        tokens = shlex.split(env_value)
        if not tokens:
            raise ConverterPythonNotFound(
                f"{ENV_VAR} is set but empty after shell-splitting.",
            )
        return tokens

    if _DEFAULT_VENV_PYTHON.is_file():
        return [str(_DEFAULT_VENV_PYTHON)]

    uv_path = shutil.which("uv")
    if uv_path is not None:
        _warn_uv_fallback()
        return [uv_path, "run", "--with", "retrofy", "python"]

    raise ConverterPythonNotFound(
        "Cannot find a Python interpreter with retrofy installed for "
        f"out-of-process conversion.\n{_RECIPE_HINT}",
    )


class ConverterWorker:
    """Long-lived subprocess that converts source files on demand.

    Spawned lazily on first :meth:`convert` call. If the worker dies,
    subsequent calls raise — we do not respawn.
    """

    _SERVER_MODULE = "retrofy._editable_converter_server"

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None

    def _spawn(self) -> None:
        argv = resolve_converter_python() + ["-m", self._SERVER_MODULE]
        self._proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            bufsize=1,
            text=True,
        )
        assert self._proc.stdout is not None
        ready = self._proc.stdout.readline()
        if not ready.startswith("READY "):
            raise RuntimeError(
                f"retrofy converter worker did not signal READY: {ready!r}",
            )

    def convert(self, src_path: str) -> str:
        if self._proc is None:
            self._spawn()
        assert self._proc is not None
        if self._proc.poll() is not None:
            rc = self._proc.returncode
            raise RuntimeError(f"retrofy converter worker has exited, returncode={rc}")
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._proc.stdin.write(f"{src_path}\n")
        self._proc.stdin.flush()
        response = self._proc.stdout.readline()
        if not response:
            raise RuntimeError("retrofy converter worker closed its output stream")

        kind, _, payload = response.rstrip("\n").partition(" ")
        if kind == "OK":
            return Path(payload).read_text(encoding="utf-8")
        if kind == "ERR":
            data = json.loads(payload)
            if data.get("type") == "SyntaxError":
                err = SyntaxError(data.get("msg") or "conversion failed")
                err.filename = src_path
                err.lineno = data.get("lineno")
                err.offset = data.get("offset")
                raise err
            raise RuntimeError(
                f"retrofy converter error ({data.get('type')}): {data.get('msg')}",
            )
        raise RuntimeError(f"retrofy converter protocol error: {response!r}")


_worker_singleton: Optional[ConverterWorker] = None


def get_worker() -> ConverterWorker:
    global _worker_singleton
    if _worker_singleton is None:
        _worker_singleton = ConverterWorker()
    return _worker_singleton
