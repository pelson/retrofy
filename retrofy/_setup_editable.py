"""``retrofy setup-editable``: write the import hook for a project
into a target Python that cannot run retrofy itself.

This does NOT install the project or its dependencies. It only
writes a ``.pth`` and a bundled host-side bootstrap into the target's
site-packages so retrofy's on-the-fly converter intercepts imports of
the project's top-level packages. Install the project's runtime
dependencies separately with the target Python's own pip.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Sequence
import venv

try:
    import tomllib
except ImportError:  # 3.9 / 3.10
    import tomli as tomllib  # type: ignore[no-redef]


_BOOTSTRAP_MODULES = (
    "_meta_hook_converter.py",
    "_editable_converter_client.py",
)
_EMBEDDED_RUNTIME_DIR = "_embedded_runtime"

BOOTSTRAP_NAME = "_retrofy_editable_bootstrap"
DEFAULT_CONVERTER_VENV = Path("~/.cache/retrofy/converter-venv").expanduser()


def _venv_python(venv_path: Path) -> Path:
    py = venv_path / "bin" / "python"
    return py if py.exists() else venv_path / "Scripts" / "python.exe"


def _target_purelib(target_python: Path) -> Path:
    out = subprocess.check_output(
        [
            str(target_python),
            "-c",
            "import sysconfig; print(sysconfig.get_path('purelib'))",
        ],
        text=True,
    ).strip()
    return Path(out)


def _provision_converter_venv(venv_path: Path) -> None:
    if _venv_python(venv_path).exists():
        return
    print(f"Provisioning converter venv at {venv_path}", file=sys.stderr)
    venv_path.parent.mkdir(parents=True, exist_ok=True)
    venv.create(str(venv_path), with_pip=True)
    py = _venv_python(venv_path)
    subprocess.check_call([str(py), "-m", "pip", "install", "--upgrade", "pip"])
    subprocess.check_call([str(py), "-m", "pip", "install", "retrofy"])


_PREPARE_METADATA_SCRIPT = """\
import importlib, sys
backend_spec = sys.argv[1]
out_dir = sys.argv[2]
mod_name, _, attr = backend_spec.partition(":")
backend = importlib.import_module(mod_name)
if attr:
    backend = getattr(backend, attr)
print(backend.prepare_metadata_for_build_wheel(out_dir))
"""


def _packages_from_backend(project_dir: Path) -> list[str]:
    """Ask the project's PEP 517 backend for its top-level packages.

    Reads ``[build-system].build-backend`` from the project's
    pyproject.toml, then calls ``prepare_metadata_for_build_wheel`` in
    a subprocess with ``cwd`` at the project root. Backends commonly
    capture ``Path.cwd()`` at import time (multistage_build does), so
    the subprocess is the cleanest way to give them the correct root.

    The subprocess uses the running interpreter, which is by definition
    the converter venv's Python — the one that has the backend
    importable.
    """
    cfg = tomllib.loads((project_dir / "pyproject.toml").read_text(encoding="utf-8"))
    backend_spec = cfg["build-system"]["build-backend"]

    with tempfile.TemporaryDirectory() as tmp:
        # Backends (setuptools especially) chatter on stdout; the
        # dist-info name is the last line our wrapper emits.
        stdout = subprocess.check_output(
            [sys.executable, "-c", _PREPARE_METADATA_SCRIPT, backend_spec, tmp],
            cwd=str(project_dir),
            text=True,
        )
        dist_info_name = stdout.splitlines()[-1].strip()
        top_level = Path(tmp) / dist_info_name / "top_level.txt"
        if not top_level.exists():
            raise RuntimeError(
                f"build backend {backend_spec!r} did not produce top_level.txt "
                f"under {dist_info_name}; cannot determine packages",
            )
        return [p for p in top_level.read_text(encoding="utf-8").split() if p]


def _copy_bootstrap(dst_pkg: Path) -> None:
    src_root = Path(__file__).parent
    if dst_pkg.exists():
        shutil.rmtree(dst_pkg)
    dst_pkg.mkdir(parents=True)
    # The bundled host always routes through the worker subprocess —
    # by definition retrofy is not installed on the target, regardless
    # of its Python version. Force the worker path on.
    (dst_pkg / "__init__.py").write_text(
        "from . import _meta_hook_converter as _mh\n"
        "_mh._HOST_NEEDS_WORKER = True\n"
        "from ._meta_hook_converter import register_hook  # noqa: F401,E402\n",
        encoding="utf-8",
    )
    for module in _BOOTSTRAP_MODULES:
        shutil.copy2(src_root / module, dst_pkg / module)
    shutil.copytree(
        src_root / _EMBEDDED_RUNTIME_DIR,
        dst_pkg / _EMBEDDED_RUNTIME_DIR,
    )


def _write_pth(
    target_purelib: Path,
    project_dir: Path,
    packages: Sequence[str],
) -> Path:
    project_root = project_dir.resolve()
    pth_path = target_purelib / f"_retrofy_editable_{project_root.name}.pth"
    pth_path.write_text(
        (
            "import sys; "
            f"sys.path.insert(0, {str(project_root)!r}); "
            f"import {BOOTSTRAP_NAME}; "
            f"{BOOTSTRAP_NAME}.register_hook({list(packages)!r})\n"
        ),
        encoding="utf-8",
    )
    return pth_path


def setup_editable(
    project_dir: Path,
    target_python: Path,
    converter_venv: Path = DEFAULT_CONVERTER_VENV,
    create_converter_venv: bool = True,
) -> int:
    if not (project_dir / "pyproject.toml").exists():
        print(f"no pyproject.toml in {project_dir}", file=sys.stderr)
        return 2
    if not target_python.exists():
        print(f"target-python not found: {target_python}", file=sys.stderr)
        return 2

    if create_converter_venv:
        _provision_converter_venv(converter_venv)
    elif not _venv_python(converter_venv).exists():
        print(
            f"converter venv missing at {converter_venv} (--no-create-converter-env set)",
            file=sys.stderr,
        )
        return 2

    packages = _packages_from_backend(project_dir)
    if not packages:
        print(
            "build backend reported no top-level packages — nothing to register",
            file=sys.stderr,
        )
        return 2

    target_purelib = _target_purelib(target_python)
    target_purelib.mkdir(parents=True, exist_ok=True)

    _copy_bootstrap(target_purelib / BOOTSTRAP_NAME)
    pth = _write_pth(target_purelib, project_dir, packages)

    print(f"wrote {pth}", file=sys.stderr)
    print(f"packages: {', '.join(packages)}", file=sys.stderr)
    print(
        "note: project dependencies are NOT installed by this command — "
        "use the target Python's pip for those.",
        file=sys.stderr,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="retrofy setup-editable",
        description=(
            "Write retrofy's editable import hook into a target Python's "
            "site-packages. Does not install the project or its dependencies."
        ),
    )
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--target-python", type=Path, required=True)
    parser.add_argument(
        "--converter-venv",
        type=Path,
        default=DEFAULT_CONVERTER_VENV,
    )
    parser.add_argument(
        "--no-create-converter-env",
        dest="create_converter_venv",
        action="store_false",
    )
    args = parser.parse_args(argv)
    return setup_editable(
        project_dir=args.project_dir,
        target_python=args.target_python,
        converter_venv=args.converter_venv,
        create_converter_venv=args.create_converter_venv,
    )


if __name__ == "__main__":
    raise SystemExit(main())
