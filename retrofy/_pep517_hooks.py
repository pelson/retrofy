from __future__ import annotations

import gzip
import importlib
import importlib.resources
import logging
import os
import pathlib
import posixpath
import shutil
import sys
import tarfile
import tempfile
import textwrap
import typing
import zipfile

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version
from setuptools_ext import WheelModifier
import tomlkit

from ._converters import convert
from ._transformations.typing_extensions import TYPING_EXTENSIONS_CONFIG

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


def _dependencies_are_dynamic(source_root: pathlib.Path) -> bool:
    """Return whether the project at ``source_root`` leaves its
    ``dependencies`` to the build backend: listed in ``[project].dynamic``
    (fully dynamic, or partially dynamic as per PEP 808), or with no
    ``[project]`` table (or ``pyproject.toml``) at all. Only then may retrofy
    add itself to them; static dependencies are left as they are (PEP 621).
    """
    pyproject = source_root / "pyproject.toml"
    if not pyproject.exists():
        return True
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = data.get("project")
    if project is None:
        return True
    return "dependencies" in (project.get("dynamic", []) or [])


def _read_target_python(source_root: pathlib.Path) -> str | None:
    """Return the bare floor (e.g. ``"3.9"``) requested by the project at
    ``source_root`` via ``[tool.retrofy] target-python``, or ``None`` if
    the project does not opt in.

    The value must be a single PEP 440 version (``"3.9"``, ``"3.9.7"``)
    -- range specifiers like ``">=3.9"`` or ``"3.9,!=3.12.*"`` are
    rejected. ``target-python`` names the exact floor to lower to, not a
    specifier; any range-shaped exclusions belong in the project's own
    ``[project].requires-python`` and are preserved by the rewrite.
    """
    pyproject = source_root / "pyproject.toml"
    if not pyproject.exists():
        return None
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    floor = data.get("tool", {}).get("retrofy", {}).get("target-python")
    if not floor:
        return None
    return _validate_target_python(floor, "[tool.retrofy] target-python")


def _read_source_python(source_root: pathlib.Path) -> str | None:
    """Return ``[tool.retrofy] source-python`` (e.g. ``"3.15"``): the Python
    the on-disk source runs on unconverted, or ``None`` if not declared.

    Declarative: it doesn't limit which Python builds the project, since
    retrofy's hooks only parse the source.
    """
    pyproject = source_root / "pyproject.toml"
    if not pyproject.exists():
        return None
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    source = data.get("tool", {}).get("retrofy", {}).get("source-python")
    if not source:
        return None
    return _validate_target_python(source, "[tool.retrofy] source-python")


def post_load_backend(backend: object) -> None:
    """``post-load-backend`` hook (multistage-build >= 0.4).

    multistage-build calls it once the build backend is imported, before
    any PEP 517 call reaches it. Checks ``[tool.retrofy] source-python``
    against ``target-python``, then supplies a dynamic ``requires-python``
    (see :func:`supply_dynamic_requires_python`).
    """
    root = pathlib.Path.cwd()
    source = _read_source_python(root)
    target = _read_target_python(root)
    if source is not None and target is not None and Version(target) > Version(source):
        raise ValueError(
            f"[tool.retrofy] target-python ({target}) is newer than "
            f"source-python ({source}); target-python is the oldest Python "
            f"the built artefacts support.",
        )
    supply_dynamic_requires_python(backend)


def supply_dynamic_requires_python(backend: object) -> None:
    """``post-load-backend`` hook (multistage-build >= 0.4).

    When ``requires-python`` is listed in ``[project].dynamic``, supply it
    as ``>={target-python}`` from ``[tool.retrofy] target-python``: the
    built artefacts are lowered to that floor, even though the on-disk
    source needs a newer Python.

    setuptools only accepts a dynamic ``requires-python`` from ``setup.py``,
    and rejects it already in ``get_requires_for_build_*``, the first call a
    frontend makes. This hook runs before every call (multistage-build calls
    it once the backend is imported), and wraps setuptools' pyproject loader
    so that the parsed configuration of this project's ``pyproject.toml``
    carries the value. Nothing is written to disk. Other backends handle
    their dynamic fields themselves and are left alone.
    """
    root = pathlib.Path.cwd()
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    if "requires-python" not in (data.get("project", {}).get("dynamic", []) or []):
        return
    if not getattr(backend, "__name__", "").startswith("setuptools"):
        return
    floor = _read_target_python(root)
    if floor is None:
        raise ValueError(
            "requires-python is listed in [project].dynamic, which retrofy "
            "supplies from [tool.retrofy] target-python; set it, e.g. "
            'target-python = "3.9".',
        )

    # Only reachable when the backend is setuptools, which retrofy itself
    # doesn't otherwise need here.
    pyprojecttoml = importlib.import_module("setuptools.config.pyprojecttoml")
    original = getattr(pyprojecttoml, "load_file")
    if getattr(original, "_retrofy_supplies_requires_python", False):
        return  # already wrapped in this process
    target = pyproject.resolve()
    requires_python = f">={floor}"

    def load_file(filepath):  # type: ignore[no-untyped-def]
        loaded = original(filepath)
        project = loaded.get("project", {})
        dynamic = project.get("dynamic", [])
        if pathlib.Path(filepath).resolve() == target and "requires-python" in dynamic:
            project["dynamic"] = [d for d in dynamic if d != "requires-python"]
            project["requires-python"] = requires_python
        return loaded

    setattr(load_file, "_retrofy_supplies_requires_python", True)
    setattr(pyprojecttoml, "load_file", load_file)


def _validate_target_python(floor: object, where: str) -> str:
    """Return *floor* if it is a single version like ``"3.9"``; raise
    otherwise. *where* names the setting in the error messages."""
    if not isinstance(floor, str):
        raise TypeError(
            f'{where} must be a string (e.g. "3.9"), '
            f"got {type(floor).__name__} {floor!r}. Quote the value in "
            f"pyproject.toml -- TOML floats like ``3.10`` lose their "
            f"trailing zero and silently lower the floor.",
        )
    try:
        parsed = Version(floor)
    except InvalidVersion:
        parsed = None
    if parsed is None or len(parsed.release) < 2:
        raise ValueError(
            f"{where} must be a single version like "
            f'"3.9" or "3.9.7"; got {floor!r}. Range specifiers '
            f'(">=3.9", "3.9,!=3.12.*") and lone-major values ("3") are '
            f"not accepted here -- put any such exclusions in "
            f"``[project].requires-python`` instead, and retrofy will "
            f"preserve them when lowering the floor.",
        )
    return floor


def _retrofy_version() -> str:
    # Read _version.py when needed rather than ``retrofy.__version__``: when
    # retrofy builds itself, this module is imported before setuptools-scm
    # writes _version.py, and ``__init__`` falls back to a placeholder.
    return importlib.import_module(f"{__package__}._version").__version__


def _add_requires_dist(text: str, requirement: str) -> str:
    """Return ``text`` with ``Requires-Dist: {requirement}`` appended to the
    METADATA header block."""
    # PEP 566 / RFC 822: the first blank line ends the header block and
    # starts the long-description body. The new Requires-Dist must go
    # inside the header block.
    header, sep, body = text.partition("\n\n")
    header = header.rstrip("\n")
    suffix = sep + body if sep else "\n"
    return header + f"\nRequires-Dist: {requirement}" + suffix


# The first typing_extensions release providing every name retrofy falls
# back to it for (``override`` arrived in 4.4.0).
_TYPING_EXTENSIONS_MIN_VERSION = "4.4"


def _typing_extensions_requirement(floor: str) -> str | None:
    """Return the ``typing_extensions`` requirement needed on ``floor`` by
    the typing fallbacks retrofy may emit, or ``None`` if every backported
    name is in ``typing`` already.

    Derived from configuration only, not from the converted source: the
    pre-flight ``prepare_metadata_for_build_wheel`` hook must reach the same
    answer as the post-build hook without having converted anything.
    """
    newest = max(f.min_version for f in TYPING_EXTENSIONS_CONFIG.features)
    if Version(floor).release[:2] >= newest:
        return None
    return (
        f"typing_extensions>={_TYPING_EXTENSIONS_MIN_VERSION}; "
        f'python_version < "{newest[0]}.{newest[1]}"'
    )


def _splice_typing_extensions_requires_dist(text: str, floor: str) -> str:
    """Add the ``typing_extensions`` requirement for ``floor``, if any.

    Callers only do so when the project's dependencies are dynamic (PEP 621).
    With static dependencies the user declares it, and the guarded fallback
    imports name the package if it is missing at runtime.
    """
    requirement = _typing_extensions_requirement(floor)
    if requirement is None:
        return text
    if f"Requires-Dist: {requirement}" in text.partition("\n\n")[0].split("\n"):
        return text
    return _add_requires_dist(text, requirement)


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
    return _add_requires_dist(text, f"retrofy=={_retrofy_version()}")


def _rewrite_requires_python_floor(existing: str | None, floor: str) -> str:
    """Return the ``Requires-Python`` value for the lowered artefact.

    ``existing`` is the project's declared spec (from ``[project]
    .requires-python`` or the ``Requires-Python:`` METADATA line), or
    ``None`` / empty if unset. ``floor`` is the bare version from
    ``[tool.retrofy] target-python``.

    The new value is ``>={floor}`` combined with every clause from
    ``existing`` that isn't a lower bound. This preserves user
    exclusions like ``!=3.12.*`` while overwriting the ``>=`` / ``>``
    that named the pre-retrofy source floor.
    """
    keep: list[str] = []
    if existing:
        for spec in SpecifierSet(existing):
            if spec.operator not in (">=", ">"):
                keep.append(str(spec))
    keep.insert(0, f">={floor}")
    return str(SpecifierSet(",".join(keep)))


def _lower_requires_python(text: str, floor: str) -> str:
    """Return ``text`` with the ``Requires-Python:`` line in the METADATA
    header rewritten to lower the floor to ``floor`` while preserving
    any other clauses (``!=``, ``<``, ...) from the existing value. If
    no such line is present in the header, one is appended.

    Only the header block (everything before the first blank line) is
    touched -- a stray ``Requires-Python:`` mention in the long
    description body is preserved verbatim.
    """
    header, sep, body = text.partition("\n\n")
    # PEP 566 / RFC 822 headers are case-insensitive on the field name,
    # so match permissively but emit the canonical capitalisation.
    replaced = False
    out_lines = []
    for line in header.split("\n"):
        if line.lower().startswith("requires-python:"):
            existing = line.split(":", 1)[1].strip()
            new_spec = _rewrite_requires_python_floor(existing, floor)
            out_lines.append(f"Requires-Python: {new_spec}")
            replaced = True
        else:
            out_lines.append(line)
    if not replaced:
        out_lines.append(
            f"Requires-Python: {_rewrite_requires_python_floor(None, floor)}",
        )
    new_header = "\n".join(out_lines)
    return new_header + (sep + body if sep else "")


def _apply_wheel_metadata_edits(text: str) -> str:
    """Single source of truth for the ``METADATA`` transformation on the
    wheel install path.

    Called from both ``lower_metadata_requires_python`` (the
    ``post-prepare-metadata-for-build-wheel`` hook) and
    ``compatibility_via_rewrite`` (the ``post-build-wheel`` hook).
    PEP 517 requires the metadata returned by
    ``prepare_metadata_for_build_wheel`` to match what appears in the
    wheel ``build_wheel`` produces; routing both hooks through this
    function is how retrofy honours that.

    Any new ``METADATA`` edit for wheel installs goes here, not in the
    hooks.
    """
    floor = _read_target_python(pathlib.Path.cwd())
    if floor is not None:
        text = _lower_requires_python(text, floor)
        if _dependencies_are_dynamic(pathlib.Path.cwd()):
            text = _splice_typing_extensions_requires_dist(text, floor)
    return text


def _apply_editable_metadata_edits(text: str) -> str:
    """Single source of truth for the ``METADATA`` transformation on the
    editable install path.

    Called from both ``inject_runtime_requirement`` (the
    ``post-prepare-metadata-for-build-editable`` hook) and
    ``compatibility_via_import_hook`` (the ``post-build-editable``
    hook). PEP 660 requires the metadata returned by
    ``prepare_metadata_for_build_editable`` to match what appears in
    the editable wheel; routing both hooks through this function is
    how retrofy honours that.

    Any new ``METADATA`` edit for editable installs goes here, not in
    the hooks.
    """
    dynamic = _dependencies_are_dynamic(pathlib.Path.cwd())
    if dynamic:
        text = _splice_retrofy_requires_dist(text)
    floor = _read_target_python(pathlib.Path.cwd())
    if floor is not None:
        text = _lower_requires_python(text, floor)
        if dynamic:
            text = _splice_typing_extensions_requires_dist(text, floor)
    return text


def inject_runtime_requirement(dist_info_path: pathlib.Path) -> None:
    """``post-prepare-metadata-for-build-editable`` hook.

    Runs the editable-install METADATA policy (see
    ``_apply_editable_metadata_edits``) on the prepared ``METADATA`` so
    the frontend sees the final field values before deciding whether
    to invoke ``build_editable``.
    """
    metadata_path = dist_info_path / "METADATA"
    text = metadata_path.read_text(encoding="utf-8")
    new_text = _apply_editable_metadata_edits(text)
    if new_text != text:
        metadata_path.write_text(new_text, encoding="utf-8")


def lower_metadata_requires_python(dist_info_path: pathlib.Path) -> None:
    """``post-prepare-metadata-for-build-wheel`` hook.

    Runs the wheel-install METADATA policy (see
    ``_apply_wheel_metadata_edits``) on the prepared ``METADATA`` so
    the frontend sees the final field values before deciding whether
    to invoke ``build_wheel``.
    """
    metadata_path = dist_info_path / "METADATA"
    text = metadata_path.read_text(encoding="utf-8")
    new_text = _apply_wheel_metadata_edits(text)
    if new_text != text:
        metadata_path.write_text(new_text, encoding="utf-8")


def _editable_pth_name(pkg: str) -> str:
    return f"_retrofy.__editable_compat__.{pkg}.pth"


def _editable_pth_script(pkg: str) -> str:
    """The ``.pth`` line which registers the import-time converter for
    ``pkg``. If retrofy cannot be imported, importing ``pkg`` (or any of its
    submodules) instead raises an ``ImportError`` saying how to fix it;
    interpreter startup, and other imports (e.g. pip, to install retrofy),
    are unaffected.
    """
    # site execs the line in a function (with separate globals and locals),
    # so the class and function bodies below only use their arguments and
    # literals: names bound here (e.g. exc) aren't visible to them.
    code = textwrap.dedent(f"""\
        try:
            import retrofy._meta_hook_converter as c
        except ImportError as exc:
            class RetrofyMissingFinder:
                retrofy_missing_package = {pkg!r}

                def __init__(self, message):
                    self.message = message

                def find_spec(self, name, path=None, target=None):
                    if name == {pkg!r} or name.startswith({pkg!r} + "."):
                        raise ImportError(self.message, name=name)
                    return None

            # (site may run this line more than once.)
            if not any(
                getattr(finder, "retrofy_missing_package", None) == {pkg!r}
                for finder in sys.meta_path
            ):
                sys.meta_path.insert(0, RetrofyMissingFinder(
                    {pkg!r} + " is installed in editable mode, which needs "
                    "retrofy to make its code compatible at import time, but "
                    "retrofy could not be imported (" + str(exc) + "). Install "
                    "retrofy (pip install retrofy), or see retrofy's README for "
                    "editable installs on Python 3.7/3.8."
                ))
        else:
            c.register_hook([{pkg!r}])
    """)
    # site runs a .pth line (only) if it starts with "import".
    return f"import sys; exec({code!r})\n"


def compatibility_via_import_hook(wheel: pathlib.Path):
    """
    Add a pth hook to ensure make imported code compatible at import time
    (i.e. suitable for editable mode)

    """
    if not _dependencies_are_dynamic(pathlib.Path.cwd()):
        _log.warning(
            "The dependencies are static (not in [project].dynamic), so retrofy "
            "is not added to those of the editable install: install retrofy "
            "yourself, or list dependencies in "
            "[project].dynamic. Without retrofy, the package fails to import.",
        )

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
            whl.write(
                zipfile.ZipInfo(_editable_pth_name(pkg)),
                _editable_pth_script(pkg),
            )

        # PEP 660: the METADATA in the built editable wheel must match
        # what ``prepare_metadata_for_build_editable`` returned. Route
        # the edit through ``_apply_editable_metadata_edits`` so this
        # hook and the pre-flight ``inject_runtime_requirement`` share
        # one policy definition.
        metadata_name = whl.dist_info_dirname() + "/METADATA"
        metadata_text = whl.read(metadata_name).decode("utf-8")
        new_metadata = _apply_editable_metadata_edits(metadata_text)
        if new_metadata != metadata_text:
            whl.write(metadata_name, new_metadata)

        with wheel.open("wb") as whl_fh:
            whl.write_wheel(whl_fh)

    _log.info("Enabling automatic retrofiting of Python code at import-time")

    editable_copy.unlink()


def _runtime_payload_dirs(
    lazy_runtime_dirs: set[str],
    existing_entries: set[str],
) -> list[str]:
    """The ``_retrofy_rt`` directories (``/``-separated, relative to the
    artefact root) to inject, one beside each converted module that imports
    from it.

    The canonical home of the runtime is ``retrofy/_retrofy_rt/``: when the
    artefact is retrofy's own, the converted module's sibling already holds
    the payload byte-for-byte, so it is skipped. Anything else already
    under a ``_retrofy_rt`` name would be silently shadowed, so it is an
    error: ``_retrofy_rt`` is retrofy's reserved namespace inside converted
    packages.
    """
    targets = []
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
        targets.append(target_dir)
    return targets


def _convert_wheel(
    source: pathlib.Path,
    destination: pathlib.Path,
    edit_metadata: typing.Callable[[str], str],
) -> bool:
    """Write a converted copy of the wheel *source* to *destination* (which
    may be the same path): lowered source, the ``_retrofy_rt`` runtime where
    needed, and METADATA passed through *edit_metadata*. RECORD is rehashed.

    Returns whether anything changed; if not, *destination* is not written.
    """
    has_modifications = False
    # Directories within the wheel whose converted modules emitted a
    # ``from ._retrofy_rt.lazy_imports import (...)`` line.
    lazy_runtime_dirs: set[str] = set()
    # Read from a copy, so that *destination* may be *source*.
    with tempfile.TemporaryDirectory() as tmpdir:
        source_copy = pathlib.Path(tmpdir) / source.name
        shutil.copy(source, source_copy)
        with zipfile.ZipFile(str(source_copy), "r") as whl_zip:
            whl = WheelModifier(whl_zip)
            existing_entries = set(whl_zip.namelist())

            for filename in whl_zip.namelist():
                if filename.endswith(".py"):
                    code = whl.read(filename).decode("utf-8")
                    new_code = convert(code)
                    if new_code != code:
                        _log.info("Converted %s to compatibility syntax", filename)
                        whl.write(filename, new_code.encode("utf-8"))
                        has_modifications = True
                        if _LAZY_RUNTIME_IMPORT_MARKER in new_code:
                            lazy_runtime_dirs.add(posixpath.dirname(filename))

            payload_dirs = _runtime_payload_dirs(lazy_runtime_dirs, existing_entries)
            if payload_dirs:
                payload = _embedded_runtime_files()
                for target_dir in payload_dirs:
                    for relpath, data in payload.items():
                        # WheelModifier.write needs a ZipInfo for entries
                        # not already in the source wheel.
                        whl.write(zipfile.ZipInfo(f"{target_dir}/{relpath}"), data)
                    _log.info("Injected retrofy runtime payload into %s/", target_dir)
                has_modifications = True

            metadata_name = whl.dist_info_dirname() + "/METADATA"
            metadata_text = whl.read(metadata_name).decode("utf-8")
            new_metadata = edit_metadata(metadata_text)
            if new_metadata != metadata_text:
                whl.write(metadata_name, new_metadata.encode("utf-8"))
                _log.info("Applied METADATA edits to %s", metadata_name)
                has_modifications = True

            if has_modifications:
                with destination.open("wb") as whl_fh:
                    whl.write_wheel(whl_fh)
    return has_modifications


def compatibility_via_rewrite(wheel: pathlib.Path):
    """``post-build-wheel`` hook: make the built wheel compatible, in place."""
    if os.environ.get("RETROFY_DISABLE_REWRITE") == "1":
        # Escape hatch: build a wheel of the raw (unconverted) source,
        # keeping the hook installed but making it a no-op.
        _log.info("RETROFY_DISABLE_REWRITE=1 — skipping wheel rewrite")
        return
    # PEP 517: the METADATA in the built wheel must match what
    # ``prepare_metadata_for_build_wheel`` returned. Route the edit
    # through ``_apply_wheel_metadata_edits`` so this hook and the
    # pre-flight ``lower_metadata_requires_python`` share one policy
    # definition.
    _convert_wheel(wheel, wheel, _apply_wheel_metadata_edits)


def _strip_top_level(member_name: str) -> tuple[str, str]:
    """Return ``(top_level, relpath)`` for a tar member name.

    Sdists are canonically packaged as ``name-version/...`` -- one
    top-level directory containing everything. We don't accept anything
    else.
    """
    head, _, rest = member_name.partition("/")
    return head, rest


def _build_requires_drop_retrofy(requires: list[str]) -> list[str]:
    """Return ``requires`` with any entry whose PEP 503-normalised name is
    ``retrofy`` removed. Entries that are not parseable as PEP 508
    requirements are passed through unchanged.
    """
    out: list[str] = []
    for spec in requires:
        try:
            req = Requirement(spec)
        except InvalidRequirement:
            out.append(spec)
            continue
        if canonicalize_name(req.name) == "retrofy":
            continue
        out.append(spec)
    return out


def _patch_pyproject_for_lowered_sdist(text: str, floor: str) -> str:
    """Patch a sdist's ``pyproject.toml`` for the lowered sdist:

    - rewrite ``[project].requires-python`` so its lower bound is
      ``>={floor}``, preserving other clauses (``!=``, ``<``, ...) from
      the source spec; if ``requires-python`` was in
      ``[project].dynamic``, drop it.
    - drop ``retrofy`` from ``[build-system].requires`` (PEP 503
      normalised match).
    - drop ``[tool.retrofy] source-python``: the sdist's source has been
      lowered to ``target-python``.

    Uses ``tomlkit`` for a round-trip edit that preserves comments and
    formatting wherever it doesn't conflict with the edit.
    """
    doc = tomlkit.parse(text)

    build_system = doc.get("build-system")
    if build_system is not None:
        requires = build_system.get("requires")
        if requires is not None:
            new_requires = _build_requires_drop_retrofy(list(requires))
            if new_requires != list(requires):
                build_system["requires"] = new_requires

    retrofy_config = doc.get("tool", {}).get("retrofy")
    if retrofy_config is not None and "source-python" in retrofy_config:
        del retrofy_config["source-python"]

    project = doc.get("project")
    if project is not None:
        dynamic = project.get("dynamic")
        if dynamic is not None and "requires-python" in dynamic:
            project["dynamic"] = [d for d in dynamic if d != "requires-python"]
        existing = project.get("requires-python")
        project["requires-python"] = _rewrite_requires_python_floor(
            str(existing) if existing else None,
            floor,
        )

    return tomlkit.dumps(doc)


def _convert_directory(root: pathlib.Path) -> bool:
    """Convert the ``.py`` files under *root* in place, and add the
    ``_retrofy_rt`` runtime beside converted modules that need it.

    Returns whether anything changed.
    """
    existing_entries = {
        p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
    }
    has_modifications = False
    lazy_runtime_dirs: set[str] = set()
    for py in sorted(root.rglob("*.py")):
        text = py.read_text(encoding="utf-8")
        new_text = convert(text)
        if new_text != text:
            py.write_text(new_text, encoding="utf-8")
            _log.info("Converted %s to compatibility syntax", py.relative_to(root))
            has_modifications = True
            if _LAZY_RUNTIME_IMPORT_MARKER in new_text:
                lazy_runtime_dirs.add(
                    posixpath.dirname(py.relative_to(root).as_posix()),
                )

    payload_dirs = _runtime_payload_dirs(lazy_runtime_dirs, existing_entries)
    if payload_dirs:
        payload = _embedded_runtime_files()
        for target_dir in payload_dirs:
            inject_dir = root / target_dir
            inject_dir.mkdir(parents=True, exist_ok=True)
            for relpath, data in payload.items():
                (inject_dir / relpath).write_bytes(data)
            _log.info("Injected retrofy runtime payload into %s/", target_dir)
        has_modifications = True
    return has_modifications


def lower_sdist(sdist_path: pathlib.Path) -> None:
    """``post-build-sdist`` hook.

    Mirrors the wheel hook (``compatibility_via_rewrite``): source
    conversion runs unconditionally, ``_retrofy_rt/`` is injected
    wherever converted source references it, and the
    ``Requires-Python`` / ``[build-system].requires`` rewrites are
    gated on ``[tool.retrofy] target-python``.

    ``RETROFY_DISABLE_REWRITE=1`` short-circuits to a no-op, matching the
    wheel hook.
    """
    if os.environ.get("RETROFY_DISABLE_REWRITE") == "1":
        _log.info("RETROFY_DISABLE_REWRITE=1 — skipping sdist rewrite")
        return
    floor = _read_target_python(pathlib.Path.cwd())

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
            # Tracked in pelson/retrofy#39 -- candidate for a retrofy
            # converter that emits this fallback automatically.
            if sys.version_info >= (3, 12):
                tar.extractall(tmp, filter="data")
            else:
                tar.extractall(tmp)  # noqa: S202  # see comment above

        root = tmp / top
        # Convert sources unconditionally -- the same modern syntax that
        # the wheel hook lowers is unparseable on any older Python, so
        # source lowering is universally useful even when the project
        # has not opted into metadata lowering via ``target-python``.
        has_modifications = _convert_directory(root)

        # Metadata lowering is opt-in via ``[tool.retrofy] target-python``.
        if floor is not None:
            pyproj = root / "pyproject.toml"
            if pyproj.exists():
                text = pyproj.read_text(encoding="utf-8")
                new_text = _patch_pyproject_for_lowered_sdist(text, floor)
                if new_text != text:
                    pyproj.write_text(new_text, encoding="utf-8")
                    has_modifications = True
            pkg_info = root / "PKG-INFO"
            if pkg_info.exists():
                text = pkg_info.read_text(encoding="utf-8")
                new_text = _lower_requires_python(text, floor)
                if new_text != text:
                    pkg_info.write_text(new_text, encoding="utf-8")
                    has_modifications = True

        if not has_modifications:
            return

        # Repack. Both the tar member order and the per-entry
        # tarinfo metadata (mtime, uid, gid, uname, gname, mode) affect
        # the sdist byte content, and so does the gzip mtime header.
        # Walk sorted so member order is stable across filesystems, use
        # a tarfile filter to normalise per-entry metadata, and open
        # the gzip stream with a fixed mtime. SOURCE_DATE_EPOCH is
        # honoured for the mtime when set (PEP 517-compatible frontends
        # use it to opt into reproducible builds); otherwise fall back
        # to 0.
        source_date_epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))

        def _normalise(info: tarfile.TarInfo) -> tarfile.TarInfo:
            info.mtime = source_date_epoch
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            # 0755 for dirs, 0644 for files, mirroring what setuptools
            # emits and what pip's sdist unpack expects.
            info.mode = 0o755 if info.isdir() else 0o644
            return info

        tmp_sdist = sdist_path.with_suffix(sdist_path.suffix + ".tmp")
        with (
            open(tmp_sdist, "wb") as raw,
            gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=raw,
                mtime=source_date_epoch,
            ) as gz,
            tarfile.open(fileobj=gz, mode="w") as tar,
        ):
            tar.add(root, arcname=top, recursive=False, filter=_normalise)
            for path in sorted(root.rglob("*")):
                arc = f"{top}/{path.relative_to(root).as_posix()}"
                tar.add(path, arcname=arc, recursive=False, filter=_normalise)
        os.replace(tmp_sdist, sdist_path)
