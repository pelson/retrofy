from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple

import libcst as cst

from .import_utils import EnhancedImportManager


@dataclass
class FeatureInfo:
    """Information about a typing feature that needs transformation."""

    name: str
    alias: str
    import_style: str  # "from_typing" or "typing_dot"
    version_requirement: Tuple[int, int]  # e.g., (3, 8) or (3, 10)


class TypingExtensionsTransformer(cst.CSTTransformer):
    """
    Transform typing features to use typing_extensions for compatibility.

    This transformation handles:
    - typing.Literal -> typing_extensions.Literal (Python < 3.8)
    - typing.get_args -> typing_extensions.get_args (Python < 3.10)
    - typing.get_origin -> typing_extensions.get_origin (Python < 3.10)
    """

    def __init__(self) -> None:
        self.import_manager = EnhancedImportManager()
        self.features: Dict[str, FeatureInfo] = {}

    def leave_Module(
        self,
        original_node: cst.Module,
        updated_node: cst.Module,
    ) -> cst.Module:
        """Transform the entire module to add version compatibility checks."""
        # Scan for imports to detect usage and aliases
        self.import_manager.scan_imports(updated_node.body)
        self._detect_typing_usage()

        if not self.features:
            return updated_node

        # Start with the updated body as a list
        new_body = list(updated_node.body)

        # Remove from existing imports if needed
        for feature in self.features.values():
            if feature.import_style == "from_typing":
                new_body = self.import_manager.remove_from_imports(
                    new_body,
                    "typing",
                    feature.name,
                )

        # Ensure sys import is present
        new_body = self.import_manager.ensure_sys_import(new_body)

        # Group features by version requirement
        version_groups = self._group_features_by_version()

        # Add version checks after imports
        version_check_position = self.import_manager.find_post_import_position(new_body)

        for version_requirement in sorted(version_groups.keys()):
            features_for_version = version_groups[version_requirement]
            version_check = self._create_grouped_version_check(
                version_requirement,
                features_for_version,
            )
            new_body.insert(version_check_position, version_check)
            version_check_position += 1

        return updated_node.with_changes(body=new_body)

    def _detect_typing_usage(self) -> None:
        """Detect if typing features are imported and determine usage style."""
        if self.import_manager.has_import("typing", "Literal"):
            alias = (
                self.import_manager.get_import_alias("typing", "Literal") or "Literal"
            )
            self.features["Literal"] = FeatureInfo(
                name="Literal",
                alias=alias,
                import_style="from_typing",
                version_requirement=(3, 8),
            )

        if self.import_manager.has_import("typing", "get_args"):
            alias = (
                self.import_manager.get_import_alias("typing", "get_args") or "get_args"
            )
            self.features["get_args"] = FeatureInfo(
                name="get_args",
                alias=alias,
                import_style="from_typing",
                version_requirement=(3, 10),
            )

        if self.import_manager.has_import("typing", "get_origin"):
            alias = (
                self.import_manager.get_import_alias("typing", "get_origin")
                or "get_origin"
            )
            self.features["get_origin"] = FeatureInfo(
                name="get_origin",
                alias=alias,
                import_style="from_typing",
                version_requirement=(3, 10),
            )

    def _group_features_by_version(self) -> Dict[Tuple[int, int], List[FeatureInfo]]:
        """Group features by their version requirements."""
        groups = defaultdict(list)
        for feature in self.features.values():
            groups[feature.version_requirement].append(feature)
        return dict(groups)

    def leave_Subscript(
        self,
        original_node: cst.Subscript,
        updated_node: cst.Subscript,
    ) -> cst.Subscript:
        """Transform typing.Literal[...] to use the version-compatible alias."""
        if (
            isinstance(updated_node.value, cst.Attribute)
            and isinstance(updated_node.value.value, cst.Name)
            and updated_node.value.value.value == "typing"
            and updated_node.value.attr.value == "Literal"
        ):
            self.features["Literal"] = FeatureInfo(
                name="Literal",
                alias="__typing_Literal",
                import_style="typing_dot",
                version_requirement=(3, 8),
            )
            return updated_node.with_changes(value=cst.Name("__typing_Literal"))

        return updated_node

    def leave_Call(
        self,
        original_node: cst.Call,
        updated_node: cst.Call,
    ) -> cst.Call:
        """Transform typing.get_args() and typing.get_origin() calls."""
        # Check for typing.get_args
        if (
            isinstance(updated_node.func, cst.Attribute)
            and isinstance(updated_node.func.value, cst.Name)
            and updated_node.func.value.value == "typing"
            and updated_node.func.attr.value == "get_args"
        ):
            self.features["get_args"] = FeatureInfo(
                name="get_args",
                alias="__typing_get_args",
                import_style="typing_dot",
                version_requirement=(3, 10),
            )
            return updated_node.with_changes(func=cst.Name("__typing_get_args"))

        # Check for typing.get_origin
        if (
            isinstance(updated_node.func, cst.Attribute)
            and isinstance(updated_node.func.value, cst.Name)
            and updated_node.func.value.value == "typing"
            and updated_node.func.attr.value == "get_origin"
        ):
            self.features["get_origin"] = FeatureInfo(
                name="get_origin",
                alias="__typing_get_origin",
                import_style="typing_dot",
                version_requirement=(3, 10),
            )
            return updated_node.with_changes(func=cst.Name("__typing_get_origin"))

        return updated_node

    def _create_grouped_version_check(
        self,
        version_requirement: Tuple[int, int],
        features: List[FeatureInfo],
    ) -> cst.If:
        """Create a grouped version check block for multiple features with the same version requirement."""
        # Create the condition: sys.version_info >= version_requirement
        condition = cst.Comparison(
            left=cst.Attribute(
                value=cst.Name("sys"),
                attr=cst.Name("version_info"),
            ),
            comparisons=[
                cst.ComparisonTarget(
                    operator=cst.GreaterThanEqual(),
                    comparator=cst.Tuple(
                        [
                            cst.Element(cst.Integer(str(version_requirement[0]))),
                            cst.Element(cst.Integer(str(version_requirement[1]))),
                        ],
                    ),
                ),
            ],
        )

        # Create import aliases for all features
        import_aliases = [
            self._create_import_alias(feature.name, feature.alias)
            for feature in features
        ]

        if_body = self._create_typing_import_body(import_aliases)
        else_body = self._create_typing_extensions_import_body(import_aliases)

        return self._create_conditional_block(condition, if_body, else_body)

    def _create_import_alias(self, name: str, alias: str) -> cst.ImportAlias:
        """Create an import alias for the given name and alias."""
        if name == alias:
            return cst.ImportAlias(name=cst.Name(name))
        else:
            return cst.ImportAlias(
                name=cst.Name(name),
                asname=cst.AsName(name=cst.Name(alias)),
            )

    def _create_typing_import_body(
        self,
        imports: list[cst.ImportAlias],
    ) -> cst.IndentedBlock:
        """Create import body for typing module."""
        return cst.IndentedBlock(
            [
                cst.SimpleStatementLine(
                    [
                        cst.ImportFrom(
                            module=cst.Name("typing"),
                            names=imports,
                        ),
                    ],
                ),
            ],
        )

    def _create_typing_extensions_import_body(
        self,
        imports: list[cst.ImportAlias],
    ) -> cst.IndentedBlock:
        """Create import body for typing_extensions module."""
        return cst.IndentedBlock(
            [
                cst.SimpleStatementLine(
                    [
                        cst.ImportFrom(
                            module=cst.Name("typing_extensions"),
                            names=imports,
                        ),
                    ],
                ),
            ],
        )

    def _create_conditional_block(
        self,
        condition: cst.Comparison,
        if_body: cst.IndentedBlock,
        else_body: cst.IndentedBlock,
    ) -> cst.If:
        """Create a conditional import block."""
        return cst.If(
            test=condition,
            body=if_body,
            orelse=cst.Else(body=else_body),
            leading_lines=[
                cst.EmptyLine(
                    whitespace=cst.SimpleWhitespace(""),
                    comment=None,
                ),
            ],
        )
