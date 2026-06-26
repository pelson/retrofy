from __future__ import annotations

import io
import pathlib
import re
import tarfile
import textwrap
import zipfile

import pytest

from retrofy import __version__ as RETROFY_VERSION
from retrofy._pep517_hooks import (
    EditableRuntimeRequirementError,
    _assert_editable_dependencies_dynamic,
    _EmbeddedRuntimeCollisionError,
    _lower_requires_python,
    _read_target_python,
    compatibility_via_rewrite,
    inject_runtime_requirement,
    lower_sdist,
)

PINNED_REQUIRES = f"Requires-Dist: retrofy=={RETROFY_VERSION}"


def _write_metadata(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    dist_info = tmp_path / "some_project-0.1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(textwrap.dedent(body), encoding="utf-8")
    return dist_info


def test_inject_runtime_requirement_adds_dep(tmp_path):
    dist_info = _write_metadata(
        tmp_path,
        """\
        Metadata-Version: 2.1
        Name: some-project
        Version: 0.1.0
        Requires-Dist: libcst

        Some long description.
        """,
    )
    inject_runtime_requirement(dist_info)
    text = (dist_info / "METADATA").read_text(encoding="utf-8")
    assert f"{PINNED_REQUIRES}\n" in text
    assert "Requires-Dist: libcst\n" in text
    assert text.endswith("Some long description.\n")
    # The line must land inside the header block (before the blank line),
    # otherwise pip parses it as part of the description.
    header, _, _ = text.partition("\n\n")
    assert PINNED_REQUIRES in header


def test_inject_runtime_requirement_adds_even_if_retrofy_already_present(tmp_path):
    # A pre-existing retrofy line may carry environment markers that
    # leave the running interpreter without retrofy installed, so we
    # add an unconditional line regardless. Pip dedupes the rest.
    dist_info = _write_metadata(
        tmp_path,
        """\
        Metadata-Version: 2.1
        Name: some-project
        Version: 0.1.0
        Requires-Dist: retrofy; python_version < "3.12"

        Body.
        """,
    )
    inject_runtime_requirement(dist_info)
    text = (dist_info / "METADATA").read_text(encoding="utf-8")
    header, _, _ = text.partition("\n\n")
    lines = [
        line
        for line in header.splitlines()
        if line.startswith("Requires-Dist: retrofy")
    ]
    assert len(lines) == 2
    assert PINNED_REQUIRES in lines


def test_inject_runtime_requirement_no_body(tmp_path):
    dist_info = _write_metadata(
        tmp_path,
        """\
        Metadata-Version: 2.1
        Name: some-project
        Version: 0.1.0
        """,
    )
    inject_runtime_requirement(dist_info)
    text = (dist_info / "METADATA").read_text(encoding="utf-8")
    assert PINNED_REQUIRES in text


def _write_pyproject(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    (tmp_path / "pyproject.toml").write_text(textwrap.dedent(body), encoding="utf-8")
    return tmp_path


def test_assert_editable_dependencies_dynamic_passes_when_dynamic(tmp_path):
    root = _write_pyproject(
        tmp_path,
        """\
        [project]
        name = "some-project"
        version = "0.1"
        dynamic = ["dependencies"]
        """,
    )
    _assert_editable_dependencies_dynamic(root)


def test_assert_editable_dependencies_dynamic_raises_when_static(tmp_path):
    root = _write_pyproject(
        tmp_path,
        """\
        [project]
        name = "some-project"
        version = "0.1"
        dependencies = ["retrofy"]
        """,
    )
    with pytest.raises(EditableRuntimeRequirementError, match="dynamic"):
        _assert_editable_dependencies_dynamic(root)


def test_assert_editable_dependencies_dynamic_raises_when_absent(tmp_path):
    root = _write_pyproject(
        tmp_path,
        """\
        [project]
        name = "some-project"
        version = "0.1"
        """,
    )
    with pytest.raises(EditableRuntimeRequirementError):
        _assert_editable_dependencies_dynamic(root)


def test_assert_editable_dependencies_dynamic_skips_when_no_pyproject(tmp_path):
    _assert_editable_dependencies_dynamic(tmp_path)


def test_lower_requires_python_replaces_existing_line():
    text = (
        "Metadata-Version: 2.1\n"
        "Name: retrofy\n"
        "Version: 0.4.0\n"
        "Requires-Python: >=3.15\n"
        "Requires-Dist: libcst\n"
        "\n"
        "Long description.\n"
    )
    new = _lower_requires_python(text, ">=3.9")
    assert "Requires-Python: >=3.9\n" in new
    assert "Requires-Python: >=3.15" not in new
    assert "Requires-Dist: libcst\n" in new
    assert new.endswith("Long description.\n")


def test_lower_requires_python_adds_when_missing():
    text = (
        "Metadata-Version: 2.1\n"
        "Name: retrofy\n"
        "Version: 0.4.0\n"
        "Requires-Dist: libcst\n"
        "\n"
        "Body.\n"
    )
    new = _lower_requires_python(text, ">=3.9")
    header, _, _ = new.partition("\n\n")
    assert "Requires-Python: >=3.9" in header


def test_lower_requires_python_only_touches_header_block():
    text = (
        "Metadata-Version: 2.1\n"
        "Name: retrofy\n"
        "Version: 0.4.0\n"
        "Requires-Python: >=3.15\n"
        "\n"
        "See Requires-Python: >=3.15 in the docs.\n"
    )
    new = _lower_requires_python(text, ">=3.9")
    header, _, body = new.partition("\n\n")
    assert "Requires-Python: >=3.9" in header
    assert "Requires-Python: >=3.15" not in header
    assert "Requires-Python: >=3.15" in body


def test_lower_requires_python_matches_case_insensitively():
    # PEP 566 headers are case-insensitive on the field name; a
    # lowercase ``requires-python:`` line must still be recognised
    # and replaced (with the canonical capitalisation).
    text = "Metadata-Version: 2.1\nName: retrofy\nrequires-python: >=3.15\n\nBody.\n"
    new = _lower_requires_python(text, ">=3.9")
    assert "Requires-Python: >=3.9" in new
    assert "requires-python: >=3.15" not in new
    assert "Requires-Python: >=3.15" not in new


def test_read_target_python_returns_floor_when_set(tmp_path):
    root = _write_pyproject(
        tmp_path,
        """\
        [project]
        name = "x"
        [tool.retrofy]
        target-python = "3.9"
        """,
    )
    assert _read_target_python(root) == "3.9"


def test_read_target_python_none_when_section_missing(tmp_path):
    root = _write_pyproject(
        tmp_path,
        """\
        [project]
        name = "x"
        """,
    )
    assert _read_target_python(root) is None


def test_read_target_python_none_when_key_missing(tmp_path):
    root = _write_pyproject(
        tmp_path,
        """\
        [project]
        name = "x"
        [tool.retrofy]
        """,
    )
    assert _read_target_python(root) is None


def test_read_target_python_none_when_pyproject_missing(tmp_path):
    assert _read_target_python(tmp_path) is None


def test_read_target_python_rejects_non_string(tmp_path):
    # Unquoted ``target-python = 3.10`` parses as the TOML float 3.10,
    # which str()s to "3.1" -- silently lowering the floor below what
    # the user intended. Reject anything that isn't a string.
    root = _write_pyproject(
        tmp_path,
        """\
        [project]
        name = "x"
        [tool.retrofy]
        target-python = 3.10
        """,
    )
    with pytest.raises(TypeError, match="must be a string"):
        _read_target_python(root)


def _make_minimal_wheel(tmp_path: pathlib.Path, metadata_text: str) -> pathlib.Path:
    whl = tmp_path / "dummypkg-0.0.0-py3-none-any.whl"
    dist_info = "dummypkg-0.0.0.dist-info"
    with zipfile.ZipFile(whl, "w") as z:
        z.writestr(f"{dist_info}/METADATA", metadata_text)
        z.writestr(
            f"{dist_info}/WHEEL",
            "Wheel-Version: 1.0\n"
            "Generator: test\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n",
        )
        z.writestr(f"{dist_info}/RECORD", "")
    return whl


def test_compatibility_via_rewrite_lowers_requires_python_when_opted_in(
    tmp_path,
    monkeypatch,
):
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [project]
            name = "dummypkg"
            [tool.retrofy]
            target-python = "3.9"
            """,
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    metadata = (
        "Metadata-Version: 2.1\n"
        "Name: dummypkg\n"
        "Version: 0.0.0\n"
        "Requires-Python: >=3.15\n"
        "\n"
        "Body.\n"
    )
    whl = _make_minimal_wheel(tmp_path, metadata)

    compatibility_via_rewrite(whl)

    with zipfile.ZipFile(whl) as z:
        new_metadata = z.read("dummypkg-0.0.0.dist-info/METADATA").decode("utf-8")
    header, _, _ = new_metadata.partition("\n\n")
    assert "Requires-Python: >=3.9" in header
    assert "Requires-Python: >=3.15" not in header


def test_compatibility_via_rewrite_leaves_requires_python_when_not_opted_in(
    tmp_path,
    monkeypatch,
):
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [project]
            name = "dummypkg"
            """,
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    metadata = (
        "Metadata-Version: 2.1\n"
        "Name: dummypkg\n"
        "Version: 0.0.0\n"
        "Requires-Python: >=3.15\n"
        "\n"
        "Body.\n"
    )
    whl = _make_minimal_wheel(tmp_path, metadata)

    compatibility_via_rewrite(whl)

    with zipfile.ZipFile(whl) as z:
        new_metadata = z.read("dummypkg-0.0.0.dist-info/METADATA").decode("utf-8")
    assert "Requires-Python: >=3.15" in new_metadata
    assert "Requires-Python: >=3.9" not in new_metadata


def test_compatibility_via_rewrite_skipped_when_disable_env_set(
    tmp_path,
    monkeypatch,
):
    # Bootstrap escape hatch: with ``RETROFY_DISABLE_REWRITE=1``, the
    # hook must be a complete no-op even when the project opts in to
    # rewriting via ``[tool.retrofy] target-python``.
    (tmp_path / "pyproject.toml").write_text(
        textwrap.dedent(
            """\
            [project]
            name = "dummypkg"
            [tool.retrofy]
            target-python = "3.9"
            """,
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RETROFY_DISABLE_REWRITE", "1")

    raw_source = "lazy from foo import bar\n"
    raw_metadata = (
        "Metadata-Version: 2.1\n"
        "Name: dummypkg\n"
        "Version: 0.0.0\n"
        "Requires-Python: >=3.15\n"
        "\n"
        "Body.\n"
    )
    whl = _make_minimal_wheel(tmp_path, raw_metadata)
    # Stuff a raw-lazy .py into the wheel so we'd notice if convert
    # ran anyway.
    with zipfile.ZipFile(whl, "a") as z:
        z.writestr("dummypkg/x.py", raw_source)

    compatibility_via_rewrite(whl)

    with zipfile.ZipFile(whl) as z:
        py = z.read("dummypkg/x.py").decode("utf-8")
        md = z.read("dummypkg-0.0.0.dist-info/METADATA").decode("utf-8")
    assert py == raw_source  # not converted
    assert "Requires-Python: >=3.15" in md  # METADATA not lowered


# ---------------------------------------------------------------------------
# lower_sdist
# ---------------------------------------------------------------------------


def _make_minimal_sdist(
    tmp_path: pathlib.Path,
    files: dict[str, str | bytes],
    *,
    name: str = "dummypkg-0.0.0",
) -> pathlib.Path:
    """Build a ``{name}.tar.gz`` at ``tmp_path`` whose top-level directory
    is ``{name}/`` and whose contents are ``files`` (mapping of
    ``relative/path`` -> text or bytes).

    PKG-INFO is auto-supplied if not in ``files``.
    """
    files = dict(files)
    files.setdefault(
        "PKG-INFO",
        "Metadata-Version: 2.1\n"
        "Name: dummypkg\n"
        "Version: 0.0.0\n"
        "Requires-Python: >=3.15\n",
    )
    sdist = tmp_path / f"{name}.tar.gz"
    with tarfile.open(sdist, "w:gz") as tar:
        for relpath, content in files.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            info = tarfile.TarInfo(f"{name}/{relpath}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return sdist


def _read_sdist(sdist: pathlib.Path, name: str = "dummypkg-0.0.0") -> dict[str, bytes]:
    """Read every regular file in the sdist into a dict keyed by the
    relative path under the top-level ``{name}/`` directory."""
    out: dict[str, bytes] = {}
    with tarfile.open(sdist, "r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            prefix = f"{name}/"
            assert member.name.startswith(prefix), member.name
            fh = tar.extractfile(member)
            assert fh is not None  # isfile() above guarantees this
            out[member.name[len(prefix) :]] = fh.read()
    return out


def _opt_in_pyproject(text: str) -> str:
    return textwrap.dedent(text)


def test_lower_sdist_converts_py_files(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        _opt_in_pyproject(
            """\
            [project]
            name = "dummypkg"
            [tool.retrofy]
            target-python = "3.9"
            """,
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    sdist = _make_minimal_sdist(
        tmp_path,
        {
            "dummypkg/__init__.py": "",
            "dummypkg/x.py": "lazy from foo import bar\n",
        },
    )

    lower_sdist(sdist)

    files = _read_sdist(sdist)
    assert "lazy from foo import bar" not in files["dummypkg/x.py"].decode("utf-8")
    assert "__lazy_from__" in files["dummypkg/x.py"].decode("utf-8")


def test_lower_sdist_injects_runtime_payload(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        _opt_in_pyproject(
            """\
            [project]
            name = "dummypkg"
            [tool.retrofy]
            target-python = "3.9"
            """,
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    sdist = _make_minimal_sdist(
        tmp_path,
        {
            "dummypkg/__init__.py": "",
            "dummypkg/x.py": "lazy from foo import bar\n",
        },
    )

    lower_sdist(sdist)

    files = _read_sdist(sdist)
    assert "dummypkg/_retrofy_rt/lazy_imports.py" in files
    assert b"def lazy_from" in files["dummypkg/_retrofy_rt/lazy_imports.py"]


def test_lower_sdist_patches_pyproject_requires_python(tmp_path, monkeypatch):
    pyproject = _opt_in_pyproject(
        """\
        [build-system]
        requires = ["multistage-build>=0.2", "setuptools", "retrofy>=0.3"]
        build-backend = "multistage_build:backend"

        [project]
        name = "dummypkg"
        version = "0.1"
        requires-python = ">=3.15"

        [tool.retrofy]
        target-python = "3.9"
        """,
    )
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    sdist = _make_minimal_sdist(tmp_path, {"pyproject.toml": pyproject})

    lower_sdist(sdist)

    new = _read_sdist(sdist)["pyproject.toml"].decode("utf-8")
    assert 'requires-python = ">=3.9"' in new
    assert ">=3.15" not in new


def test_lower_sdist_patches_pyproject_dynamic_requires_python(tmp_path, monkeypatch):
    pyproject = _opt_in_pyproject(
        """\
        [build-system]
        requires = ["multistage-build>=0.2", "setuptools", "retrofy>=0.3"]
        build-backend = "multistage_build:backend"

        [project]
        name = "dummypkg"
        version = "0.1"
        dynamic = ["requires-python", "dependencies"]

        [tool.retrofy]
        target-python = "3.9"
        """,
    )
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    sdist = _make_minimal_sdist(tmp_path, {"pyproject.toml": pyproject})

    lower_sdist(sdist)

    new = _read_sdist(sdist)["pyproject.toml"].decode("utf-8")
    assert 'requires-python = ">=3.9"' in new
    # requires-python is gone from dynamic; dependencies is still there.
    # Match the rendered inline-array line we emit.
    assert 'dynamic = ["dependencies"]' in new
    assert '"requires-python"' not in new


@pytest.mark.parametrize(
    "spec",
    [
        "retrofy",
        "retrofy>=0.3",
        "retrofy[extra]",
        "retrofy ; python_version < '3.13'",
        "RetroFy",  # PEP 503 case-insensitivity
        "RETROFY",  # PEP 503 case-insensitivity
    ],
)
def test_lower_sdist_strips_retrofy_from_build_requires(spec, tmp_path, monkeypatch):
    pyproject = (
        "[build-system]\n"
        f'requires = ["multistage-build>=0.2", "setuptools", "{spec}"]\n'
        'build-backend = "multistage_build:backend"\n'
        "\n"
        "[project]\n"
        'name = "dummypkg"\n'
        'version = "0.1"\n'
        "\n"
        "[tool.retrofy]\n"
        'target-python = "3.9"\n'
    )
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    sdist = _make_minimal_sdist(tmp_path, {"pyproject.toml": pyproject})

    lower_sdist(sdist)

    new = _read_sdist(sdist)["pyproject.toml"].decode("utf-8")
    requires_line = next(
        line for line in new.splitlines() if line.strip().startswith("requires =")
    )
    assert "retrofy" not in requires_line.lower()
    assert "multistage-build" in requires_line


def test_lower_sdist_patches_pkg_info(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        _opt_in_pyproject(
            """\
            [project]
            name = "dummypkg"
            [tool.retrofy]
            target-python = "3.9"
            """,
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    sdist = _make_minimal_sdist(tmp_path, {})

    lower_sdist(sdist)

    pkg_info = _read_sdist(sdist)["PKG-INFO"].decode("utf-8")
    assert "Requires-Python: >=3.9" in pkg_info
    assert "Requires-Python: >=3.15" not in pkg_info


def test_lower_sdist_converts_source_even_without_target_python(tmp_path, monkeypatch):
    # Without ``[tool.retrofy] target-python`` the metadata edits are
    # skipped, but source conversion still runs -- modern syntax is
    # unparseable on older Pythons regardless of whether the project
    # has opted into Requires-Python lowering, so emitting unconverted
    # source would leave a half-broken sdist behind.
    pyproject = '[project]\nname = "dummypkg"\nrequires-python = ">=3.15"\n'
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    raw_source = "lazy from foo import bar\n"
    sdist = _make_minimal_sdist(
        tmp_path,
        {"dummypkg/x.py": raw_source, "pyproject.toml": pyproject},
    )

    lower_sdist(sdist)

    files = _read_sdist(sdist)
    # Source was converted...
    assert files["dummypkg/x.py"].decode("utf-8") != raw_source
    assert "__lazy_from__" in files["dummypkg/x.py"].decode("utf-8")
    # ...but pyproject/PKG-INFO were left alone.
    assert ">=3.15" in files["pyproject.toml"].decode("utf-8")
    assert "Requires-Python: >=3.15" in files["PKG-INFO"].decode("utf-8")


def test_lower_sdist_skipped_when_disable_env_set(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        _opt_in_pyproject(
            """\
            [project]
            name = "dummypkg"
            [tool.retrofy]
            target-python = "3.9"
            """,
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RETROFY_DISABLE_REWRITE", "1")
    raw_source = "lazy from foo import bar\n"
    sdist = _make_minimal_sdist(tmp_path, {"dummypkg/x.py": raw_source})

    lower_sdist(sdist)

    files = _read_sdist(sdist)
    assert files["dummypkg/x.py"].decode("utf-8") == raw_source
    assert "Requires-Python: >=3.15" in files["PKG-INFO"].decode("utf-8")


def test_lower_sdist_collision_on_existing_retrofy_rt(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        _opt_in_pyproject(
            """\
            [project]
            name = "dummypkg"
            [tool.retrofy]
            target-python = "3.9"
            """,
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    sdist = _make_minimal_sdist(
        tmp_path,
        {
            "dummypkg/__init__.py": "",
            "dummypkg/x.py": "lazy from foo import bar\n",
            "dummypkg/_retrofy_rt/preexisting.py": "# user-owned\n",
        },
    )

    with pytest.raises(_EmbeddedRuntimeCollisionError):
        lower_sdist(sdist)


def test_lower_sdist_strip_name_matches_retrofy_distribution_name():
    """Contract: the name ``lower_sdist`` looks for in
    ``[build-system].requires`` must match retrofy's installed
    distribution name (PEP 503 normalised). If retrofy is ever renamed
    on PyPI, this test catches the silent miss.
    """
    import importlib.metadata

    from retrofy._pep517_hooks import _build_requires_drop_retrofy

    dist_name = importlib.metadata.distribution("retrofy").metadata["Name"]
    normalized = re.sub(r"[-_.]+", "-", dist_name).lower()
    assert normalized == "retrofy"
    # And a sanity round-trip: dropping the live dist name works.
    assert _build_requires_drop_retrofy([dist_name, "multistage-build"]) == [
        "multistage-build",
    ]


def test_lower_sdist_entry_point_registered():
    """Contract: the installed retrofy distribution advertises
    ``post-build-sdist = retrofy._pep517_hooks:lower_sdist`` in the
    ``multistage_build`` entry-point group, so multistage-build's hook
    discovery picks it up at build time.
    """
    import importlib.metadata

    retrofy_eps = [
        ep
        for ep in importlib.metadata.distribution("retrofy").entry_points
        if ep.group == "multistage_build"
        and ep.name == "post-build-sdist"
        and ep.value.startswith("retrofy.")
    ]
    assert len(retrofy_eps) == 1
    assert retrofy_eps[0].value == "retrofy._pep517_hooks:lower_sdist"
