"""``retrofy wheel`` and ``retrofy files``: convert an existing wheel, or a
directory of Python files, into a copy that runs on older Pythons."""

from __future__ import annotations

import ast
import base64
import csv
import hashlib
import importlib
import io
import pathlib
import sys
import textwrap
import zipfile

import pytest

from retrofy.__main__ import main

# Uses PEP 810 lazy imports (needs the injected ``_retrofy_rt`` runtime) and
# a walrus, so both a syntax lowering and the runtime injection are covered.
MODULE_SOURCE = textwrap.dedent("""\
    lazy import json


    def first_positive(xs):
        if (y := next((x for x in xs if x > 0), None)) is not None:
            return y


    def dumps(value):
        return json.dumps(value)
    """)

METADATA = (
    "Metadata-Version: 2.1\n"
    "Name: demo\n"
    "Version: 1.0\n"
    "Requires-Python: >=3.15\n"
    "\n"
    "Long description.\n"
)


def _make_wheel(directory: pathlib.Path, tag: str = "py3-none-any") -> pathlib.Path:
    whl = directory / f"demo-1.0-{tag}.whl"
    files = {
        "demo/__init__.py": MODULE_SOURCE.encode(),
        "demo-1.0.dist-info/METADATA": METADATA.encode(),
        "demo-1.0.dist-info/WHEEL": (
            f"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: {tag}\n"
        ).encode(),
    }
    record = "".join(
        f"{name},{_record_hash(content)},{len(content)}\n"
        for name, content in files.items()
    )
    record += "demo-1.0.dist-info/RECORD,,\n"
    with zipfile.ZipFile(whl, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
        z.writestr("demo-1.0.dist-info/RECORD", record)
    return whl


def _record_hash(content: bytes) -> str:
    digest = hashlib.sha256(content).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def test_wheel_converts_a_copy(tmp_path):
    src = _make_wheel(tmp_path)
    original_bytes = src.read_bytes()
    out = tmp_path / "out"

    assert main(["wheel", str(src), "--target-python", "3.9", "-o", str(out)]) == 0

    # The input is untouched, and the copy keeps the name (and version).
    assert src.read_bytes() == original_bytes
    converted = out / src.name
    with zipfile.ZipFile(converted) as z:
        names = set(z.namelist())
        metadata = z.read("demo-1.0.dist-info/METADATA").decode()
        module = z.read("demo/__init__.py").decode()
        record = z.read("demo-1.0.dist-info/RECORD").decode()
        contents = {name: z.read(name) for name in names}

    header = metadata.partition("\n\n")[0].splitlines()
    assert "Version: 1.0" in header
    assert "Requires-Python: >=3.9" in header
    assert 'Requires-Dist: typing_extensions>=4.4; python_version < "3.12"' in header

    assert "lazy import" not in module
    assert not any(isinstance(n, ast.NamedExpr) for n in ast.walk(ast.parse(module)))
    assert "demo/_retrofy_rt/lazy_imports.py" in names

    # RECORD covers every file, with correct hashes.
    rows = {row[0]: row[1] for row in csv.reader(io.StringIO(record)) if row}
    for name, content in contents.items():
        if name.endswith("/RECORD"):
            continue
        assert rows[name] == _record_hash(content), name


def test_wheel_converted_code_runs(tmp_path, monkeypatch):
    src = _make_wheel(tmp_path)
    out = tmp_path / "out"
    main(["wheel", str(src), "--target-python", "3.9", "-o", str(out)])

    site = tmp_path / "site"
    with zipfile.ZipFile(out / src.name) as z:
        z.extractall(site)
    monkeypatch.syspath_prepend(str(site))
    monkeypatch.delitem(sys.modules, "demo", raising=False)
    demo = importlib.import_module("demo")
    assert demo.first_positive([-1, 0, 2, 3]) == 2
    assert demo.dumps({"a": 1}) == '{"a": 1}'


@pytest.mark.parametrize(
    ("args", "message"),
    [
        pytest.param(
            ["--target-python", "3.9", "-o", "{out}"],
            "pure-Python",
            id="not-pure-python",
        ),
        pytest.param(
            ["--target-python", ">=3.9", "-o", "{out}"],
            "target-python",
            id="target-python-not-a-version",
        ),
        pytest.param(
            ["--target-python", "3.9", "-o", "{src_dir}"],
            "overwrite",
            id="output-would-overwrite-input",
        ),
        pytest.param(["-o", "{out}"], "--target-python", id="target-python-missing"),
    ],
)
def test_wheel_errors(tmp_path, capsys, args, message):
    tag = "cp315-cp315-linux_x86_64" if message == "pure-Python" else "py3-none-any"
    src = _make_wheel(tmp_path, tag=tag)
    args = [a.format(out=tmp_path / "out", src_dir=tmp_path) for a in args]
    with pytest.raises(SystemExit) as excinfo:
        main(["wheel", str(src), *args])
    assert excinfo.value.code == 2
    assert message in capsys.readouterr().err


def test_files_converts_a_copy(tmp_path):
    src = tmp_path / "src"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "__init__.py").write_text(MODULE_SOURCE)
    (src / "pkg" / "data.txt").write_text("not python\n")
    out = tmp_path / "out"

    assert main(["files", str(src), "-o", str(out)]) == 0

    assert (src / "pkg" / "__init__.py").read_text() == MODULE_SOURCE
    converted = (out / "pkg" / "__init__.py").read_text()
    assert "lazy import" not in converted
    assert (out / "pkg" / "data.txt").read_text() == "not python\n"
    assert (out / "pkg" / "_retrofy_rt" / "lazy_imports.py").exists()


def test_files_refuses_non_empty_output(tmp_path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text("x = 1\n")
    out = tmp_path / "out"
    out.mkdir()
    (out / "existing.txt").write_text("keep me\n")

    with pytest.raises(SystemExit) as excinfo:
        main(["files", str(src), "-o", str(out)])
    assert excinfo.value.code == 2
    assert "not empty" in capsys.readouterr().err
    assert (out / "existing.txt").read_text() == "keep me\n"
