from __future__ import annotations

import pathlib
import shutil
import sys
import zipfile

from setuptools_ext import WheelModifier

from ._converters import convert

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


class EditableRuntimeRequirementError(RuntimeError):
    """The project being built editable has not marked ``dependencies``
    as ``dynamic``, so retrofy cannot legitimately inject itself as a
    runtime requirement.
    """


def _assert_editable_dependencies_dynamic(source_root: pathlib.Path) -> None:
    """Raise ``EditableRuntimeRequirementError`` unless the project at
    ``source_root`` lists ``dependencies`` in ``[project].dynamic``,
    which is what lets this backend splice ``Requires-Dist: retrofy``
    into the editable METADATA in a PEP 621-compliant way.
    """
    pyproject = source_root / "pyproject.toml"
    if not pyproject.exists():
        return
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = data.get("project", {})
    dynamic = project.get("dynamic", []) or []
    if "dependencies" in dynamic:
        return
    raise EditableRuntimeRequirementError(
        "Editable installs of retrofy-using projects need retrofy at "
        "runtime, but this project's pyproject.toml does not mark "
        "``dependencies`` as dynamic, so retrofy cannot inject itself "
        "as a runtime requirement in a PEP 621-compliant way.\n\n"
        "Add the following to pyproject.toml:\n\n"
        "    [project]\n"
        '    dynamic = ["dependencies"]\n\n'
        "Non-editable builds of the same project are unaffected and "
        "impose no such requirement.",
    )


def _splice_retrofy_requires_dist(text: str) -> str:
    """Return ``text`` with an unconditional ``Requires-Dist: retrofy``
    added to the METADATA header block.
    """
    # PEP 566 / RFC 822: the first blank line ends the header block and
    # starts the long-description body. The new Requires-Dist must go
    # inside the header block.
    header, sep, body = text.partition("\n\n")
    header = header.rstrip("\n")
    suffix = sep + body if sep else "\n"
    return header + "\nRequires-Dist: retrofy" + suffix


def inject_runtime_requirement(dist_info_path: pathlib.Path) -> None:
    """``post-prepare-metadata-for-build-editable`` hook.

    Editable installs of a retrofy-using project keep their original
    source on disk and rely on retrofy's import-time rewriter, so retrofy
    must be present at runtime. Splice ``Requires-Dist: retrofy`` into
    the prepared ``METADATA`` so pip resolves it as a runtime dep.
    """
    metadata_path = dist_info_path / "METADATA"
    text = metadata_path.read_text(encoding="utf-8")
    new_text = _splice_retrofy_requires_dist(text)
    if new_text != text:
        metadata_path.write_text(new_text, encoding="utf-8")


def compatibility_via_import_hook(wheel: pathlib.Path):
    """
    Add a pth hook to ensure make imported code compatible at import time
    (i.e. suitable for editable mode)

    """
    _assert_editable_dependencies_dynamic(pathlib.Path.cwd())

    editable_copy = wheel.parent / (wheel.name + ".copy.whl")
    shutil.copy(wheel, editable_copy)

    with zipfile.ZipFile(str(editable_copy), "r") as whl_zip:
        whl = WheelModifier(whl_zip)

        top_level = (
            whl.read(
                whl.dist_info_dirname() + "/top_level.txt",
            )
            .decode("utf-8")
            .splitlines()
        )
        top_level_pkgs = [pkg for pkg in top_level if pkg]

        for pkg in top_level_pkgs:
            fn = f"_retrofy.__editable_compat__.{pkg}.pth"
            script = (
                f"import retrofy._meta_hook_converter as c; c.register_hook(['{pkg}']);"
            )
            whl.write(zipfile.ZipInfo(fn), script)

        # Mirror the prepare_metadata_for_build_editable splice into the
        # final wheel so the installed dist-info also advertises retrofy
        # as a runtime dep.
        metadata_name = whl.dist_info_dirname() + "/METADATA"
        metadata_text = whl.read(metadata_name).decode("utf-8")
        new_metadata = _splice_retrofy_requires_dist(metadata_text)
        if new_metadata != metadata_text:
            whl.write(metadata_name, new_metadata)

        with wheel.open("wb") as whl_fh:
            whl.write_wheel(whl_fh)

    print("Enabling automatic retrofiting of Python code at import-time")

    editable_copy.unlink()


def compatibility_via_rewrite(wheel: pathlib.Path):
    """Change code within the given wheel to be compatible"""
    editable_copy = wheel.parent / (wheel.name + ".copy.whl")
    shutil.copy(wheel, editable_copy)

    has_modifications = False

    with zipfile.ZipFile(str(editable_copy), "r") as whl_zip:
        whl = WheelModifier(whl_zip)

        for filename in whl_zip.namelist():
            if filename.endswith(".py"):
                code = whl.read(filename).decode("utf-8")
                new_code = convert(code)
                if new_code != code:
                    print(f"Converted {filename} to compatibility syntax")
                    whl.write(filename, new_code)
                    has_modifications = True
        if has_modifications:
            with wheel.open("wb") as whl_fh:
                whl.write_wheel(whl_fh)

    editable_copy.unlink()
