from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import typing
from typing import Dict, List, Set, Tuple

import libcst as cst

from .import_utils import EnhancedImportManager


@dataclass
class TypingFeature:
    """Configuration for a typing feature that needs transformation."""

    name: str
    min_version: Tuple[
        int,
        int,
    ]  # Minimum Python version where this is available in typing


# Configuration: Add new features here as one-line additions
TYPING_FEATURES = [
    TypingFeature("Literal", (3, 8)),
    TypingFeature("get_args", (3, 10)),
    TypingFeature("get_origin", (3, 10)),
    TypingFeature("final", (3, 8)),
    TypingFeature("TypedDict", (3, 8)),  # Example of easy addition
]

# Create lookup dict for fast access
FEATURE_LOOKUP = {f.name: f for f in TYPING_FEATURES}


@dataclass
class UsageInfo:
    """Information about how a typing feature is used in the code."""

    feature: TypingFeature
    alias: str  # The name/alias used in the code
    import_style: str  # "from_typing" or "typing_dot"
    scope_path: Tuple[cst.CSTNode, ...]  # Path from module to usage scope


@dataclass
class ImportStatementInfo:
    """Information about a typing import statement that needs replacement."""

    import_node: cst.ImportFrom  # The actual import statement
    features: List[TypingFeature]  # Features being imported
    scope_path: Tuple[cst.CSTNode, ...]  # Path from module to this import
    replacement_strategy: str  # "in_place" or "remove" (if handled elsewhere)


class TypingAnalysisVisitor(cst.CSTVisitor):
    """First pass: Analyze typing usage and collect transformation requirements."""

    def __init__(self) -> None:
        self.import_manager = EnhancedImportManager()
        self.usages: List[UsageInfo] = []
        self.import_statements: List[ImportStatementInfo] = []
        self._scope_stack: List[cst.CSTNode] = []  # Track nested scopes
        self._typing_import_scopes: Dict[
            Tuple[cst.CSTNode, ...],
            bool,
        ] = {}  # Track where 'import typing' occurs
        self._existing_version_checks: Set[
            Tuple[Tuple[cst.CSTNode, ...], Tuple[int, int], str]
        ] = set()  # Track existing version checks: (scope_path, version, type)

    def visit_Module(self, node: cst.Module) -> None:
        """Scan imports at module level."""
        self.import_manager.scan_imports(node.body)
        self._detect_from_typing_imports()

    def visit_If(self, node: cst.If) -> bool:
        """Track nested scopes and detect existing version checks."""
        self._scope_stack.append(node)

        # Check if this is an existing version check we should avoid duplicating
        self._detect_existing_version_check(node)

        return True

    def leave_If(self, original_node: cst.If) -> None:
        """Clean up scope stack."""
        if self._scope_stack and self._scope_stack[-1] is original_node:
            self._scope_stack.pop()

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        """Track function scopes."""
        self._scope_stack.append(node)
        return True

    def leave_FunctionDef(self, original_node: cst.FunctionDef) -> None:
        """Clean up function scope stack."""
        if self._scope_stack and self._scope_stack[-1] is original_node:
            self._scope_stack.pop()

    def visit_Import(self, node: cst.Import) -> bool:
        """Track 'import typing' statements and their scopes."""
        for alias in node.names:
            if isinstance(alias, cst.ImportAlias) and isinstance(alias.name, cst.Name):
                if alias.name.value == "typing":
                    # Record where this 'import typing' statement occurs
                    current_scope = tuple(self._scope_stack)
                    self._typing_import_scopes[current_scope] = True
        return True

    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool:
        """Detect typing imports that need transformation."""
        if isinstance(node.module, cst.Name) and node.module.value == "typing":
            # Track this import statement for potential transformation
            features = self._extract_features_from_import(node)
            if features:
                import_info = ImportStatementInfo(
                    import_node=node,
                    features=features,
                    scope_path=tuple(self._scope_stack),
                    replacement_strategy="in_place",  # Default to in-place replacement
                )
                self.import_statements.append(import_info)

            # Also track usages if this is a nested import
            if self._scope_stack:
                self._process_typing_import(node, "from_typing")
        return True

    def visit_Attribute(self, node: cst.Attribute) -> bool:
        """Detect all typing.feature usage regardless of context."""
        self._check_typing_dot_usage(node)
        return True

    def _detect_from_typing_imports(self) -> None:
        """Detect module-level from typing imports that need transformation."""
        for feature in TYPING_FEATURES:
            if self.import_manager.has_import("typing", feature.name):
                alias = (
                    self.import_manager.get_import_alias("typing", feature.name)
                    or feature.name
                )
                usage = UsageInfo(
                    feature=feature,
                    alias=alias,
                    import_style="from_typing",
                    scope_path=(),  # Module level
                )
                self.usages.append(usage)

    def _check_typing_dot_usage(self, node: cst.BaseExpression) -> None:
        """Check if node is a typing.feature usage and record it."""
        if (
            isinstance(node, cst.Attribute)
            and isinstance(node.value, cst.Name)
            and node.value.value == "typing"
        ):
            feature_name = node.attr.value
            if feature_name in FEATURE_LOOKUP:
                feature = FEATURE_LOOKUP[feature_name]
                usage = UsageInfo(
                    feature=feature,
                    alias=feature_name,
                    import_style="typing_dot",
                    scope_path=tuple(self._scope_stack),
                )
                self.usages.append(usage)

    def _extract_features_from_import(
        self,
        node: cst.ImportFrom,
    ) -> List[TypingFeature]:
        """Extract typing features from an import statement."""
        if isinstance(node.names, cst.ImportStar):
            return []  # Skip star imports

        features = []
        # Normalize names to a list
        if isinstance(node.names, (list, tuple)):
            names = node.names
        else:
            names = [node.names]

        for name_item in names:
            if isinstance(name_item, cst.ImportAlias):
                feature_name = name_item.name.value
                if feature_name in FEATURE_LOOKUP:
                    features.append(FEATURE_LOOKUP[feature_name])

        return features

    def _process_typing_import(self, node: cst.ImportFrom, import_style: str) -> None:
        """Process a typing import and record any transformable features."""
        if isinstance(node.names, cst.ImportStar):
            return  # Skip star imports

        # Normalize names to a list
        if isinstance(node.names, (list, tuple)):
            names = node.names
        else:
            names = [node.names]

        for name_item in names:
            if isinstance(name_item, cst.ImportAlias):
                feature_name = name_item.name.value
                if feature_name in FEATURE_LOOKUP:
                    alias = (
                        name_item.asname.name.value
                        if name_item.asname
                        else feature_name
                    )
                    feature = FEATURE_LOOKUP[feature_name]
                    usage = UsageInfo(
                        feature=feature,
                        alias=alias,
                        import_style=import_style,
                        scope_path=tuple(self._scope_stack),
                    )
                    self.usages.append(usage)

    def _detect_existing_version_check(self, node: cst.If) -> None:
        """Detect if this If node is an existing typing_extensions version check."""
        # Check if this is a sys.version_info comparison
        if not self._is_version_check(node.test):
            return

        # Check if it contains typing_extensions assignments or imports
        version_info = self._extract_version_info(node.test)
        if not version_info:
            return

        check_type = self._classify_version_check(node)
        if check_type:
            current_scope = tuple(self._scope_stack[:-1])  # Exclude the current If node
            self._existing_version_checks.add((current_scope, version_info, check_type))

    def _is_version_check(self, test_node: cst.BaseExpression) -> bool:
        """Check if the test is a sys.version_info comparison."""
        if not isinstance(test_node, cst.Comparison):
            return False

        # Check if left side is sys.version_info
        if not (
            isinstance(test_node.left, cst.Attribute)
            and isinstance(test_node.left.value, cst.Name)
            and test_node.left.value.value == "sys"
            and test_node.left.attr.value == "version_info"
        ):
            return False

        return True

    def _extract_version_info(
        self,
        test_node: cst.BaseExpression,
    ) -> Tuple[int, int] | None:
        """Extract the version tuple from a version comparison."""
        if not isinstance(test_node, cst.Comparison):
            return None

        if len(test_node.comparisons) != 1:
            return None

        comparison = test_node.comparisons[0]
        if not isinstance(comparison.comparator, cst.Tuple):
            return None

        if len(comparison.comparator.elements) != 2:
            return None

        try:
            major = int(comparison.comparator.elements[0].value.value)  # type: ignore[attr-defined]
            minor = int(comparison.comparator.elements[1].value.value)  # type: ignore[attr-defined]
            return (major, minor)
        except (AttributeError, ValueError):
            return None

    def _classify_version_check(self, node: cst.If) -> str | None:
        """Classify what type of version check this is (assignment or conditional_import)."""
        # Look at the body to determine the type
        for stmt in node.body.body:
            if isinstance(stmt, cst.SimpleStatementLine):
                for substmt in stmt.body:
                    if isinstance(substmt, cst.Assign):
                        # Check if it's a typing.X = typing_extensions.X assignment
                        if (
                            isinstance(substmt.value, cst.Attribute)
                            and isinstance(substmt.value.value, cst.Name)
                            and substmt.value.value.value == "typing_extensions"
                        ):
                            return "assignment"
                    elif isinstance(substmt, cst.ImportFrom):
                        # Check if it's importing from typing or typing_extensions
                        if isinstance(
                            substmt.module,
                            cst.Name,
                        ) and substmt.module.value in ["typing", "typing_extensions"]:
                            return "conditional_import"

        # Check the else clause too
        if hasattr(node, "orelse") and node.orelse:
            if isinstance(node.orelse, cst.Else):
                for stmt in node.orelse.body.body:
                    if isinstance(stmt, cst.SimpleStatementLine):
                        for substmt in stmt.body:
                            if isinstance(substmt, cst.ImportFrom):
                                # Check if it's importing from typing or typing_extensions
                                if isinstance(
                                    substmt.module,
                                    cst.Name,
                                ) and substmt.module.value in [
                                    "typing",
                                    "typing_extensions",
                                ]:
                                    return "conditional_import"

        return None


class TypingExtensionsTransformer(cst.CSTTransformer):
    """Second pass: Transform typing features to use typing_extensions for compatibility."""

    def __init__(self, analysis: TypingAnalysisVisitor) -> None:
        self.analysis = analysis
        self.import_manager = analysis.import_manager
        self.usages = analysis.usages
        self.import_statements = analysis.import_statements
        self.existing_version_checks = analysis._existing_version_checks

        # Pre-compute the transformation strategy
        self.typing_dot_assignments = self._plan_typing_dot_assignments()

        # Track what we've already applied
        self._applied_assignments: Set[Tuple[Tuple[cst.CSTNode, ...], str]] = set()
        self._fully_replaced_imports: Set[Tuple[Tuple[cst.CSTNode, ...], frozenset]] = (
            set()
        )

        # Track current scope during transformation
        self._current_scope_stack: List[cst.CSTNode] = []

    def _find_typing_import_scope(self) -> Tuple[cst.CSTNode, ...]:
        """Find the scope where 'import typing' is located.

        Returns the deepest scope that has an 'import typing' statement,
        or module level if no nested 'import typing' is found.
        """
        # Check all tracked typing import scopes, return the deepest one
        deepest_scope: Tuple[cst.CSTNode, ...] = ()
        max_depth = -1

        for scope_path in self.analysis._typing_import_scopes.keys():
            if len(scope_path) > max_depth:
                max_depth = len(scope_path)
                deepest_scope = scope_path

        return deepest_scope

    def _plan_typing_dot_assignments(
        self,
    ) -> Dict[Tuple[cst.CSTNode, ...], Dict[str, TypingFeature]]:
        """Plan where typing.X = typing_extensions.X assignments need to be placed.

        Returns: {scope_path: {feature_name: feature}}
        """
        assignments = defaultdict(dict)  # type: ignore[var-annotated]

        # Find all typing_dot usages that need assignment patches
        typing_dot_usages = [u for u in self.usages if u.import_style == "typing_dot"]

        if not typing_dot_usages:
            return dict(assignments)

        # Strategy: Find the optimal placement scope by considering all usages together.
        # This ensures that features used in the same logical scope get placed together.

        # Find the common scope that can serve all typing_dot usages
        all_scope_paths = [usage.scope_path for usage in typing_dot_usages]
        common_scope = self._find_common_scope_path(all_scope_paths)

        # Check if we should use the common scope or the import typing scope
        import_typing_scope = self._find_typing_import_scope()

        # Only optimize by placing in nested scope for specific cases.
        # For most cases, use import typing scope for broad accessibility.
        # The main optimization case is when all usages are inside a single IF block
        # (like TYPE_CHECKING) where it makes sense to scope the assignments there.
        if (
            common_scope  # There is a common nested scope
            and len(common_scope)
            > len(import_typing_scope)  # It's deeper than import typing scope
            and len(common_scope)
            == 1  # Only one level deep (direct child of import scope)
            and isinstance(common_scope[0], cst.If)
        ):  # And it's an IF statement
            optimal_scope = common_scope
        else:
            # Default to import typing scope for broad accessibility
            optimal_scope = import_typing_scope

        # Group features by their names and place them all at the optimal scope
        feature_groups = defaultdict(list)
        for usage in typing_dot_usages:
            feature_groups[usage.feature.name].append(usage)

        for feature_name, usages in feature_groups.items():
            assignments[optimal_scope][feature_name] = usages[0].feature

        return dict(assignments)

    def _get_optimal_scope_for_features(
        self,
        features: List[UsageInfo],
    ) -> Tuple[cst.CSTNode, ...]:
        """Determine the optimal scope to place version checks.

        For typing_dot usages, prefer the deepest common scope of all usages if they're all nested.
        For from_typing usages, place at the scope where the import occurs.
        """
        if not features:
            return ()

        # For typing_dot usages, we can optimize placement
        if all(usage.import_style == "typing_dot" for usage in features):
            # If all usages are within the same nested scope, place assignments there
            # instead of at the import typing level
            if features:
                # Check if all usages share a common nested scope
                common_scope = self._find_common_scope_path(
                    [f.scope_path for f in features],
                )

                # If we have a common nested scope, use it
                if common_scope:
                    return common_scope

            # Otherwise fall back to placing at import typing scope
            return self._find_typing_import_scope()

        # If any usage is at module level, place at module level
        if any(not usage.scope_path for usage in features):
            return ()

        # Find the common scope prefix for from_typing usages
        if len(features) == 1:
            return features[0].scope_path

        # Find common prefix of all scope paths
        return self._find_common_scope_path([f.scope_path for f in features])

    def _find_common_scope_path(
        self,
        scope_paths: List[Tuple[cst.CSTNode, ...]],
    ) -> Tuple[cst.CSTNode, ...]:
        """Find the common scope prefix for a list of scope paths."""
        if not scope_paths:
            return ()

        if len(scope_paths) == 1:
            return scope_paths[0]

        # Find common prefix of all scope paths
        common_scope: tuple[typing.Any, ...] = ()
        min_depth = min(len(path) for path in scope_paths)
        for i in range(min_depth):
            if all(path[i] is scope_paths[0][i] for path in scope_paths):
                common_scope = common_scope + (scope_paths[0][i],)
            else:
                break

        return common_scope

    def _version_check_exists(
        self,
        scope_path: Tuple[cst.CSTNode, ...],
        version: Tuple[int, int],
        check_type: str,
    ) -> bool:
        """Check if a version check already exists for the given scope, version, and type."""
        return (scope_path, version, check_type) in self.existing_version_checks

    def leave_Module(
        self,
        original_node: cst.Module,
        updated_node: cst.Module,
    ) -> cst.Module:
        """Transform the entire module to add sys import and module-level assignments."""
        if not self.import_statements and not self.typing_dot_assignments:
            return updated_node

        new_body = list(updated_node.body)

        # Remove only the transformable features from module-level typing imports
        # Leave other imports (like Union, List, etc.) unchanged
        module_level_imports = [
            stmt for stmt in self.import_statements if not stmt.scope_path
        ]
        for import_stmt in module_level_imports:
            for feature in import_stmt.features:
                new_body = self.import_manager.remove_from_imports(
                    new_body,
                    "typing",
                    feature.name,
                )

        # Ensure sys import if we have any transformations
        if self.import_statements or self.typing_dot_assignments:
            new_body = self.import_manager.ensure_sys_import(new_body)

        # Ensure typing import if we have module-level typing_dot assignments
        module_level_assignments = self.typing_dot_assignments.get((), {})
        if module_level_assignments:
            new_body = self.import_manager.ensure_direct_import(new_body, "typing")

        # Process all module-level transformations together, sorted by version
        version_check_position = self.import_manager.find_post_import_position(new_body)

        # Collect all transformations that need to be added
        all_transformations = []

        # 1. Collect conditional imports for ALL module-level from_typing usages
        # (leave_SimpleStatementLine no longer handles module-level imports)
        from_typing_usages = [
            u
            for u in self.usages
            if u.import_style == "from_typing" and not u.scope_path
        ]
        if from_typing_usages:
            from_typing_groups = defaultdict(list)
            for usage in from_typing_usages:
                from_typing_groups[usage.feature.min_version].append(usage)

            for version, usages in from_typing_groups.items():
                # Skip if this conditional import already exists
                if not self._version_check_exists((), version, "conditional_import"):
                    all_transformations.append((version, "conditional_import", usages))

        # 2. Collect assignment patches for typing_dot usages
        if module_level_assignments:
            version_groups = defaultdict(list)
            for feature_name, feature in module_level_assignments.items():
                version_groups[feature.min_version].append((feature_name, feature))

            for version, features in version_groups.items():
                assignment_key = ((), version)
                # Skip if this assignment already exists or we've already applied it
                if (
                    assignment_key not in self._applied_assignments
                    and not self._version_check_exists((), version, "assignment")
                ):
                    self._applied_assignments.add(assignment_key)  # type: ignore[arg-type]
                    all_transformations.append((version, "assignment", features))  # type: ignore[arg-type]

        # Sort all transformations by type first (assignments, then conditional imports), then by version
        all_transformations.sort(key=lambda x: (x[1] == "conditional_import", x[0]))

        for version, transformation_type, data in all_transformations:  # type: ignore[arg-type]
            if transformation_type == "conditional_import":
                version_check = self._create_conditional_import_version_check(
                    version,
                    data,
                    nested=False,
                )
            else:  # assignment
                version_check = self._create_assignment_version_check_for_features(
                    version,
                    data,  # type: ignore[arg-type]
                )

            new_body.insert(version_check_position, version_check)
            version_check_position += 1

        return updated_node.with_changes(body=new_body)

    def leave_If(
        self,
        original_node: cst.If,
        updated_node: cst.If,
    ) -> cst.If:
        """Add typing.X assignments for nested scopes when needed."""
        # Find the scope path for this if block
        current_scope = None
        for scope_path in self.typing_dot_assignments.keys():
            if scope_path and len(scope_path) > 0 and scope_path[-1] is original_node:
                current_scope = scope_path
                break

        if not current_scope or current_scope not in self.typing_dot_assignments:
            return updated_node

        # Get assignments needed for this scope
        scope_assignments = self.typing_dot_assignments[current_scope]
        if not scope_assignments:
            return updated_node

        new_body = list(updated_node.body.body)
        # Find position after any import statements
        insert_position = 0
        for i, stmt in enumerate(new_body):
            if isinstance(stmt, cst.SimpleStatementLine):
                if any(isinstance(s, (cst.Import, cst.ImportFrom)) for s in stmt.body):
                    insert_position = i + 1
                else:
                    break
            else:
                break

        # Group assignments by version
        version_groups = defaultdict(list)
        for feature_name, feature in scope_assignments.items():
            version_groups[feature.min_version].append((feature_name, feature))

        for version in sorted(version_groups.keys()):
            features = version_groups[version]
            assignment_key = (current_scope, version)

            # Skip if this assignment already exists or we've already applied it
            if (
                assignment_key not in self._applied_assignments
                and not self._version_check_exists(current_scope, version, "assignment")
            ):
                self._applied_assignments.add(assignment_key)  # type: ignore[arg-type]

                version_check = self._create_assignment_version_check_for_features(
                    version,
                    features,
                    nested=True,
                )
                new_body.insert(insert_position, version_check)
                insert_position += 1

        if insert_position > 0:
            new_body_block = cst.IndentedBlock(body=new_body)
            return updated_node.with_changes(body=new_body_block)

        return updated_node

    def visit_If(self, node: cst.If) -> None:
        """Track If scopes during transformation."""
        self._current_scope_stack.append(node)

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        """Track function scopes during transformation."""
        self._current_scope_stack.append(node)

    def _is_inside_version_check(self) -> bool:
        """Check if the current position is already inside a version check."""
        # Look through the current scope stack to see if we're inside an if statement
        # that looks like a version check
        for scope_node in self._current_scope_stack:
            if isinstance(scope_node, cst.If):
                if self.analysis._is_version_check(scope_node.test):
                    return True
        return False

    def leave_SimpleStatementLine(
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ) -> cst.SimpleStatementLine | cst.FlattenSentinel[cst.BaseStatement]:
        """Replace typing import statements with conditional imports in-place."""
        # Check if this line contains a typing import we need to transform
        if len(updated_node.body) == 1 and isinstance(
            updated_node.body[0],
            cst.ImportFrom,
        ):
            import_node = updated_node.body[0]
            if (
                isinstance(import_node.module, cst.Name)
                and import_node.module.value == "typing"
            ):
                # Skip transformation if we're already inside a version check
                if self._is_inside_version_check():
                    return updated_node

                # Check if this import contains any transformable features
                current_names = self._extract_import_names(import_node)

                # Find all transformable features in this import
                transformable_features: List[cst.CSTNode] = []
                for import_info in self.import_statements:
                    if import_info.replacement_strategy == "in_place":
                        # Only handle nested imports in leave_SimpleStatementLine
                        # Module-level imports will be handled by leave_Module for consistent ordering
                        if import_info.scope_path:  # Only nested imports
                            for feature in import_info.features:
                                if feature.name in current_names:
                                    transformable_features.append(feature)

                if transformable_features:
                    # This import contains transformable features
                    transformable_names = {f.name for f in transformable_features}

                    if current_names == transformable_names:
                        # All imports in this statement are transformable - replace entire statement
                        # Create a synthetic import_info for this transformation
                        # Use the actual scope path from one of the matching import_info objects
                        actual_scope_path: Tuple[cst.CSTNode, ...] = (
                            transformable_features[0] if transformable_features else ()
                        )
                        for import_info in self.import_statements:
                            if (
                                import_info.replacement_strategy == "in_place"
                                and import_info.scope_path
                            ):
                                for feature in import_info.features:
                                    if feature.name in current_names:
                                        actual_scope_path = import_info.scope_path
                                        break
                                if actual_scope_path != ():
                                    break

                        synthetic_import_info = ImportStatementInfo(
                            import_node=import_node,
                            features=transformable_features,
                            scope_path=actual_scope_path,
                            replacement_strategy="in_place",
                        )

                        # Only remove this import if it would be truly redundant
                        # For TYPE_CHECKING blocks and other special cases, we should keep the import
                        # since it might be needed after variable shadowing
                        features_to_transform = transformable_features

                        # Track that we've fully replaced this import to avoid duplication
                        replacement_key = (
                            (),
                            frozenset(f.name for f in features_to_transform),
                        )
                        self._fully_replaced_imports.add(replacement_key)

                        # Update synthetic_import_info with only the features we need to transform
                        synthetic_import_info = ImportStatementInfo(
                            import_node=import_node,
                            features=features_to_transform,
                            scope_path=actual_scope_path,
                            replacement_strategy="in_place",
                        )

                        return self._create_conditional_import_replacement(
                            synthetic_import_info,
                        )
                    # else: Mixed import - let leave_Module handle removal of transformable parts

        return updated_node

    def _imports_match(
        self,
        info_import: cst.ImportFrom,
        current_import: cst.ImportFrom,
    ) -> bool:
        """Check if two import statements represent the same import."""
        # Compare module names
        if not (
            isinstance(info_import.module, cst.Name)
            and isinstance(current_import.module, cst.Name)
        ):
            return False
        if info_import.module.value != current_import.module.value:
            return False

        # Compare imported names
        info_names = self._extract_import_names(info_import)
        current_names = self._extract_import_names(current_import)

        return info_names == current_names

    def _extract_import_names(self, import_node: cst.ImportFrom) -> set:
        """Extract the set of names being imported."""
        if isinstance(import_node.names, cst.ImportStar):
            return {"*"}

        names = (
            import_node.names
            if isinstance(import_node.names, (list, tuple))
            else [import_node.names]
        )
        result = set()
        for name_item in names:
            if isinstance(name_item, cst.ImportAlias):
                result.add(name_item.name.value)
        return result

    def _import_contains_feature(
        self,
        import_node: cst.ImportFrom,
        feature_name: str,
    ) -> bool:
        """Check if an import node imports a specific feature."""
        if isinstance(import_node.names, cst.ImportStar):
            return True  # Star imports include everything

        names = (
            import_node.names
            if isinstance(import_node.names, (list, tuple))
            else [import_node.names]
        )

        for name_item in names:
            if (
                isinstance(name_item, cst.ImportAlias)
                and name_item.name.value == feature_name
            ):
                return True
        return False

    def _create_assignment_version_check(
        self,
        version_requirement: Tuple[int, int],
        usages: List[UsageInfo],
        nested: bool = False,
    ) -> cst.If:
        """Create a version check block that assigns typing_extensions features to typing module."""
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

        statements = []

        # Only add import once if we need it
        if usages:
            statements.append(
                cst.SimpleStatementLine(
                    [
                        cst.Import([cst.ImportAlias(cst.Name("typing_extensions"))]),
                    ],
                ),
            )

        # Add assignments for each unique feature (avoid duplicates)
        seen_features = set()
        for usage in usages:
            if usage.feature.name not in seen_features:
                seen_features.add(usage.feature.name)
                assignment = cst.SimpleStatementLine(
                    [
                        cst.Assign(
                            targets=[
                                cst.AssignTarget(
                                    cst.Attribute(
                                        value=cst.Name("typing"),
                                        attr=cst.Name(usage.feature.name),
                                    ),
                                ),
                            ],
                            value=cst.Attribute(
                                value=cst.Name("typing_extensions"),
                                attr=cst.Name(usage.feature.name),
                            ),
                        ),
                    ],
                )
                statements.append(assignment)

        if_body = cst.IndentedBlock(statements)

        leading_lines = (
            []
            if nested
            else [
                cst.EmptyLine(),
            ]
        )

        return cst.If(
            test=condition,
            body=if_body,
            leading_lines=leading_lines,
        )

    def _create_conditional_import_version_check(
        self,
        version_requirement: Tuple[int, int],
        usages: List[UsageInfo],
        nested: bool = False,
    ) -> cst.If:
        """Create a conditional import version check for from_typing style imports."""
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
            self._create_import_alias(usage.feature.name, usage.alias)
            for usage in usages
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

        leading_lines = (
            []
            if nested
            else [
                cst.EmptyLine(),
            ]
        )

        return cst.If(
            test=condition,
            body=if_body,
            orelse=cst.Else(body=else_body),
            leading_lines=leading_lines,
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

    def _create_conditional_import_replacement(
        self,
        import_info: ImportStatementInfo,
    ) -> cst.FlattenSentinel[cst.BaseStatement]:
        """Replace a typing import with conditional import statements."""
        # Extract alias information from the original import
        feature_aliases = self._extract_feature_aliases(import_info.import_node)

        # Group features by version
        version_groups = defaultdict(list)
        for feature in import_info.features:
            # Use the actual alias from the import
            alias = feature_aliases.get(feature.name, feature.name)
            usage = UsageInfo(
                feature=feature,
                alias=alias,
                import_style="from_typing",
                scope_path=import_info.scope_path,
            )
            version_groups[feature.min_version].append(usage)

        statements = []
        for version in sorted(version_groups.keys()):
            usages = version_groups[version]
            version_check = self._create_conditional_import_version_check(
                version,
                usages,
                nested=bool(import_info.scope_path),
            )
            statements.append(version_check)

        return cst.FlattenSentinel(statements)

    def _extract_feature_aliases(self, import_node: cst.ImportFrom) -> dict:
        """Extract feature name to alias mapping from an import statement."""
        aliases = {}  # type: ignore[var-annotated]

        if isinstance(import_node.names, cst.ImportStar):
            return aliases

        names = (
            import_node.names
            if isinstance(import_node.names, (list, tuple))
            else [import_node.names]
        )

        for name_item in names:
            if isinstance(name_item, cst.ImportAlias):
                feature_name = name_item.name.value
                alias = (
                    name_item.asname.name.value if name_item.asname else feature_name
                )
                aliases[feature_name] = alias

        return aliases

    def _create_assignment_version_check_for_features(
        self,
        version_requirement: Tuple[int, int],
        features: List[Tuple[str, TypingFeature]],
        nested: bool = False,
    ) -> cst.If:
        """Create a version check that assigns typing_extensions features to typing module."""
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

        statements = []

        # Add import typing_extensions
        statements.append(
            cst.SimpleStatementLine(
                [
                    cst.Import([cst.ImportAlias(cst.Name("typing_extensions"))]),
                ],
            ),
        )

        # Add assignments for each feature
        for feature_name, feature in features:
            assignment = cst.SimpleStatementLine(
                [
                    cst.Assign(
                        targets=[
                            cst.AssignTarget(
                                cst.Attribute(
                                    value=cst.Name("typing"),
                                    attr=cst.Name(feature_name),
                                ),
                            ),
                        ],
                        value=cst.Attribute(
                            value=cst.Name("typing_extensions"),
                            attr=cst.Name(feature_name),
                        ),
                    ),
                ],
            )
            statements.append(assignment)

        if_body = cst.IndentedBlock(statements)

        leading_lines = (
            []
            if nested
            else [
                cst.EmptyLine(),
            ]
        )

        return cst.If(
            test=condition,
            body=if_body,
            leading_lines=leading_lines,
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
    code = transformed_module.code
    # FIXME: we should not be producing empty lines with whitespace in the first place.
    code = "\n".join(line if line.strip() else "" for line in code.splitlines()) + "\n"
    return code
