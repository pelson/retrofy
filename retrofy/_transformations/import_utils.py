"""Utilities for managing imports in transformations."""

from typing import Dict, List, Optional, Tuple, Union

import libcst as cst


class ImportManager:
    """Helper class for managing automatic imports in transformations."""

    def __init__(self):
        self._required_imports = set()

    def require_import(self, import_name: str) -> None:
        """Mark an import as required."""
        self._required_imports.add(import_name)

    def apply_imports(self, module: cst.Module) -> cst.Module:
        """Add all required imports to the module."""
        if not self._required_imports:
            return module

        # Find the correct position to insert imports
        insert_position = self._find_import_position(module.body)

        new_stmts = list(module.body)
        for import_name in sorted(self._required_imports):
            import_stmt = self._create_import_statement(import_name)
            new_stmts.insert(insert_position, import_stmt)
            insert_position += 1

        return module.with_changes(body=tuple(new_stmts))

    def _create_import_statement(self, import_name: str) -> cst.SimpleStatementLine:
        """Create an import statement for the given import name."""
        if "." in import_name:
            # Handle dotted imports like "collections.abc"
            parts = import_name.split(".")
            import_node = cst.Name(parts[0])
            for part in parts[1:]:
                import_node = cst.Attribute(import_node, cst.Name(part))
        else:
            # Simple import like "typing"
            import_node = cst.Name(import_name)

        return cst.SimpleStatementLine(
            [
                cst.Import(
                    [
                        cst.ImportAlias(import_node),
                    ],
                ),
            ],
            trailing_whitespace=cst.TrailingWhitespace(
                newline=cst.Newline(),
            ),
        )

    def _find_import_position(
        self,
        body: Tuple[cst.BaseStatement, ...],
    ) -> int:
        """Find the correct position to insert imports."""
        position = 0

        # Skip module docstrings
        if body and isinstance(body[0], cst.SimpleStatementLine):
            if (
                len(body[0].body) == 1
                and isinstance(body[0].body[0], cst.Expr)
                and isinstance(body[0].body[0].value, cst.SimpleString)
            ):
                position = 1

        # Skip __future__ imports
        for i in range(position, len(body)):
            stmt = body[i]
            if isinstance(stmt, cst.SimpleStatementLine):
                for substmt in stmt.body:
                    if (
                        isinstance(substmt, cst.ImportFrom)
                        and substmt.module
                        and isinstance(substmt.module, cst.Attribute)
                        and substmt.module.attr.value == "__future__"
                    ):
                        position = i + 1
                        break
                    elif (
                        isinstance(substmt, cst.ImportFrom)
                        and substmt.module
                        and isinstance(substmt.module, cst.Name)
                        and substmt.module.value == "__future__"
                    ):
                        position = i + 1
                        break
                else:
                    # If we didn't find a __future__ import in this statement, stop looking
                    break
            else:
                # If we hit a non-simple statement, stop looking
                break

        return position


class ImportInfo:
    """Information about an import found in the module."""

    def __init__(
        self,
        module_name: str,
        imported_name: str,
        alias: Optional[str] = None,
        stmt_index: int = 0,
        import_index: int = 0,
    ):
        self.module_name = module_name  # e.g., "typing"
        self.imported_name = imported_name  # e.g., "final"
        self.alias = alias  # e.g., "fi_na_l" if imported as "final as fi_na_l"
        self.stmt_index = stmt_index  # Index in module body
        self.import_index = import_index  # Index within the import statement

    @property
    def effective_name(self) -> str:
        """The name used to reference this import in code."""
        return self.alias if self.alias else self.imported_name


class EnhancedImportManager:
    """Enhanced import manager with sophisticated import manipulation capabilities."""

    def __init__(self):
        self._import_info: Dict[str, List[ImportInfo]] = {}
        self._has_sys_import = False

    def scan_imports(
        self,
        body: Union[Tuple[cst.BaseStatement, ...], List[cst.BaseStatement]],
    ) -> None:
        """Scan module body to detect existing imports and their aliases."""
        self._import_info.clear()
        self._has_sys_import = False

        for stmt_idx, stmt in enumerate(body):
            if isinstance(stmt, cst.SimpleStatementLine):
                for substmt in stmt.body:
                    if isinstance(substmt, cst.ImportFrom) and substmt.module:
                        self._scan_import_from(substmt, stmt_idx)
                    elif isinstance(substmt, cst.Import):
                        self._scan_import(substmt, stmt_idx)

    def _scan_import_from(self, import_stmt: cst.ImportFrom, stmt_idx: int) -> None:
        """Scan a 'from X import Y' statement."""
        if not isinstance(import_stmt.module, cst.Name):
            return

        module_name = import_stmt.module.value

        if isinstance(import_stmt.names, (list, tuple)):
            for import_idx, name in enumerate(import_stmt.names):
                if isinstance(name, cst.ImportAlias) and isinstance(
                    name.name,
                    cst.Name,
                ):
                    imported_name = name.name.value
                    alias = name.asname.name.value if name.asname else None

                    info = ImportInfo(
                        module_name=module_name,
                        imported_name=imported_name,
                        alias=alias,
                        stmt_index=stmt_idx,
                        import_index=import_idx,
                    )

                    if module_name not in self._import_info:
                        self._import_info[module_name] = []
                    self._import_info[module_name].append(info)

    def _scan_import(self, import_stmt: cst.Import, stmt_idx: int) -> None:
        """Scan a direct 'import X' statement."""
        for alias in import_stmt.names:
            if isinstance(alias.name, cst.Name) and alias.name.value == "sys":
                self._has_sys_import = True
                break

    def has_import(self, module_name: str, imported_name: str) -> bool:
        """Check if a specific import exists."""
        if module_name not in self._import_info:
            return False
        return any(
            info.imported_name == imported_name
            for info in self._import_info[module_name]
        )

    def get_import_alias(self, module_name: str, imported_name: str) -> Optional[str]:
        """Get the alias for an import, or None if not found or no alias."""
        if module_name not in self._import_info:
            return None
        for info in self._import_info[module_name]:
            if info.imported_name == imported_name:
                return info.effective_name
        return None

    def remove_from_imports(
        self,
        body: List[cst.BaseStatement],
        module_name: str,
        imported_name: str,
    ) -> List[cst.BaseStatement]:
        """Remove a specific import from existing import statements."""
        if module_name not in self._import_info:
            return body

        new_body = []
        for stmt in body:
            if isinstance(stmt, cst.SimpleStatementLine):
                new_substmts = []
                for substmt in stmt.body:
                    if (
                        isinstance(substmt, cst.ImportFrom)
                        and substmt.module
                        and isinstance(substmt.module, cst.Name)
                        and substmt.module.value == module_name
                    ):
                        # Filter out the specific import
                        if isinstance(substmt.names, (list, tuple)):
                            new_names = []
                            for name in substmt.names:
                                if (
                                    isinstance(name, cst.ImportAlias)
                                    and isinstance(name.name, cst.Name)
                                    and name.name.value != imported_name
                                ):
                                    new_names.append(name)

                            # Only keep the import if there are other names
                            if new_names:
                                new_substmt = substmt.with_changes(names=new_names)
                                new_substmts.append(new_substmt)
                        else:
                            # Single name import - keep if not the target
                            if not (
                                isinstance(substmt.names, cst.ImportAlias)
                                and isinstance(substmt.names.name, cst.Name)
                                and substmt.names.name.value == imported_name
                            ):
                                new_substmts.append(substmt)
                    else:
                        new_substmts.append(substmt)

                if new_substmts:
                    new_body.append(stmt.with_changes(body=new_substmts))
            else:
                new_body.append(stmt)

        return new_body

    def ensure_sys_import(
        self,
        body: List[cst.BaseStatement],
    ) -> List[cst.BaseStatement]:
        """Ensure sys is imported early in the module."""
        # Check if sys is already imported in import section
        early_sys_import = self._check_early_sys_import(body)

        if not early_sys_import:
            # Add sys import at the appropriate position
            insert_position = self.find_import_position(body)
            sys_import = self._create_sys_import()
            body.insert(insert_position, sys_import)

        return body

    def _check_early_sys_import(self, body: List[cst.BaseStatement]) -> bool:
        """Check if sys is imported early in the module (before non-import statements)."""
        for stmt in body:
            if isinstance(stmt, cst.SimpleStatementLine):
                # Check if this is an import statement
                has_imports = any(
                    isinstance(substmt, (cst.Import, cst.ImportFrom))
                    for substmt in stmt.body
                )

                for substmt in stmt.body:
                    if isinstance(substmt, cst.Import) and any(
                        isinstance(alias.name, cst.Name) and alias.name.value == "sys"
                        for alias in substmt.names
                    ):
                        return True

                # If we hit non-import statements, stop looking
                if not has_imports:
                    break
            else:
                # If we hit a non-simple statement, stop looking
                break

        return False

    def _create_sys_import(self) -> cst.SimpleStatementLine:
        """Create a sys import statement."""
        return cst.SimpleStatementLine(
            [
                cst.Import(
                    [
                        cst.ImportAlias(
                            cst.Name("sys"),
                        ),
                    ],
                ),
            ],
            trailing_whitespace=cst.TrailingWhitespace(
                newline=cst.Newline(),
            ),
        )

    def find_import_position(self, body: List[cst.BaseStatement]) -> int:
        """Find the correct position to insert imports (after __future__ imports)."""
        position = 0

        # Skip module docstrings
        if body and isinstance(body[0], cst.SimpleStatementLine):
            if (
                len(body[0].body) == 1
                and isinstance(body[0].body[0], cst.Expr)
                and isinstance(body[0].body[0].value, cst.SimpleString)
            ):
                position = 1

        # Skip __future__ imports
        for i in range(position, len(body)):
            stmt = body[i]
            if isinstance(stmt, cst.SimpleStatementLine):
                for substmt in stmt.body:
                    if (
                        isinstance(substmt, cst.ImportFrom)
                        and substmt.module
                        and isinstance(substmt.module, cst.Attribute)
                        and substmt.module.attr.value == "__future__"
                    ):
                        position = i + 1
                        break
                    elif (
                        isinstance(substmt, cst.ImportFrom)
                        and substmt.module
                        and isinstance(substmt.module, cst.Name)
                        and substmt.module.value == "__future__"
                    ):
                        position = i + 1
                        break
                else:
                    break
            else:
                break

        return position

    def find_post_import_position(self, body: List[cst.BaseStatement]) -> int:
        """Find the position after all imports (for adding conditional blocks)."""
        position = 0

        # Skip module docstrings
        if body and isinstance(body[0], cst.SimpleStatementLine):
            if (
                len(body[0].body) == 1
                and isinstance(body[0].body[0], cst.Expr)
                and isinstance(body[0].body[0].value, cst.SimpleString)
            ):
                position = 1

        # Skip all imports
        for i in range(position, len(body)):
            stmt = body[i]
            if isinstance(stmt, cst.SimpleStatementLine):
                # If this statement contains any imports, skip it
                if any(
                    isinstance(substmt, (cst.Import, cst.ImportFrom))
                    for substmt in stmt.body
                ):
                    position = i + 1
                else:
                    break
            else:
                break

        return position

    def create_conditional_import(
        self,
        condition: cst.Comparison,
        if_import: cst.ImportFrom,
        else_assignment: cst.Assign,
    ) -> cst.If:
        """Create a conditional import block with version check."""
        if_body = cst.IndentedBlock([cst.SimpleStatementLine([if_import])])
        else_body = cst.IndentedBlock([cst.SimpleStatementLine([else_assignment])])

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
