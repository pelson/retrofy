from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

import libcst as cst

from .import_utils import EnhancedImportManager


@dataclass
class FeatureInfo:
    """Information about a typing feature that needs transformation."""

    name: str
    alias: str
    import_style: str  # "from_typing" or "typing_dot"
    version_requirement: Tuple[int, int]  # e.g., (3, 8) or (3, 10)


@dataclass
class NestedImportContext:
    """Information about a nested import location."""

    import_node: cst.ImportFrom
    parent_node: cst.CSTNode
    features: List[FeatureInfo]


class TypingAnalysisVisitor(cst.CSTVisitor):
    """Visitor to analyze typing usage and detect nested imports."""

    def __init__(self) -> None:
        self.import_manager = EnhancedImportManager()
        self.features: Dict[str, FeatureInfo] = {}
        self.nested_imports: List[NestedImportContext] = []
        self._current_context_stack: List[cst.CSTNode] = []
        self._nested_typing_dot_features: Dict[cst.CSTNode, List[FeatureInfo]] = {}

    def visit_Module(self, node: cst.Module) -> None:
        """Scan imports at module level."""
        self.import_manager.scan_imports(node.body)
        self._detect_typing_usage()

    def visit_If(self, node: cst.If) -> bool:
        """Track if blocks for nested import detection."""
        self._current_context_stack.append(node)
        return True

    def leave_If(self, original_node: cst.If) -> None:
        """Clean up context stack."""
        if (
            self._current_context_stack
            and self._current_context_stack[-1] is original_node
        ):
            self._current_context_stack.pop()

    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool:
        """Detect nested imports that need transformation."""
        if (
            self._current_context_stack
            and isinstance(node.module, cst.Name)
            and node.module.value == "typing"
        ):
            # This is a nested from typing import
            nested_features = []

            if isinstance(node.names, cst.ImportStar):
                return True  # Skip star imports

            # Handle both tuple and list cases
            if isinstance(node.names, (list, tuple)):
                names = node.names
            else:
                names = [node.names]

            for name_item in names:
                if isinstance(name_item, cst.ImportAlias):
                    feature_name = name_item.name.value
                    alias = (
                        name_item.asname.name.value
                        if name_item.asname
                        else feature_name
                    )

                    # Check if this is a transformable feature
                    version_req = self._get_version_requirement(feature_name)
                    if version_req:
                        feature_info = FeatureInfo(
                            name=feature_name,
                            alias=alias,
                            import_style="from_typing",
                            version_requirement=version_req,
                        )
                        nested_features.append(feature_info)

            if nested_features:
                context = NestedImportContext(
                    import_node=node,
                    parent_node=self._current_context_stack[-1],
                    features=nested_features,
                )
                self.nested_imports.append(context)

        return True

    def visit_Subscript(self, node: cst.Subscript) -> bool:
        """Detect typing.Literal[...] usage."""
        if (
            isinstance(node.value, cst.Attribute)
            and isinstance(node.value.value, cst.Name)
            and node.value.value.value == "typing"
            and node.value.attr.value == "Literal"
        ):
            feature = FeatureInfo(
                name="Literal",
                alias="Literal",
                import_style="typing_dot",
                version_requirement=(3, 8),
            )
            self._add_typing_dot_feature("Literal", feature)
        return True

    def visit_Call(self, node: cst.Call) -> bool:
        """Detect typing.get_args() and typing.get_origin() calls."""
        if (
            isinstance(node.func, cst.Attribute)
            and isinstance(node.func.value, cst.Name)
            and node.func.value.value == "typing"
        ):
            if node.func.attr.value == "get_args":
                feature = FeatureInfo(
                    name="get_args",
                    alias="get_args",
                    import_style="typing_dot",
                    version_requirement=(3, 10),
                )
                self._add_typing_dot_feature("get_args", feature)
            elif node.func.attr.value == "get_origin":
                feature = FeatureInfo(
                    name="get_origin",
                    alias="get_origin",
                    import_style="typing_dot",
                    version_requirement=(3, 10),
                )
                self._add_typing_dot_feature("get_origin", feature)
        return True

    def visit_Decorator(self, node: cst.Decorator) -> bool:
        """Detect @typing.final decorator usage."""
        if (
            isinstance(node.decorator, cst.Attribute)
            and isinstance(node.decorator.value, cst.Name)
            and node.decorator.value.value == "typing"
            and node.decorator.attr.value == "final"
        ):
            feature = FeatureInfo(
                name="final",
                alias="final",
                import_style="typing_dot",
                version_requirement=(3, 8),
            )
            self._add_typing_dot_feature("final_typing_dot", feature)
        return True

    def _add_typing_dot_feature(self, key: str, feature: FeatureInfo) -> None:
        """Add a typing_dot feature, tracking whether it's in a nested context."""
        if self._current_context_stack:
            # We're in a nested context, add to the nested context
            context = self._current_context_stack[-1]
            if context not in self._nested_typing_dot_features:
                self._nested_typing_dot_features[context] = []
            self._nested_typing_dot_features[context].append(feature)
        else:
            # We're at module level
            self.features[key] = feature

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

        if self.import_manager.has_import("typing", "final"):
            alias = self.import_manager.get_import_alias("typing", "final") or "final"
            self.features["final_from_typing"] = FeatureInfo(
                name="final",
                alias=alias,
                import_style="from_typing",
                version_requirement=(3, 8),
            )

    def _get_version_requirement(self, feature_name: str) -> Tuple[int, int] | None:
        """Get version requirement for a typing feature."""
        requirements = {
            "Literal": (3, 8),
            "get_args": (3, 10),
            "get_origin": (3, 10),
            "final": (3, 8),
        }
        return requirements.get(feature_name)


class TypingExtensionsTransformer(cst.CSTTransformer):
    """
    Transform typing features to use typing_extensions for compatibility.

    This transformation handles:
    - typing.Literal -> typing_extensions.Literal (Python < 3.8)
    - typing.get_args -> typing_extensions.get_args (Python < 3.10)
    - typing.get_origin -> typing_extensions.get_origin (Python < 3.10)
    """

    def __init__(self, analysis: TypingAnalysisVisitor) -> None:
        self.analysis = analysis
        self.import_manager = analysis.import_manager
        self.features = analysis.features
        self.nested_imports = analysis.nested_imports
        self.nested_typing_dot_features = analysis._nested_typing_dot_features

        # Track which nested imports we've already transformed
        self._transformed_nested_imports: Set[cst.ImportFrom] = set()

    def leave_Module(
        self,
        original_node: cst.Module,
        updated_node: cst.Module,
    ) -> cst.Module:
        """Transform the entire module to add version compatibility checks."""
        # Use all module-level features (both from_typing and typing_dot can coexist)
        module_level_features = dict(self.features)

        # Check if we have any nested features that need sys import
        has_nested_features = bool(
            self.nested_imports or self.nested_typing_dot_features,
        )

        if not module_level_features and not has_nested_features:
            return updated_node

        # Start with the updated body as a list
        new_body = list(updated_node.body)

        # Remove from existing imports if needed (only for from_typing style)
        for feature in module_level_features.values():
            if feature.import_style == "from_typing":
                new_body = self.import_manager.remove_from_imports(
                    new_body,
                    "typing",
                    feature.name,
                )

        # Ensure required imports are present (sys is needed for any nested features)
        if has_nested_features or module_level_features:
            new_body = self.import_manager.ensure_sys_import(new_body)

        # Only add import typing if we have typing_dot style features
        has_typing_dot = any(
            f.import_style == "typing_dot" for f in module_level_features.values()
        )
        if has_typing_dot:
            new_body = self.import_manager.ensure_direct_import(new_body, "typing")

        # Group features by version requirement and import style
        version_groups = self._group_features_by_version_and_style(
            module_level_features,
        )

        # Add version checks after imports
        version_check_position = self.import_manager.find_post_import_position(new_body)

        # Simple approach: process each group exactly once
        for version_requirement, import_style in sorted(
            version_groups.keys(),
            key=lambda x: (x[0], x[1] == "typing_dot"),
        ):
            features_for_version = version_groups[(version_requirement, import_style)]

            if import_style == "typing_dot":
                # Use assignment for typing.X style
                version_check = self._create_assignment_version_check(
                    version_requirement,
                    features_for_version,
                )
            else:  # from_typing style
                # Use conditional imports for from_typing style
                version_check = self._create_conditional_import_version_check(
                    version_requirement,
                    features_for_version,
                )

            new_body.insert(version_check_position, version_check)
            version_check_position += 1

        return updated_node.with_changes(body=new_body)

    def leave_SimpleStatementLine(
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ) -> cst.SimpleStatementLine | cst.FlattenSentinel[cst.BaseStatement]:
        """Transform nested imports to conditional imports."""
        # Check if this line contains an ImportFrom that we need to transform
        if len(updated_node.body) == 1 and isinstance(
            updated_node.body[0],
            cst.ImportFrom,
        ):
            import_node = updated_node.body[0]

            # Only process if it matches a nested import exactly
            if (
                isinstance(import_node.module, cst.Name)
                and import_node.module.value == "typing"
                and isinstance(import_node.names, (list, tuple))
            ):
                # Find matching nested context
                for nested_context in self.nested_imports:
                    if self._imports_match(import_node, nested_context.import_node):
                        if import_node in self._transformed_nested_imports:
                            # Already transformed, return as-is
                            return updated_node

                        # Mark as transformed
                        self._transformed_nested_imports.add(import_node)

                        # Group features by version requirement
                        version_groups = defaultdict(list)
                        for feature in nested_context.features:
                            version_groups[feature.version_requirement].append(feature)

                        # Create conditional import statements
                        statements = []
                        for version_req, features in sorted(version_groups.items()):
                            conditional_import = (
                                self._create_conditional_import_version_check(
                                    version_req,
                                    features,
                                )
                            )
                            statements.append(conditional_import)

                        # Also add assignment checks for any typing_dot features in the same nested context
                        if (
                            nested_context.parent_node
                            in self.nested_typing_dot_features
                        ):
                            typing_dot_features = defaultdict(list)
                            for feature in self.nested_typing_dot_features[
                                nested_context.parent_node
                            ]:
                                typing_dot_features[feature.version_requirement].append(
                                    feature,
                                )

                            for version_req, features in sorted(
                                typing_dot_features.items(),
                            ):
                                assignment_check = (
                                    self._create_assignment_version_check(
                                        version_req,
                                        features,
                                    )
                                )
                                statements.append(assignment_check)

                        return cst.FlattenSentinel(statements)

        return updated_node

    def _imports_match(self, import1: cst.ImportFrom, import2: cst.ImportFrom) -> bool:
        """Check if two ImportFrom nodes have the same content."""
        if (
            not isinstance(import1.module, cst.Name)
            or not isinstance(import2.module, cst.Name)
            or import1.module.value != import2.module.value
        ):
            return False

        if not isinstance(import1.names, (list, tuple)) or not isinstance(
            import2.names,
            (list, tuple),
        ):
            return False

        if len(import1.names) != len(import2.names):
            return False

        # Compare import names
        for name1, name2 in zip(import1.names, import2.names):
            if (
                not isinstance(name1, cst.ImportAlias)
                or not isinstance(name2, cst.ImportAlias)
                or name1.name.value != name2.name.value
            ):
                return False

            # Compare aliases if present
            if name1.asname is None and name2.asname is None:
                continue
            elif (
                name1.asname is not None
                and name2.asname is not None
                and name1.asname.name.value == name2.asname.name.value
            ):
                continue
            else:
                return False

        return True

    def _group_features_by_version_and_style(
        self,
        features: Dict[str, FeatureInfo],
    ) -> Dict[Tuple[Tuple[int, int], str], List[FeatureInfo]]:
        """Group features by their version requirements and import style."""
        groups = defaultdict(list)
        for feature in features.values():
            key = (feature.version_requirement, feature.import_style)
            groups[key].append(feature)
        return dict(groups)

    def _create_assignment_version_check(
        self,
        version_requirement: Tuple[int, int],
        features: List[FeatureInfo],
    ) -> cst.If:
        """Create a version check block that assigns typing_extensions features to typing module."""
        # Create the condition: sys.version_info < version_requirement (inverted)
        condition = cst.Comparison(
            left=cst.Attribute(
                value=cst.Name("sys"),
                attr=cst.Name("version_info"),
            ),
            comparisons=[
                cst.ComparisonTarget(
                    operator=cst.LessThan(),
                    comparator=cst.Tuple(
                        [
                            cst.Element(cst.Integer(str(version_requirement[0]))),
                            cst.Element(cst.Integer(str(version_requirement[1]))),
                        ],
                    ),
                ),
            ],
        )

        # Create assignment statements
        statements = []

        # Only add import once if we need it
        if features:
            statements.append(
                cst.SimpleStatementLine(
                    [
                        cst.Import([cst.ImportAlias(cst.Name("typing_extensions"))]),
                    ],
                ),
            )

        # Add assignments for each feature
        for feature in features:
            assignment = cst.SimpleStatementLine(
                [
                    cst.Assign(
                        targets=[
                            cst.AssignTarget(
                                cst.Attribute(
                                    value=cst.Name("typing"),
                                    attr=cst.Name(feature.name),
                                ),
                            ),
                        ],
                        value=cst.Attribute(
                            value=cst.Name("typing_extensions"),
                            attr=cst.Name(feature.name),
                        ),
                    ),
                ],
            )
            statements.append(assignment)

        if_body = cst.IndentedBlock(statements)

        return cst.If(
            test=condition,
            body=if_body,
            leading_lines=[
                cst.EmptyLine(
                    whitespace=cst.SimpleWhitespace(""),
                    comment=None,
                ),
            ],
        )

    def _create_conditional_import_version_check(
        self,
        version_requirement: Tuple[int, int],
        features: List[FeatureInfo],
    ) -> cst.If:
        """Create a conditional import version check for from_typing style imports."""
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

        if_body = cst.IndentedBlock(
            [
                cst.SimpleStatementLine(
                    [
                        cst.ImportFrom(
                            module=cst.Name("typing"),
                            names=import_aliases,
                        ),
                    ],
                ),
            ],
        )

        else_body = cst.IndentedBlock(
            [
                cst.SimpleStatementLine(
                    [
                        cst.ImportFrom(
                            module=cst.Name("typing_extensions"),
                            names=import_aliases,
                        ),
                    ],
                ),
            ],
        )

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

    def _create_import_alias(self, name: str, alias: str) -> cst.ImportAlias:
        """Create an import alias for the given name and alias."""
        if name == alias:
            return cst.ImportAlias(name=cst.Name(name))
        else:
            return cst.ImportAlias(
                name=cst.Name(name),
                asname=cst.AsName(name=cst.Name(alias)),
            )


def transform_typing_extensions(source_code: str) -> str:
    """Apply typing_extensions transformation to source code using two-pass approach."""
    # Parse the module
    module = cst.parse_module(source_code)

    # First pass: analyze the code
    visitor = TypingAnalysisVisitor()
    module.visit(visitor)

    # Second pass: transform based on analysis
    transformer = TypingExtensionsTransformer(visitor)
    transformed_module = module.visit(transformer)

    return transformed_module.code
