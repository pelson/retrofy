from __future__ import annotations

import importlib.resources
import logging
import os
import pathlib
import posixpath
import re
import shutil
import sys
import tarfile
import tempfile
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
# modules need the ``_retrofy_rt`` sub-package dropped alongside them.
# Trailing space is significant — it pins the form
# ``from ._retrofy_rt.lazy_imports import <alias>, ...`` and avoids
# false positives on, say, ``from .._retrofy_rt.lazy_imports import ...``
# (different package depth that retrofy never emits).
_LAZY_RUNTIME_IMPORT_MARKER = "from ._retrofy_rt.lazy_imports import "


class _EmbeddedRuntimeCollisionError(RuntimeError):
    """The wheel already contains a ``_retrofy_rt`` entry in a directory
    where retrofy needs to inject its runtime helpers. ``_retrofy_rt``
    is reserved as the retrofy runtime namespace inside converted
    packages."""


class EditableRuntimeRequirementError(RuntimeError):
    """The project being built editable has not marked ``dependencies``
    as ``dynamic``, so retrofy cannot legitimately inject itself as a
    runtime requirement.
    """


def _embedded_runtime_files() -> dict[str, bytes]:
    """Return ``{relpath: bytes}`` for the ``_retrofy_rt`` payload that
    gets dropped into converted packages.

    The payload tree lives at ``retrofy/_retrofy_rt/`` in the source
    distribution and is the single canonical home for any runtime helper
    a converter needs to ship into user code.
    """
    root = importlib.resources.files("retrofy._retrofy_rt")
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


def _read_target_python(source_root: pathlib.Path) -> str | None:
    """Return the ``Requires-Python`` specifier requested by the project
    at ``source_root`` via ``[tool.retrofy] target-python``, or ``None``
    if the project does not opt in.

    The value in pyproject is a bare floor (e.g. ``"3.9"``); the returned
    string is a PEP 440 specifier (``">=3.9"``) suitable for splicing
    into METADATA verbatim.
    """
    pyproject = source_root / "pyproject.toml"
    if not pyproject.exists():
        return None
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    floor = data.get("tool", {}).get("retrofy", {}).get("target-python")
    if not floor:
        return None
    if not isinstance(floor, str):
        raise TypeError(
            f'[tool.retrofy] target-python must be a string (e.g. "3.9"), '
            f"got {type(floor).__name__} {floor!r}. Quote the value in "
            f"pyproject.toml -- TOML floats like ``3.10`` lose their "
            f"trailing zero and silently lower the floor.",
        )
    return f">={floor}"


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
    # PEP 566 / RFC 822 headers are case-insensitive on the field name,
    # so match permissively but emit the canonical capitalisation.
    replaced = False
    out_lines = []
    for line in header.split("\n"):
        if line.lower().startswith("requires-python:"):
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
    if os.environ.get("RETROFY_DISABLE_REWRITE") == "1":
        # Escape hatch for the chicken-and-egg bootstrap: producing
        # retrofy's *own* wheel from source needs a wheel-build pass
        # whose source still contains raw ``lazy from``, run without
        # any rewrite hook in the way. Setting this env var on that
        # first pass keeps the hook installed (entry point intact) but
        # makes it a no-op.
        _log.info("RETROFY_DISABLE_REWRITE=1 — skipping wheel rewrite")
        return
    editable_copy = wheel.parent / (wheel.name + ".copy.whl")
    shutil.copy(wheel, editable_copy)

    has_modifications = False
    # Directories within the wheel whose converted modules emitted a
    # ``from ._retrofy_rt.lazy_imports import (...)`` line. Each one
    # needs a sibling ``_retrofy_rt/`` sub-package dropped in.
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
                target_dir = f"{pkg_dir}/_retrofy_rt" if pkg_dir else "_retrofy_rt"
                # The canonical home of the runtime lives at
                # ``retrofy/_retrofy_rt/`` -- that is the source we are
                # otherwise copying from. When the wheel under rewrite
                # IS retrofy's own wheel, the converted module's own
                # sibling already contains the payload byte-for-byte, so
                # skip the inject (and the clash check below).
                if f"{target_dir}/lazy_imports.py" in existing_entries:
                    continue
                # ``_retrofy_rt`` is retrofy's reserved namespace inside
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
                        f"`_retrofy_rt` entry ({clash[0]!r}); `_retrofy_rt` "
                        f"is reserved as retrofy's runtime namespace inside "
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

        target_py = _read_target_python(pathlib.Path.cwd())
        if target_py is not None:
            metadata_name = whl.dist_info_dirname() + "/METADATA"
            metadata_text = whl.read(metadata_name).decode("utf-8")
            new_metadata = _lower_requires_python(metadata_text, target_py)
            if new_metadata != metadata_text:
                whl.write(metadata_name, new_metadata)
                _log.info(
                    "Lowered Requires-Python in %s to %s",
                    metadata_name,
                    target_py,
                )
                has_modifications = True

        if has_modifications:
            with wheel.open("wb") as whl_fh:
                whl.write_wheel(whl_fh)

    editable_copy.unlink()


def _strip_top_level(member_name: str) -> tuple[str, str]:
    """Return ``(top_level, relpath)`` for a tar member name.

    Sdists are canonically packaged as ``name-version/...`` -- one
    top-level directory containing everything. We don't accept anything
    else.
    """
    head, _, rest = member_name.partition("/")
    return head, rest


def _build_requires_drop_retrofy(requires: list[str]) -> list[str]:
    """Return ``requires`` with any entry whose PEP 508-normalised name is
    ``retrofy`` removed. Entries that are not parseable as PEP 508
    requirements are passed through unchanged.
    """
    out: list[str] = []
    for spec in requires:
        # Cheap parse: name is everything up to the first
        # ``<``, ``=``, ``>``, ``!``, ``~``, ``;``, ``[``, or whitespace.
        # Normalize per PEP 503 (lowercase, ``-``/``_``/``.`` collapsed).
        m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", spec)
        if not m:
            out.append(spec)
            continue
        name = re.sub(r"[-_.]+", "-", m.group(1)).lower()
        if name == "retrofy":
            continue
        out.append(spec)
    return out


def _patch_pyproject_for_lowered_sdist(text: str, floor: str) -> str:
    """Patch a sdist's ``pyproject.toml`` text for the lowered sdist:

    - rewrite ``[project].requires-python`` to ``>={floor}``; if
      ``requires-python`` was in ``[project].dynamic``, drop it.
    - drop ``retrofy`` from ``[build-system].requires``.

    Implementation note: round-tripping TOML losslessly is hard, so we
    keep the rewrite line-based. The pyproject files we touch are written
    by humans (or other tools that emit human-readable TOML); the cases
    we need to handle are the standard ones, not pathological TOML.
    """
    # Parse with tomllib for correctness; rewrite by string edits to
    # preserve formatting/comments where it doesn't matter.
    data = tomllib.loads(text)
    project = data.get("project", {})
    build_system = data.get("build-system", {})

    # --- [build-system].requires ---------------------------------------
    new_text = text
    requires = build_system.get("requires")
    if requires:
        new_requires = _build_requires_drop_retrofy(requires)
        if new_requires != requires:
            new_text = _rewrite_array_value(
                new_text,
                "build-system",
                "requires",
                new_requires,
            )

    # --- [project].dynamic --------------------------------------------
    dynamic = project.get("dynamic", []) or []
    if "requires-python" in dynamic:
        new_dynamic = [d for d in dynamic if d != "requires-python"]
        new_text = _rewrite_array_value(
            new_text,
            "project",
            "dynamic",
            new_dynamic,
        )

    # --- [project].requires-python -------------------------------------
    new_text = _set_requires_python(new_text, floor)

    return new_text


_TABLE_HEADER_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def _find_table_span(text: str, table_name: str) -> tuple[int, int] | None:
    """Return ``(start, end)`` byte offsets of the body of ``[table_name]``
    in ``text``, or ``None`` if the table is absent. ``start`` is just
    after the header line; ``end`` is at the start of the next table
    header (or EOF).
    """
    lines = text.splitlines(keepends=True)
    start_line: int | None = None
    for i, line in enumerate(lines):
        m = _TABLE_HEADER_RE.match(line)
        if m and m.group(1).strip() == table_name:
            start_line = i + 1
            break
    if start_line is None:
        return None
    end_line = len(lines)
    for j in range(start_line, len(lines)):
        m = _TABLE_HEADER_RE.match(lines[j])
        if m:
            end_line = j
            break
    start = sum(len(line) for line in lines[:start_line])
    end = sum(len(line) for line in lines[:end_line])
    return start, end


_KEY_RE_TMPL = r"^(\s*){key}\s*=\s*"


def _rewrite_array_value(
    text: str,
    table: str,
    key: str,
    new_value: list[str],
) -> str:
    """Replace the value of ``table.key`` (assumed to be an inline array)
    with ``new_value`` rendered as a single-line inline array. Leaves
    other formatting untouched. If the key isn't found, returns text
    unchanged.
    """
    span = _find_table_span(text, table)
    if span is None:
        return text
    start, end = span
    body = text[start:end]
    pattern = re.compile(
        _KEY_RE_TMPL.format(key=re.escape(key)) + r"(\[[^\]]*\])",
        flags=re.MULTILINE,
    )
    rendered = "[" + ", ".join(f'"{v}"' for v in new_value) + "]"
    new_body, n = pattern.subn(
        lambda m: f"{m.group(1)}{key} = {rendered}",
        body,
        count=1,
    )
    if n == 0:
        return text
    return text[:start] + new_body + text[end:]


def _set_requires_python(text: str, floor: str) -> str:
    """Set ``[project].requires-python = ">={floor}"``. If the table
    exists but the key doesn't, append the key inside the table; if the
    table is missing, return text unchanged (a pyproject without
    ``[project]`` isn't a PEP 621 project and we have nothing to lower).
    """
    span = _find_table_span(text, "project")
    if span is None:
        return text
    start, end = span
    body = text[start:end]
    pattern = re.compile(
        _KEY_RE_TMPL.format(key=re.escape("requires-python")) + r'"[^"]*"',
        flags=re.MULTILINE,
    )
    new_body, n = pattern.subn(
        lambda m: f'{m.group(1)}requires-python = ">={floor}"',
        body,
        count=1,
    )
    if n == 0:
        # Append at the end of the table body.
        trailing_blank = ""
        if not body.endswith("\n"):
            trailing_blank = "\n"
        new_body = body + trailing_blank + f'requires-python = ">={floor}"\n'
    return text[:start] + new_body + text[end:]


def lower_sdist(sdist_path: pathlib.Path) -> None:
    """``post-build-sdist`` hook.

    When ``[tool.retrofy] target-python`` is set, rewrite the sdist in
    place so it is installable on the target Python without retrofy in
    the build environment: convert source files, inject ``_retrofy_rt/``
    where needed, lower ``Requires-Python`` in pyproject + PKG-INFO,
    and strip ``retrofy`` from ``[build-system].requires``.

    ``RETROFY_DISABLE_REWRITE=1`` short-circuits to a no-op, mirroring
    the wheel hook's bootstrap escape.
    """
    if os.environ.get("RETROFY_DISABLE_REWRITE") == "1":
        _log.info("RETROFY_DISABLE_REWRITE=1 — skipping sdist rewrite")
        return
    target_py = _read_target_python(pathlib.Path.cwd())
    if target_py is None:
        return
    floor = target_py.lstrip(">=")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        with tarfile.open(sdist_path, "r:gz") as tar:
            members = tar.getmembers()
            top_levels = {_strip_top_level(m.name)[0] for m in members if m.name}
            if len(top_levels) != 1:
                raise RuntimeError(
                    f"sdist {sdist_path.name} does not have a single "
                    f"top-level directory (found {sorted(top_levels)!r}); "
                    f"retrofy's sdist lowering only supports the standard "
                    f"name-version layout.",
                )
            top = next(iter(top_levels))
            # ``filter="data"`` lands in 3.12; on 3.9-3.11 it emits a
            # DeprecationWarning and behaves like fully-trusted. We're
            # opening a sdist we just produced ourselves, not user-
            # supplied input, so trusted-extraction is acceptable.
            if sys.version_info >= (3, 12):
                tar.extractall(tmp, filter="data")
            else:
                tar.extractall(tmp)  # noqa: S202  # see comment above

        root = tmp / top
        existing_entries = {
            str(p.relative_to(root)).replace(os.sep, "/")
            for p in root.rglob("*")
            if p.is_file()
        }

        # Convert sources + collect lazy runtime dirs.
        lazy_runtime_dirs: set[str] = set()
        for py in root.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            new_text = convert(text)
            if new_text != text:
                py.write_text(new_text, encoding="utf-8")
                _log.info(
                    "Converted %s to compatibility syntax",
                    py.relative_to(root),
                )
                if _LAZY_RUNTIME_IMPORT_MARKER in new_text:
                    rel = posixpath.dirname(
                        str(py.relative_to(root)).replace(os.sep, "/"),
                    )
                    lazy_runtime_dirs.add(rel)

        # Inject _retrofy_rt/ payload where needed.
        if lazy_runtime_dirs:
            payload = _embedded_runtime_files()
            for pkg_dir in sorted(lazy_runtime_dirs):
                target_dir = f"{pkg_dir}/_retrofy_rt" if pkg_dir else "_retrofy_rt"
                if f"{target_dir}/lazy_imports.py" in existing_entries:
                    continue
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
                        f"`_retrofy_rt` entry ({clash[0]!r}); `_retrofy_rt` "
                        f"is reserved as retrofy's runtime namespace inside "
                        f"converted packages.",
                    )
                inject_dir = root / target_dir.replace("/", os.sep)
                inject_dir.mkdir(parents=True, exist_ok=True)
                for relpath, data in payload.items():
                    (inject_dir / relpath).write_bytes(data)
                _log.info(
                    "Injected retrofy runtime payload into %s/",
                    target_dir,
                )

        # Patch pyproject.toml.
        pyproj = root / "pyproject.toml"
        if pyproj.exists():
            text = pyproj.read_text(encoding="utf-8")
            new_text = _patch_pyproject_for_lowered_sdist(text, floor)
            if new_text != text:
                pyproj.write_text(new_text, encoding="utf-8")

        # Patch PKG-INFO.
        pkg_info = root / "PKG-INFO"
        if pkg_info.exists():
            text = pkg_info.read_text(encoding="utf-8")
            new_text = _lower_requires_python(text, target_py)
            if new_text != text:
                pkg_info.write_text(new_text, encoding="utf-8")

        # Repack.
        tmp_sdist = sdist_path.with_suffix(sdist_path.suffix + ".tmp")
        with tarfile.open(tmp_sdist, "w:gz") as tar:
            tar.add(root, arcname=top, recursive=True)
        os.replace(tmp_sdist, sdist_path)
