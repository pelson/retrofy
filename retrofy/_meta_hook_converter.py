from __future__ import annotations

from importlib.abc import Loader, MetaPathFinder, SourceLoader
from importlib.machinery import ModuleSpec
from pathlib import Path
import sys
import typing

lazy from ._converters import convert

# ``_converters`` pulls libcst, which does not install on 3.7/3.8;
# ``_editable_converter_client`` is only used on those hosts. Both
# are lazy so each branch in ``get_data`` only pays for its own deps.
lazy from ._editable_converter_client import get_worker

_HOST_NEEDS_WORKER = sys.version_info < (3, 9)

# Name of the sub-package retrofy injects into every converted package
# that needs runtime helpers. Reserved — converters must never emit
# code that imports from anything else under this name.
_RUNTIME_SUBPKG = "_retrofy"


class OnTheFlyConverter(SourceLoader):
    def __init__(self, path):
        self.path = path

    def get_filename(self, fullname):
        return self.path

    def get_data(self, filename):
        """exec_module is already defined for us, we just have to provide a way
        of getting the source code of the module"""
        if _HOST_NEEDS_WORKER:
            return get_worker().convert(filename)

        with open(filename) as f:
            data = f.read()
        return convert(data)


class _EmbeddedRuntimeLoader(Loader):
    """Serve a single payload file as a module's source.

    Used by :class:`MyMetaPathFinder` to synthesise the ``_retrofy``
    sub-package (and its modules) inside a converted package whose code
    was rewritten on-the-fly — there is no ``_retrofy/`` directory on
    disk to import from in the editable / pytest paths, so we hand the
    payload bytes straight to the import machinery.
    """

    def __init__(self, source: bytes, filename: str) -> None:
        self._source = source
        self._filename = filename

    def create_module(self, spec):  # noqa: ARG002 - default semantics
        return None

    def exec_module(self, module):
        code = compile(self._source, self._filename, "exec")
        exec(code, module.__dict__)

    def get_source(self, fullname):  # noqa: ARG002 - linecache hook
        return self._source.decode("utf-8")

    def get_filename(self, fullname):  # noqa: ARG002
        return self._filename


def _embedded_runtime_root() -> Path:
    # Derived from ``__file__`` so the lookup works both when this
    # module lives under retrofy itself and when it has been bundled
    # under a different parent (see ``retrofy setup-editable``).
    # importlib.resources.files is 3.9+, so a plain Path is also the
    # only thing portable to 3.7/3.8 hosts.
    return Path(__file__).parent / "_embedded_runtime" / "_retrofy"


def _embedded_runtime_source(modname: str) -> typing.Tuple[bytes, str] | None:
    """Return ``(source_bytes, synthetic_filename)`` for the payload
    module *modname* (e.g. ``"lazy_runtime"`` or ``""`` for the
    package's own ``__init__``), or ``None`` if it doesn't exist."""
    root = _embedded_runtime_root()
    if modname == "":
        target = root / "__init__.py"
    else:
        target = root / f"{modname}.py"
    if not target.is_file():
        return None
    return target.read_bytes(), f"<retrofy-payload:{target.name}>"


class MyMetaPathFinder(MetaPathFinder):
    def __init__(self, package_names: typing.Sequence[str] = ()):
        self.package_names: set[str] = set()
        self.add_package_handling(package_names)

    def add_package_handling(self, package_names: typing.Sequence[str]):
        self.package_names.update(package_names)

    def _is_handled_module(self, fullname: str) -> bool:
        for prefix in self.package_names:
            if fullname == prefix or fullname.startswith(f"{prefix}."):
                return True
        return False

    def _embedded_runtime_spec(self, fullname: str) -> typing.Optional[ModuleSpec]:
        """Synthesise a spec for the reserved ``_retrofy`` sub-package
        (or any module beneath it) inside a converted package.

        Converted modules emit ``from ._retrofy.lazy_runtime import
        ...``; in the editable / pytest paths there is no
        ``_retrofy/`` directory on disk, so this finder serves the
        payload sources directly from retrofy's own install.
        """
        parts = fullname.split(".")
        if _RUNTIME_SUBPKG not in parts:
            return None
        idx = parts.index(_RUNTIME_SUBPKG)
        parent = ".".join(parts[:idx])
        if not parent or not self._is_handled_module(parent):
            return None

        tail = parts[idx + 1 :]
        if len(tail) == 0:
            # ``<parent>._retrofy`` — the package itself.
            payload = _embedded_runtime_source("")
            if payload is None:
                return None
            source, filename = payload
            loader = _EmbeddedRuntimeLoader(source, filename)
            spec = ModuleSpec(fullname, loader, origin=filename, is_package=True)
            # No on-disk submodule search location — submodules are
            # served via this same finder.
            spec.submodule_search_locations = []
            return spec

        if len(tail) == 1:
            payload = _embedded_runtime_source(tail[0])
            if payload is None:
                return None
            source, filename = payload
            loader = _EmbeddedRuntimeLoader(source, filename)
            return ModuleSpec(fullname, loader, origin=filename)

        # Nested under ``_retrofy.<x>.<y>...`` — payload is flat for
        # now, nothing to serve.
        return None

    def find_spec(self, fullname, path, target=None):
        if not self._is_handled_module(fullname):
            return None

        payload_spec = self._embedded_runtime_spec(fullname)
        if payload_spec is not None:
            return payload_spec

        # If your custom logic doesn't handle it, defer to the next finder
        for finder in sys.meta_path:
            if isinstance(finder, MyMetaPathFinder):
                continue

            spec = finder.find_spec(fullname, path, target)
            if spec:
                break
        else:
            return None

        spec.loader = OnTheFlyConverter(spec.origin)
        return spec


class RetrofyRuntimeFinder(MetaPathFinder):
    """Permissive synthesiser for the reserved ``_retrofy`` sub-package.

    Unlike :class:`MyMetaPathFinder`, this finder is not scoped to a
    registered set of packages. It serves ``<any_pkg>._retrofy[.X]``
    from the canonical payload tree whenever the import machinery
    asks. Designed to be appended to the *end* of ``sys.meta_path``
    so that an on-disk ``_retrofy`` directory (the wheel-build payload
    drop) takes precedence over the synthesis.

    Used by the pytest plugin, where test files are converted on the
    fly but live in user packages we never explicitly register a hook
    for.
    """

    def find_spec(self, fullname, path, target=None):  # noqa: ARG002
        parts = fullname.split(".")
        if _RUNTIME_SUBPKG not in parts:
            return None
        idx = parts.index(_RUNTIME_SUBPKG)
        if idx == 0:
            # Top-level ``_retrofy`` is not ours — only the sub-package
            # form (``somepkg._retrofy``) is reserved.
            return None
        tail = parts[idx + 1 :]
        if len(tail) == 0:
            payload = _embedded_runtime_source("")
        elif len(tail) == 1:
            payload = _embedded_runtime_source(tail[0])
        else:
            return None
        if payload is None:
            return None
        source, filename = payload
        loader = _EmbeddedRuntimeLoader(source, filename)
        if not tail:
            spec = ModuleSpec(fullname, loader, origin=filename, is_package=True)
            spec.submodule_search_locations = []
            return spec
        return ModuleSpec(fullname, loader, origin=filename)


def register_runtime_synthesiser() -> None:
    """Ensure exactly one :class:`RetrofyRuntimeFinder` sits at the end
    of ``sys.meta_path``. Idempotent."""
    for finder in sys.meta_path:
        if isinstance(finder, RetrofyRuntimeFinder):
            return
    sys.meta_path.append(RetrofyRuntimeFinder())


def register_hook(package_names):
    for finder in sys.meta_path:
        if isinstance(finder, MyMetaPathFinder):
            existing_hook = finder
            break
    else:
        existing_hook = MyMetaPathFinder()
        sys.meta_path.insert(0, MyMetaPathFinder(package_names))

    existing_hook.add_package_handling(package_names)
