"""Long-lived converter subprocess for out-of-process editable mode.

Invoked as ``python -m retrofy._editable_converter_server`` by the
editable meta-hook on Python 3.7/3.8 hosts, where retrofy itself
cannot run because libcst doesn't install. The worker runs on a
modern Python (the user-nominated converter interpreter) and serves
single-file conversion requests over stdin/stdout.

Protocol (line-based, UTF-8):

    Request:  <absolute source path>\\n   or   QUIT\\n
    Response: OK <converted-output-path>\\n
              ERR <json error payload>\\n

On startup the worker emits ``READY <out-dir>\\n`` so the host can
synchronise. The output directory is a per-worker tmpdir cleaned up
on exit.

This module has no public API; the host invokes it by module path
only.
"""

from __future__ import annotations

import atexit
import itertools
import json
from pathlib import Path
import shutil
import sys
import tempfile

from ._converters import convert


def _encode_error(exc: BaseException) -> str:
    payload: dict[str, object] = {"type": type(exc).__name__, "msg": str(exc)}
    if isinstance(exc, SyntaxError):
        payload["msg"] = exc.msg or str(exc)
        payload["lineno"] = exc.lineno
        payload["offset"] = exc.offset
    return json.dumps(payload)


def _convert_one_file(src_path: Path, out_dir: Path, seq: int) -> Path:
    source = src_path.read_text(encoding="utf-8")
    converted = convert(source)
    out_path = out_dir / f"{seq:08d}-{src_path.name}"
    out_path.write_text(converted, encoding="utf-8")
    return out_path


def serve(out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    counter = itertools.count()
    stdout = sys.stdout

    stdout.write(f"READY {out_dir}\n")
    stdout.flush()

    for raw in sys.stdin:
        line = raw.rstrip("\n")
        if not line:
            continue
        if line == "QUIT":
            return 0
        src_path = Path(line)
        try:
            out_path = _convert_one_file(src_path, out_dir, next(counter))
        except BaseException as exc:
            stdout.write(f"ERR {_encode_error(exc)}\n")
            stdout.flush()
            continue
        stdout.write(f"OK {out_path}\n")
        stdout.flush()
    return 0


def main() -> int:
    out_dir = Path(tempfile.mkdtemp(prefix="retrofy-worker-"))
    atexit.register(shutil.rmtree, out_dir, ignore_errors=True)
    return serve(out_dir)


if __name__ == "__main__":
    raise SystemExit(main())
