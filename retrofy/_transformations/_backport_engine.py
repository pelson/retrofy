"""Generic import-backport engine.

Rewrites ``from <source_module> import <name>`` statements (and
``<source_module>.<name>`` attribute access) into ``sys.version_info``-gated
conditional imports that fall back to a different module on older Pythons.

Used by both :mod:`retrofy._transformations.typing_extensions` (typing →
typing_extensions for features added in newer typing versions) and
:mod:`retrofy._transformations.collections_abc` (collections.abc → typing for
generic-subscriptable ABCs on Python < 3.9).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import libcst as cst

from .import_utils import EnhancedImportManager


@dataclass(frozen=True)
class BackportFeature:
    """A name from ``source_module`` that has a fallback on older Pythons."""

    name: str
    min_version: Tuple[int, int]
    # Name in the fallback module if different (e.g. collections.abc.Set is
    # typing.AbstractSet). ``None`` means the name is identical.
    fallback_name: Optional[str] = None

    @property
    def effective_fallback_name(self) -> str:
        return self.fallback_name if self.fallback_name is not None else self.name


@dataclass(frozen=True)
class BackportConfig:
    source_module: str  # e.g. "typing", "collections.abc"
    fallback_module: str  # e.g. "typing_extensions", "typing"
    features: Tuple[BackportFeature, ...] = field(default_factory=tuple)

    @property
    def feature_lookup(self) -> Dict[str, BackportFeature]:
        return {f.name: f for f in self.features}


# ---------------------------------------------------------------------------
# Dotted-module helpers
# ---------------------------------------------------------------------------


def _module_matches(node: Optional[cst.BaseExpression], dotted: str) -> bool:
    """Whether a CST node represents the dotted module name ``dotted``."""
    if node is None:
        return False
    parts = dotted.split(".")
    cur = node
    for part in reversed(parts[1:]):
        if not isinstance(cur, cst.Attribute):
            return False
        if not isinstance(cur.attr, cst.Name) or cur.attr.value != part:
            return False
        cur = cur.value
    if not isinstance(cur, cst.Name):
        return False
    return cur.value == parts[0]


def _dotted_to_cst(dotted: str) -> cst.BaseExpression:
    parts = dotted.split(".")
    node: cst.BaseExpression = cst.Name(parts[0])
    for part in parts[1:]:
        node = cst.Attribute(value=node, attr=cst.Name(part))
    return node


def _make_direct_import(dotted: str) -> cst.SimpleStatementLine:
    return cst.SimpleStatementLine(
        [cst.Import([cst.ImportAlias(_dotted_to_cst(dotted))])],
        trailing_whitespace=cst.TrailingWhitespace(newline=cst.Newline()),
    )


def _has_early_direct_import(
    body: List[cst.BaseStatement],
    dotted: str,
) -> bool:
    for stmt in body:
        if isinstance(stmt, cst.SimpleStatementLine):
            has_any_import = any(
                isinstance(s, (cst.Import, cst.ImportFrom)) for s in stmt.body
            )
            if not has_any_import:
                return False
            for substmt in stmt.body:
                if isinstance(substmt, cst.Import):
                    for alias in substmt.names:
                        if _module_matches(alias.name, dotted):
                            return True
        else:
            return False
    return False


def _ensure_direct_import(
    body: List[cst.BaseStatement],
    dotted: str,
    insert_position_fn,
) -> List[cst.BaseStatement]:
    if _has_early_direct_import(body, dotted):
        return body
    pos = insert_position_fn(body)
    body.insert(pos, _make_direct_import(dotted))
    return body


# ---------------------------------------------------------------------------
# Pass 1: analysis
# ---------------------------------------------------------------------------


@dataclass
class _UsageInfo:
    feature: BackportFeature
    alias: str  # the local binding name in the user's code
    import_style: str  # "from_source" or "source_dot"
    scope_path: Tuple[cst.CSTNode, ...]


@dataclass
class _ImportStmtInfo:
    import_node: cst.ImportFrom
    features: List[BackportFeature]
    scope_path: Tuple[cst.CSTNode, ...]


class _AnalysisVisitor(cst.CSTVisitor):
    def __init__(self, config: BackportConfig) -> None:
        self.config = config
        self.lookup = config.feature_lookup
        self.import_manager = EnhancedImportManager()
        self.usages: List[_UsageInfo] = []
        self.import_statements: List[_ImportStmtInfo] = []
        self._scope_stack: List[cst.CSTNode] = []
        # Scopes in which ``import <source_module>`` was found.
        self._source_import_scopes: Dict[Tuple[cst.CSTNode, ...], bool] = {}
        # Existing (scope_path, version, check_type) tuples we should not duplicate.
        self._existing_version_checks: Set[
            Tuple[Tuple[cst.CSTNode, ...], Tuple[int, int], str]
        ] = set()

    # -- scope tracking -----------------------------------------------------

    def visit_Module(self, node: cst.Module) -> None:
        self.import_manager.scan_imports(node.body)

    def leave_Module(self, original_node: cst.Module) -> None:
        # Module-level usages depend on having seen every ImportFrom first,
        # so detect after the traversal rather than at visit_Module time.
        self._detect_module_level_from_imports(original_node)

    def visit_If(self, node: cst.If) -> bool:
        self._scope_stack.append(node)
        self._detect_existing_version_check(node)
        return True

    def leave_If(self, original_node: cst.If) -> None:
        if self._scope_stack and self._scope_stack[-1] is original_node:
            self._scope_stack.pop()

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        self._scope_stack.append(node)
        return True

    def leave_FunctionDef(self, original_node: cst.FunctionDef) -> None:
        if self._scope_stack and self._scope_stack[-1] is original_node:
            self._scope_stack.pop()

    # -- import detection ---------------------------------------------------

    def visit_Import(self, node: cst.Import) -> bool:
        for alias in node.names:
            if isinstance(alias, cst.ImportAlias) and _module_matches(
                alias.name,
                self.config.source_module,
            ):
                self._source_import_scopes[tuple(self._scope_stack)] = True
        return True

    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool:
        if not _module_matches(node.module, self.config.source_module):
            return True
        features = self._extract_features_from_import(node)
        if features:
            self.import_statements.append(
                _ImportStmtInfo(
                    import_node=node,
                    features=features,
                    scope_path=tuple(self._scope_stack),
                ),
            )
        if self._scope_stack:
            self._record_from_import_usages(node)
        return True

    def visit_Attribute(self, node: cst.Attribute) -> bool:
        # Detect ``<source_module>.<feature>`` attribute access. For dotted
        # source modules (e.g. collections.abc) the value side is itself an
        # Attribute; for simple ones it's a Name.
        if _module_matches(node.value, self.config.source_module):
            feature_name = node.attr.value
            if feature_name in self.lookup:
                self.usages.append(
                    _UsageInfo(
                        feature=self.lookup[feature_name],
                        alias=feature_name,
                        import_style="source_dot",
                        scope_path=tuple(self._scope_stack),
                    ),
                )
        return True

    def _detect_module_level_from_imports(self, _node: cst.Module) -> None:
        # Walk the import_statements collected in visit_ImportFrom so that
        # usages preserve source order rather than config order.
        for info in self.import_statements:
            if info.scope_path:
                continue
            aliases = self._extract_feature_aliases(info.import_node)
            for feature in info.features:
                self.usages.append(
                    _UsageInfo(
                        feature=feature,
                        alias=aliases.get(feature.name, feature.name),
                        import_style="from_source",
                        scope_path=(),
                    ),
                )

    @staticmethod
    def _extract_feature_aliases(import_node: cst.ImportFrom) -> Dict[str, str]:
        aliases: Dict[str, str] = {}
        if isinstance(import_node.names, cst.ImportStar):
            return aliases
        names = (
            import_node.names
            if isinstance(import_node.names, (list, tuple))
            else [import_node.names]
        )
        for n in names:
            if isinstance(n, cst.ImportAlias):
                fname = n.name.value
                alias = n.asname.name.value if n.asname else fname
                aliases[fname] = alias
        return aliases

    def _extract_features_from_import(
        self,
        node: cst.ImportFrom,
    ) -> List[BackportFeature]:
        if isinstance(node.names, cst.ImportStar):
            return []
        names = node.names if isinstance(node.names, (list, tuple)) else [node.names]
        out = []
        for name_item in names:
            if isinstance(name_item, cst.ImportAlias):
                fname = name_item.name.value
                if fname in self.lookup:
                    out.append(self.lookup[fname])
        return out

    def _record_from_import_usages(self, node: cst.ImportFrom) -> None:
        if isinstance(node.names, cst.ImportStar):
            return
        names = node.names if isinstance(node.names, (list, tuple)) else [node.names]
        for name_item in names:
            if not isinstance(name_item, cst.ImportAlias):
                continue
            fname = name_item.name.value
            if fname not in self.lookup:
                continue
            alias = name_item.asname.name.value if name_item.asname else fname
            self.usages.append(
                _UsageInfo(
                    feature=self.lookup[fname],
                    alias=alias,
                    import_style="from_source",
                    scope_path=tuple(self._scope_stack),
                ),
            )

    # -- existing version check detection (idempotence) --------------------

    def _detect_existing_version_check(self, node: cst.If) -> None:
        if not self._is_version_check(node.test):
            return
        version = self._extract_version_info(node.test)
        if not version:
            return
        check_type = self._classify_version_check(node)
        if check_type:
            scope = tuple(self._scope_stack[:-1])
            self._existing_version_checks.add((scope, version, check_type))

    def _is_version_check(self, test_node: cst.BaseExpression) -> bool:
        if not isinstance(test_node, cst.Comparison):
            return False
        left = test_node.left
        return (
            isinstance(left, cst.Attribute)
            and isinstance(left.value, cst.Name)
            and left.value.value == "sys"
            and isinstance(left.attr, cst.Name)
            and left.attr.value == "version_info"
        )

    def _extract_version_info(
        self,
        test_node: cst.BaseExpression,
    ) -> Optional[Tuple[int, int]]:
        if not isinstance(test_node, cst.Comparison):
            return None
        if len(test_node.comparisons) != 1:
            return None
        comparator = test_node.comparisons[0].comparator
        if not isinstance(comparator, cst.Tuple):
            return None
        if len(comparator.elements) != 2:
            return None
        try:
            major = int(comparator.elements[0].value.value)  # type: ignore[attr-defined]
            minor = int(comparator.elements[1].value.value)  # type: ignore[attr-defined]
        # Parens kept until PEP 758 backport (#23) lands; without them
        # ruff format on target-version=py315 emits the PEP 758 form
        # which pre-3.14 Pythons can't parse.
        except (AttributeError, ValueError):  # fmt: skip
            return None
        return (major, minor)

    def _classify_version_check(self, node: cst.If) -> Optional[str]:
        # Only treat an existing version-check as ours if it manipulates a
        # name in *this* config's feature lookup. Without this, an unrelated
        # block like ``if py>=3.9: from collections.abc import Mapping else:
        # from typing import Mapping`` would be misidentified as a pre-existing
        # check for the (collections -> typing) config because it also imports
        # from typing.
        src = self.config.source_module
        fb = self.config.fallback_module

        def _import_touches_a_feature(substmt: cst.ImportFrom) -> bool:
            if not (
                _module_matches(substmt.module, src)
                or _module_matches(substmt.module, fb)
            ):
                return False
            names = self._import_alias_names(substmt)
            return any(n in self.lookup for n in names)

        for stmt in node.body.body:
            if not isinstance(stmt, cst.SimpleStatementLine):
                continue
            for substmt in stmt.body:
                if isinstance(substmt, cst.Assign):
                    val = substmt.value
                    target = substmt.targets[0].target if substmt.targets else None
                    if (
                        isinstance(val, cst.Attribute)
                        and _module_matches(val.value, fb)
                        and isinstance(target, cst.Attribute)
                        and _module_matches(target.value, src)
                        and isinstance(target.attr, cst.Name)
                        and target.attr.value in self.lookup
                    ):
                        return "assignment"
                elif isinstance(
                    substmt,
                    cst.ImportFrom,
                ) and _import_touches_a_feature(substmt):
                    return "conditional_import"
        if isinstance(node.orelse, cst.Else):
            for stmt in node.orelse.body.body:
                if not isinstance(stmt, cst.SimpleStatementLine):
                    continue
                for substmt in stmt.body:
                    if isinstance(
                        substmt,
                        cst.ImportFrom,
                    ) and _import_touches_a_feature(substmt):
                        return "conditional_import"
        return None

    @staticmethod
    def _import_alias_names(import_node: cst.ImportFrom) -> List[str]:
        if isinstance(import_node.names, cst.ImportStar):
            return []
        names = (
            import_node.names
            if isinstance(import_node.names, (list, tuple))
            else [import_node.names]
        )
        out: List[str] = []
        for n in names:
            if isinstance(n, cst.ImportAlias) and isinstance(n.name, cst.Name):
                out.append(n.name.value)
        return out


# ---------------------------------------------------------------------------
# Pass 2: transformation
# ---------------------------------------------------------------------------


class _BackportTransformer(cst.CSTTransformer):
    def __init__(self, analysis: _AnalysisVisitor) -> None:
        self.config = analysis.config
        self.analysis = analysis
        self.import_manager = analysis.import_manager
        self.usages = analysis.usages
        self.import_statements = analysis.import_statements
        self.existing_version_checks = analysis._existing_version_checks

        self.source_dot_assignments = self._plan_source_dot_assignments()
        self._applied_assignments: Set[
            Tuple[Tuple[cst.CSTNode, ...], Tuple[int, int]]
        ] = set()
        self._current_scope_stack: List[cst.CSTNode] = []

    # -- planning -----------------------------------------------------------

    def _find_source_import_scope(self) -> Tuple[cst.CSTNode, ...]:
        deepest: Tuple[cst.CSTNode, ...] = ()
        max_depth = -1
        for scope_path in self.analysis._source_import_scopes.keys():
            if len(scope_path) > max_depth:
                max_depth = len(scope_path)
                deepest = scope_path
        return deepest

    def _plan_source_dot_assignments(
        self,
    ) -> Dict[Tuple[cst.CSTNode, ...], Dict[str, BackportFeature]]:
        assignments: Dict[
            Tuple[cst.CSTNode, ...],
            Dict[str, BackportFeature],
        ] = defaultdict(dict)
        dot_usages = [u for u in self.usages if u.import_style == "source_dot"]
        if not dot_usages:
            return {}
        common_scope = self._find_common_scope_path(
            [u.scope_path for u in dot_usages],
        )
        import_scope = self._find_source_import_scope()
        if (
            common_scope
            and len(common_scope) > len(import_scope)
            and len(common_scope) == 1
            and isinstance(common_scope[0], cst.If)
        ):
            optimal = common_scope
        else:
            optimal = import_scope
        groups: Dict[str, List[_UsageInfo]] = defaultdict(list)
        for u in dot_usages:
            groups[u.feature.name].append(u)
        for fname, us in groups.items():
            assignments[optimal][fname] = us[0].feature
        return dict(assignments)

    def _find_common_scope_path(
        self,
        paths: List[Tuple[cst.CSTNode, ...]],
    ) -> Tuple[cst.CSTNode, ...]:
        if not paths:
            return ()
        if len(paths) == 1:
            return paths[0]
        common: Tuple[cst.CSTNode, ...] = ()
        depth = min(len(p) for p in paths)
        for i in range(depth):
            if all(p[i] is paths[0][i] for p in paths):
                common = common + (paths[0][i],)
            else:
                break
        return common

    def _version_check_exists(
        self,
        scope: Tuple[cst.CSTNode, ...],
        version: Tuple[int, int],
        check_type: str,
    ) -> bool:
        return (scope, version, check_type) in self.existing_version_checks

    # -- scope tracking during transform -----------------------------------

    def visit_If(self, node: cst.If) -> None:
        self._current_scope_stack.append(node)

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        self._current_scope_stack.append(node)

    def _is_inside_version_check(self) -> bool:
        for scope_node in self._current_scope_stack:
            if isinstance(scope_node, cst.If):
                if self.analysis._is_version_check(scope_node.test):
                    return True
        return False

    # -- Module-level emission ---------------------------------------------

    def leave_Module(
        self,
        original_node: cst.Module,
        updated_node: cst.Module,
    ) -> cst.Module:
        if not self.import_statements and not self.source_dot_assignments:
            return updated_node

        new_body = list(updated_node.body)

        # Strip transformable names out of module-level ``from source import``
        # statements; nested imports are handled in leave_SimpleStatementLine.
        module_imports = [s for s in self.import_statements if not s.scope_path]
        for imp in module_imports:
            for feature in imp.features:
                new_body = self.import_manager.remove_from_imports(
                    new_body,
                    self.config.source_module,
                    feature.name,
                )

        new_body = self.import_manager.ensure_sys_import(new_body)

        module_assignments = self.source_dot_assignments.get((), {})
        if module_assignments:
            new_body = _ensure_direct_import(
                new_body,
                self.config.source_module,
                self.import_manager.find_import_position,
            )

        version_check_pos = self.import_manager.find_post_import_position(new_body)
        all_transforms: List[Tuple[Tuple[int, int], str, list]] = []

        from_source_usages = [
            u
            for u in self.usages
            if u.import_style == "from_source" and not u.scope_path
        ]
        if from_source_usages:
            groups: Dict[Tuple[int, int], List[_UsageInfo]] = defaultdict(list)
            for u in from_source_usages:
                groups[u.feature.min_version].append(u)
            for version, usages in groups.items():
                if not self._version_check_exists((), version, "conditional_import"):
                    all_transforms.append((version, "conditional_import", usages))

        if module_assignments:
            groups2: Dict[Tuple[int, int], List[Tuple[str, BackportFeature]]] = (
                defaultdict(list)
            )
            for fname, feature in module_assignments.items():
                groups2[feature.min_version].append((fname, feature))
            for version, features in groups2.items():
                key = ((), version)
                if (
                    key not in self._applied_assignments
                    and not self._version_check_exists((), version, "assignment")
                ):
                    self._applied_assignments.add(key)
                    all_transforms.append((version, "assignment", features))

        all_transforms.sort(key=lambda x: (x[1] == "conditional_import", x[0]))

        for version, kind, data in all_transforms:
            if kind == "conditional_import":
                block = self._make_conditional_import_check(
                    version,
                    data,
                    nested=False,
                )
            else:
                block = self._make_assignment_check(version, data, nested=False)
            new_body.insert(version_check_pos, block)
            version_check_pos += 1

        return updated_node.with_changes(body=new_body)

    def leave_If(
        self,
        original_node: cst.If,
        updated_node: cst.If,
    ) -> cst.If:
        current = None
        for scope_path in self.source_dot_assignments.keys():
            if scope_path and scope_path[-1] is original_node:
                current = scope_path
                break
        if not current:
            return updated_node
        scope_assignments = self.source_dot_assignments[current]
        if not scope_assignments:
            return updated_node

        new_body = list(updated_node.body.body)
        insert_pos = 0
        for i, stmt in enumerate(new_body):
            if isinstance(stmt, cst.SimpleStatementLine) and any(
                isinstance(s, (cst.Import, cst.ImportFrom)) for s in stmt.body
            ):
                insert_pos = i + 1
            else:
                break

        groups: Dict[Tuple[int, int], List[Tuple[str, BackportFeature]]] = defaultdict(
            list,
        )
        for fname, feature in scope_assignments.items():
            groups[feature.min_version].append((fname, feature))

        for version in sorted(groups.keys()):
            features = groups[version]
            key = (current, version)
            if key in self._applied_assignments:
                continue
            if self._version_check_exists(current, version, "assignment"):
                continue
            self._applied_assignments.add(key)
            block = self._make_assignment_check(version, features, nested=True)
            new_body.insert(insert_pos, block)
            insert_pos += 1

        if insert_pos > 0:
            return updated_node.with_changes(body=cst.IndentedBlock(body=new_body))
        return updated_node

    def leave_SimpleStatementLine(
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ):
        if len(updated_node.body) != 1:
            return updated_node
        stmt = updated_node.body[0]
        if not isinstance(stmt, cst.ImportFrom):
            return updated_node
        if not _module_matches(stmt.module, self.config.source_module):
            return updated_node
        if self._is_inside_version_check():
            return updated_node

        current_names = self._extract_import_names(stmt)
        transformable: List[BackportFeature] = []
        actual_scope: Tuple[cst.CSTNode, ...] = ()
        for info in self.import_statements:
            if not info.scope_path:
                continue
            for feature in info.features:
                if feature.name in current_names:
                    transformable.append(feature)
                    actual_scope = info.scope_path
        if not transformable:
            return updated_node

        transformable_names = {f.name for f in transformable}
        if current_names != transformable_names:
            return updated_node

        feature_aliases = self._extract_feature_aliases(stmt)
        # Build usages grouped by version.
        version_groups: Dict[Tuple[int, int], List[_UsageInfo]] = defaultdict(list)
        for feature in transformable:
            alias = feature_aliases.get(feature.name, feature.name)
            version_groups[feature.min_version].append(
                _UsageInfo(
                    feature=feature,
                    alias=alias,
                    import_style="from_source",
                    scope_path=actual_scope,
                ),
            )
        out = []
        for version in sorted(version_groups.keys()):
            out.append(
                self._make_conditional_import_check(
                    version,
                    version_groups[version],
                    nested=bool(actual_scope),
                ),
            )
        return cst.FlattenSentinel(out)

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _extract_import_names(import_node: cst.ImportFrom) -> set:
        if isinstance(import_node.names, cst.ImportStar):
            return {"*"}
        names = (
            import_node.names
            if isinstance(import_node.names, (list, tuple))
            else [import_node.names]
        )
        out = set()
        for n in names:
            if isinstance(n, cst.ImportAlias):
                out.add(n.name.value)
        return out

    @staticmethod
    def _extract_feature_aliases(import_node: cst.ImportFrom) -> dict:
        aliases: Dict[str, str] = {}
        if isinstance(import_node.names, cst.ImportStar):
            return aliases
        names = (
            import_node.names
            if isinstance(import_node.names, (list, tuple))
            else [import_node.names]
        )
        for n in names:
            if isinstance(n, cst.ImportAlias):
                fname = n.name.value
                alias = n.asname.name.value if n.asname else fname
                aliases[fname] = alias
        return aliases

    # -- output construction -----------------------------------------------

    def _version_condition(
        self,
        version: Tuple[int, int],
        op: cst.BaseCompOp,
    ) -> cst.Comparison:
        return cst.Comparison(
            left=cst.Attribute(
                value=cst.Name("sys"),
                attr=cst.Name("version_info"),
            ),
            comparisons=[
                cst.ComparisonTarget(
                    operator=op,
                    comparator=cst.Tuple(
                        [
                            cst.Element(cst.Integer(str(version[0]))),
                            cst.Element(cst.Integer(str(version[1]))),
                        ],
                    ),
                ),
            ],
        )

    def _make_conditional_import_check(
        self,
        version: Tuple[int, int],
        usages: List[_UsageInfo],
        nested: bool,
    ) -> cst.If:
        # ``if sys.version_info >= (X, Y): from source import ... else: from fallback import ...``
        source_aliases = [self._import_alias(u.feature.name, u.alias) for u in usages]
        fallback_aliases = [
            self._import_alias(u.feature.effective_fallback_name, u.alias)
            for u in usages
        ]
        if_body = cst.IndentedBlock(
            [
                cst.SimpleStatementLine(
                    [
                        cst.ImportFrom(
                            module=_dotted_to_cst(self.config.source_module),
                            names=source_aliases,
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
                            module=_dotted_to_cst(self.config.fallback_module),
                            names=fallback_aliases,
                        ),
                    ],
                ),
            ],
        )
        return cst.If(
            test=self._version_condition(version, cst.GreaterThanEqual()),
            body=if_body,
            orelse=cst.Else(body=else_body),
            leading_lines=[] if nested else [cst.EmptyLine()],
        )

    def _make_assignment_check(
        self,
        version: Tuple[int, int],
        features: List[Tuple[str, BackportFeature]],
        nested: bool,
    ) -> cst.If:
        # ``if sys.version_info < (X, Y): import fallback; source.X = fallback.X``
        statements: List[cst.SimpleStatementLine] = [
            cst.SimpleStatementLine(
                [
                    cst.Import(
                        [cst.ImportAlias(_dotted_to_cst(self.config.fallback_module))],
                    ),
                ],
            ),
        ]
        for fname, feature in features:
            statements.append(
                cst.SimpleStatementLine(
                    [
                        cst.Assign(
                            targets=[
                                cst.AssignTarget(
                                    cst.Attribute(
                                        value=_dotted_to_cst(self.config.source_module),
                                        attr=cst.Name(fname),
                                    ),
                                ),
                            ],
                            value=cst.Attribute(
                                value=_dotted_to_cst(self.config.fallback_module),
                                attr=cst.Name(feature.effective_fallback_name),
                            ),
                        ),
                    ],
                ),
            )
        return cst.If(
            test=self._version_condition(version, cst.LessThan()),
            body=cst.IndentedBlock(statements),
            leading_lines=[] if nested else [cst.EmptyLine()],
        )

    @staticmethod
    def _import_alias(name: str, alias: str) -> cst.ImportAlias:
        if name == alias:
            return cst.ImportAlias(name=cst.Name(name))
        return cst.ImportAlias(
            name=cst.Name(name),
            asname=cst.AsName(name=cst.Name(alias)),
        )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def transform_module(module: cst.Module, config: BackportConfig) -> cst.Module:
    """CST-in / CST-out form, for use inside the converter pipeline."""
    analyzer = _AnalysisVisitor(config)
    module.visit(analyzer)
    transformer = _BackportTransformer(analyzer)
    return module.visit(transformer)


def transform(source_code: str, config: BackportConfig) -> str:
    """Source-in / source-out form, used directly by tests."""
    module = cst.parse_module(source_code)
    transformed = transform_module(module, config)
    code = transformed.code
    # FIXME: we should not be producing empty lines with whitespace in the
    # first place — same workaround as the original typing_extensions impl.
    code = "\n".join(line if line.strip() else "" for line in code.splitlines()) + "\n"
    return code
