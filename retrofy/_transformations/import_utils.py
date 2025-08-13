"""Utilities for managing imports in transformations."""

from typing import Tuple

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
