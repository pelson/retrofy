from __future__ import annotations

import importlib.resources
import logging
import pathlib
import posixpath
import shutil
import sys
import zipfile

from setuptools_ext import WheelModifier

from ._converters import convert

_log = logging.getLogger("retrofy.build")

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


# Marker that ``transform_lazy_imports`` injects into any module it
# rewrites. The wheel-build hook uses this to identify which converted
# modules need the ``_retrofy`` sub-package dropped alongside them.
# Trailing space is significant — it pins the form
# ``from ._retrofy.lazy_runtime import <alias>, ...`` and avoids
# false positives on, say, ``from .._retrofy.lazy_runtime import ...``
# (different package depth that retrofy never emits).
_LAZY_RUNTIME_IMPORT_MARKER = "from ._retrofy.lazy_runtime import "


class _EmbeddedRuntimeCollisionError(RuntimeError):
    """The wheel already contains a ``_retrofy`` entry in a directory
    where retrofy needs to inject its runtime helpers. ``_retrofy`` is
    reserved as the retrofy runtime namespace inside converted
    packages."""


class EditableRuntimeRequirementError(RuntimeError):
    """The project being built editable has not marked ``dependencies``
    as ``dynamic``, so retrofy cannot legitimately inject itself as a
    runtime requirement.
    """


def _embedded_runtime_files() -> dict[str, bytes]:
    """Return ``{relpath: bytes}`` for the ``_retrofy`` payload that
    gets dropped into converted packages.

    The payload tree lives at ``retrofy/_embedded_runtime/_retrofy/`` in the
    source distribution and is the single canonical home for any
    runtime helper a converter needs to ship into user code.
    """
    root = importlib.resources.files("retrofy._embedded_runtime._retrofy")
    out: dict[str, bytes] = {}
    for entry in root.iterdir():
        if not entry.is_file():
            continue
        if entry.name.endswith(".py"):
            out[entry.name] = entry.read_bytes()
    return out


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


def _retrofy_version() -> str:
    # Imported lazily so this module is safe to import without retrofy's
    # own setuptools-scm-generated ``_version.py`` being on disk yet
    # (e.g. during retrofy's own bootstrap build).
    from . import __version__

    return __version__


def _splice_retrofy_requires_dist(text: str) -> str:
    """Return ``text`` with ``Requires-Dist: retrofy=={VERSION}`` added
    to the METADATA header block, where ``VERSION`` is the exact version
    of the retrofy that is doing the splicing.

    Pinning to the build-env retrofy keeps build- and runtime-retrofy
    in lockstep: the editable wheel was produced by the import-hook
    code of a specific retrofy version, and that exact code has to be
    importable at runtime for the editable install to behave
    consistently. Leaving the line unconstrained instead asks the
    runtime resolver to make a policy decision (stable vs prerelease,
    PyPI vs find-links) which is not stable across uv versions.
    """
    # PEP 566 / RFC 822: the first blank line ends the header block and
    # starts the long-description body. The new Requires-Dist must go
    # inside the header block.
    header, sep, body = text.partition("\n\n")
    header = header.rstrip("\n")
    suffix = sep + body if sep else "\n"
    return header + f"\nRequires-Dist: retrofy=={_retrofy_version()}" + suffix


def _lower_requires_python(text: str, floor: str) -> str:
    """Return ``text`` with the ``Requires-Python:`` line in the METADATA
    header replaced by ``Requires-Python: {floor}``. If no such line is
    present in the header, one is appended to the header block.

    Only the header block (everything before the first blank line) is
    touched -- a stray ``Requires-Python:`` mention in the long
    description body is preserved verbatim.
    """
    header, sep, body = text.partition("\n\n")
    new_value = f"Requires-Python: {floor}"
    replaced = False
    out_lines = []
    for line in header.split("\n"):
        if line.startswith("Requires-Python:"):
            out_lines.append(new_value)
            replaced = True
        else:
            out_lines.append(line)
    if not replaced:
        out_lines.append(new_value)
    new_header = "\n".join(out_lines)
    return new_header + (sep + body if sep else "")


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

    _log.info("Enabling automatic retrofiting of Python code at import-time")

    editable_copy.unlink()


def compatibility_via_rewrite(wheel: pathlib.Path):
    """Change code within the given wheel to be compatible"""
    editable_copy = wheel.parent / (wheel.name + ".copy.whl")
    shutil.copy(wheel, editable_copy)

    has_modifications = False
    # Directories within the wheel whose converted modules emitted a
    # ``from ._retrofy.lazy_runtime import (...)`` line. Each one needs
    # a sibling ``_retrofy/`` sub-package dropped in.
    lazy_runtime_dirs: set[str] = set()

    with zipfile.ZipFile(str(editable_copy), "r") as whl_zip:
        whl = WheelModifier(whl_zip)
        existing_entries = set(whl_zip.namelist())

        for filename in whl_zip.namelist():
            if filename.endswith(".py"):
                code = whl.read(filename).decode("utf-8")
                new_code = convert(code)
                if new_code != code:
                    _log.info("Converted %s to compatibility syntax", filename)
                    whl.write(filename, new_code)
                    has_modifications = True
                    if _LAZY_RUNTIME_IMPORT_MARKER in new_code:
                        lazy_runtime_dirs.add(posixpath.dirname(filename))

        if lazy_runtime_dirs:
            payload = _embedded_runtime_files()
            for pkg_dir in sorted(lazy_runtime_dirs):
                target_dir = f"{pkg_dir}/_retrofy" if pkg_dir else "_retrofy"
                # ``_retrofy`` is retrofy's reserved namespace inside
                # converted packages. If a user already ships anything
                # under that name we'd silently shadow it — refuse to.
                clash = [
                    e
                    for e in existing_entries
                    if e == target_dir
                    or e == f"{target_dir}/"
                    or e.startswith(f"{target_dir}/")
                ]
                if clash:
                    raise _EmbeddedRuntimeCollisionError(
                        f"package directory {pkg_dir!r} already contains a "
                        f"`_retrofy` entry ({clash[0]!r}); `_retrofy` is "
                        f"reserved as retrofy's runtime namespace inside "
                        f"converted packages.",
                    )
                for relpath, data in payload.items():
                    # WheelModifier.write needs a ZipInfo for entries
                    # not already in the source wheel (which the
                    # injected payload never is).
                    whl.write(zipfile.ZipInfo(f"{target_dir}/{relpath}"), data)
                _log.info(
                    "Injected retrofy runtime payload into %s/",
                    target_dir,
                )
            has_modifications = True

        if has_modifications:
            with wheel.open("wb") as whl_fh:
                whl.write_wheel(whl_fh)

    editable_copy.unlink()
