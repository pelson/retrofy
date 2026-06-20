"""Tests for ``retrofy setup-editable``.

Drives the command end-to-end against a tiny fixture project, using
the test's own interpreter as both the converter Python (it has
retrofy installed) and — via a freshly-created venv — the target.
This exercises the same mechanics CI uses on 3.7/3.8.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap
import venv

import pytest

from retrofy._setup_editable import BOOTSTRAP_NAME


def _make_project(root: Path) -> Path:
    project = root / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        textwrap.dedent(
            """
            [build-system]
            requires = ["setuptools"]
            build-backend = "setuptools.build_meta"

            [project]
            name = "retrofy_setup_editable_fixture"
            version = "0.0"
            """,
        ),
        encoding="utf-8",
    )
    pkg = project / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        # Uses post-3.8 PEP 604 syntax to prove the worker conversion runs.
        "VALUE: int | None = 42\n",
        encoding="utf-8",
    )
    return project


def _make_target_venv(root: Path) -> Path:
    target = root / "target-venv"
    venv.create(target, with_pip=False)
    return target / "bin" / "python"


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


def _run_setup_editable(*extra_args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "retrofy", "setup-editable", *extra_args],
        capture_output=True,
        text=True,
    )


@pytest.fixture
def project_and_target(tmp_path: Path):
    project = _make_project(tmp_path)
    target_python = _make_target_venv(tmp_path)
    return project, target_python


def test_setup_editable_writes_pth_and_bootstrap(project_and_target):
    project, target_python = project_and_target
    result = _run_setup_editable(
        str(project),
        "--target-python",
        str(target_python),
        "--converter-venv",
        sys.prefix,
        "--no-create-converter-env",
    )
    assert result.returncode == 0, result.stderr

    purelib = _target_purelib(target_python)
    assert (purelib / BOOTSTRAP_NAME / "__init__.py").exists()
    assert (purelib / BOOTSTRAP_NAME / "_meta_hook_converter.py").exists()
    assert (purelib / BOOTSTRAP_NAME / "_editable_converter_client.py").exists()
    assert (purelib / BOOTSTRAP_NAME / "_retrofy_rt" / "lazy_imports.py").exists()

    pth_files = list(purelib.glob("_retrofy_editable_*.pth"))
    assert len(pth_files) == 1
    content = pth_files[0].read_text(encoding="utf-8")
    assert str(project.resolve()) in content
    assert "register_hook(['mypkg'])" in content


def test_setup_editable_target_can_import_through_worker(project_and_target):
    project, target_python = project_and_target
    result = _run_setup_editable(
        str(project),
        "--target-python",
        str(target_python),
        "--converter-venv",
        sys.prefix,
        "--no-create-converter-env",
    )
    assert result.returncode == 0, result.stderr

    env = {
        **os.environ,
        "RETROFY_CONVERTER_PYTHON": sys.executable,
    }
    out = subprocess.run(
        [str(target_python), "-c", "import mypkg; print(mypkg.VALUE, mypkg.__file__)"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    value, _, file_path = out.stdout.strip().partition(" ")
    assert value == "42"
    # ``__file__`` should point at the *original* source so user
    # tracebacks aren't littered with worker tmp paths.
    assert Path(file_path) == project / "mypkg" / "__init__.py"


def test_setup_editable_no_create_errors_when_missing(tmp_path, project_and_target):
    project, target_python = project_and_target
    missing_venv = tmp_path / "does-not-exist"
    result = _run_setup_editable(
        str(project),
        "--target-python",
        str(target_python),
        "--converter-venv",
        str(missing_venv),
        "--no-create-converter-env",
    )
    assert result.returncode != 0
    assert "converter venv missing" in result.stderr


def test_setup_editable_rejects_project_without_pyproject(tmp_path, project_and_target):
    _, target_python = project_and_target
    empty = tmp_path / "no-pyproject"
    empty.mkdir()
    result = _run_setup_editable(
        str(empty),
        "--target-python",
        str(target_python),
        "--converter-venv",
        sys.prefix,
        "--no-create-converter-env",
    )
    assert result.returncode != 0
    assert "no pyproject.toml" in result.stderr
