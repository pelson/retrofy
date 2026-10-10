"""PEP 585 built-in generics backport (``list[int]`` -> ``typing.List[int]``).

PEP 585 made the built-in collection types runtime-subscriptable in Python
3.9. On 3.7/3.8, ``list[int]`` raises ``TypeError: 'type' object is not
subscriptable`` wherever it is evaluated (e.g. a module-level alias, or an
annotation without ``from __future__ import annotations``). This
transformation rewrites subscripts of the built-ins to the equivalent
:mod:`typing` generics, adding ``import typing`` where needed.

Names which do not resolve to the built-in (e.g. a parameter named ``dict``,
or an imported ``list``) are left untouched.
"""

from __future__ import annotations

import dataclasses

import libcst as cst
from libcst.metadata import QualifiedNameProvider, QualifiedNameSource

from .import_utils import EnhancedImportManager

_FALLBACKS = {
    "list": "List",
    "dict": "Dict",
    "tuple": "Tuple",
    "set": "Set",
    "frozenset": "FrozenSet",
    "type": "Type",
}


class _BuiltinGenericsTransformer(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (QualifiedNameProvider,)

    def __init__(self) -> None:
        super().__init__()
        self._require_typing = False

    def leave_Subscript(
        self,
        original_node: cst.Subscript,
        updated_node: cst.Subscript,
    ) -> cst.Subscript:
        # Only a subscripted built-in (``list[...]``) is rewritten: other uses
        # (``list()``, ``isinstance(x, list)``, ``mapping[list]``) are not
        # visited here, or are not the value being subscripted.
        value = original_node.value
        if not isinstance(value, cst.Name) or value.value not in _FALLBACKS:
            return updated_node
        names = self.get_metadata(QualifiedNameProvider, value, set())
        if not any(
            name.source == QualifiedNameSource.BUILTIN
            and name.name == f"builtins.{value.value}"
            for name in names
        ):
            return updated_node
        self._require_typing = True
        return updated_node.with_changes(
            value=cst.Attribute(cst.Name("typing"), cst.Name(_FALLBACKS[value.value])),
        )

    def leave_Module(
        self,
        original_node: cst.Module,
        updated_node: cst.Module,
    ) -> cst.Module:
        if not self._require_typing:
            return updated_node
        import_manager = EnhancedImportManager()
        import_manager.scan_imports(updated_node.body)
        body = import_manager.ensure_direct_import(list(updated_node.body), "typing")
        return dataclasses.replace(updated_node, body=tuple(body))


def convert(module: cst.Module) -> cst.Module:
    return cst.MetadataWrapper(module).visit(_BuiltinGenericsTransformer())
