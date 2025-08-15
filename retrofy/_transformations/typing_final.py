from __future__ import annotations

from typing import List

import libcst as cst


class TypingFinalTransformer(cst.CSTTransformer):
    """
    Transform @final decorators to use sys.version_info checks for compatibility.

    This transformation wraps typing.final usage in version checks for Python < 3.8
    compatibility, using lambda cls: cls as fallback.
    """

    def __init__(self) -> None:
        self.has_final_usage = False
        self.final_import_style: str | None = None  # "from_typing" or "typing_dot"
        self.has_sys_import = False
        self.final_alias: str | None = (
            None  # Store the alias name if final is imported as alias
        )

    def leave_Module(
        self,
        original_node: cst.Module,
        updated_node: cst.Module,
    ) -> cst.Module:
        """Transform the entire module to add version compatibility checks."""
        # First, scan for final imports to detect aliases
        self._scan_for_final_imports(updated_node.body)

        if not self.has_final_usage:
            return updated_node

        # Check if sys is already imported
        self._check_existing_sys_import(updated_node.body)

        # Remove final from existing imports and update them
        new_body = self._update_imports(list(updated_node.body))

        # Add sys import if needed
        if not self.has_sys_import:
            sys_insert_position = self._find_sys_import_position(new_body)
            sys_import = self._create_sys_import()
            new_body.insert(sys_insert_position, sys_import)

        # Find position to insert version check after imports
        version_check_position = self._find_version_check_position(new_body)
        version_check = self._create_version_check_block()
        new_body.insert(version_check_position, version_check)

        return updated_node.with_changes(body=new_body)

    def leave_Decorator(
        self,
        original_node: cst.Decorator,
        updated_node: cst.Decorator,
    ) -> cst.Decorator:
        """Transform @final decorators to use the version-compatible alias."""

        # Check for @final or @alias_name (where alias_name is an alias for final)
        if isinstance(updated_node.decorator, cst.Name):
            decorator_name = updated_node.decorator.value
            # We need to determine if this decorator is referring to final
            # We'll detect this during import processing and store the alias
            if decorator_name == "final" or (
                self.final_alias and decorator_name == self.final_alias
            ):
                self.has_final_usage = True
                self.final_import_style = "from_typing"
                # Keep using the same decorator name
                return updated_node

        # Check for @typing.final
        if (
            isinstance(updated_node.decorator, cst.Attribute)
            and isinstance(updated_node.decorator.value, cst.Name)
            and updated_node.decorator.value.value == "typing"
            and updated_node.decorator.attr.value == "final"
        ):
            self.has_final_usage = True
            self.final_import_style = "typing_dot"
            return updated_node.with_changes(decorator=cst.Name("__typing_final"))

        return updated_node

    def _scan_for_final_imports(self, body) -> None:
        """Scan the module body to detect final imports and their aliases."""
        for stmt in body:
            if isinstance(stmt, cst.SimpleStatementLine):
                for substmt in stmt.body:
                    if (
                        isinstance(substmt, cst.ImportFrom)
                        and substmt.module
                        and isinstance(substmt.module, cst.Name)
                        and substmt.module.value == "typing"
                    ):
                        # Check if final is in the import list
                        if isinstance(substmt.names, (list, tuple)):
                            for name in substmt.names:
                                if (
                                    isinstance(name, cst.ImportAlias)
                                    and isinstance(name.name, cst.Name)
                                    and name.name.value == "final"
                                ):
                                    self.has_final_usage = True
                                    self.final_import_style = "from_typing"
                                    if name.asname:
                                        # final imported with alias
                                        self.final_alias = name.asname.name.value
                                    else:
                                        # final imported without alias
                                        self.final_alias = "final"
                                    return

    def _check_existing_sys_import(self, body) -> None:
        """Check if sys is already imported early in the module."""
        for i, stmt in enumerate(body):
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
                        self.has_sys_import = True
                        return

                # If we hit non-import statements, stop looking for sys imports
                # as we only want sys imports that come before the version check
                if not has_imports:
                    break
            else:
                # If we hit a non-simple statement, stop looking
                break

    def _update_imports(self, body: List[cst.BaseStatement]) -> List[cst.BaseStatement]:
        """Remove final from existing typing imports."""
        if self.final_import_style != "from_typing":
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
                        and substmt.module.value == "typing"
                    ):
                        # Filter out final from the import
                        if isinstance(substmt.names, (list, tuple)):
                            new_names = []
                            for name in substmt.names:
                                if (
                                    isinstance(name, cst.ImportAlias)
                                    and isinstance(name.name, cst.Name)
                                    and name.name.value != "final"
                                ):
                                    new_names.append(name)

                            # Only keep the import if there are other names
                            if new_names:
                                new_substmt = substmt.with_changes(names=new_names)
                                new_substmts.append(new_substmt)
                        else:
                            # Single name or star import - keep as is if not final
                            if not (
                                isinstance(substmt.names, cst.ImportAlias)
                                and isinstance(substmt.names.name, cst.Name)
                                and substmt.names.name.value == "final"
                            ):
                                new_substmts.append(substmt)
                    else:
                        new_substmts.append(substmt)

                if new_substmts:
                    new_body.append(stmt.with_changes(body=new_substmts))
            else:
                new_body.append(stmt)

        return new_body

    def _create_sys_import(self) -> cst.SimpleStatementLine:
        """Create sys import statement."""
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

    def _find_sys_import_position(self, body: List[cst.BaseStatement]) -> int:
        """Find the correct position to insert sys import."""
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

    def _find_version_check_position(self, body: List[cst.BaseStatement]) -> int:
        """Find the correct position to insert version check after all imports."""
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

    def _find_import_insertion_position(self, body: List[cst.BaseStatement]) -> int:
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
                    # Skip all imports
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

    def _create_version_check_block(self) -> cst.If:
        """Create the version check block for typing.final compatibility."""

        # Create the condition: sys.version_info >= (3, 8)
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
                            cst.Element(cst.Integer("3")),
                            cst.Element(cst.Integer("8")),
                        ],
                    ),
                ),
            ],
        )

        # Create the if body based on import style
        if self.final_import_style == "from_typing":
            # Determine the import and assignment name
            alias_name = self.final_alias or "final"

            # from typing import final [as alias_name]
            if alias_name == "final":
                import_alias = cst.ImportAlias(name=cst.Name("final"))
            else:
                import_alias = cst.ImportAlias(
                    name=cst.Name("final"),
                    asname=cst.AsName(name=cst.Name(alias_name)),
                )

            if_body = cst.IndentedBlock(
                [
                    cst.SimpleStatementLine(
                        [
                            cst.ImportFrom(
                                module=cst.Name("typing"),
                                names=[import_alias],
                            ),
                        ],
                    ),
                ],
            )

            # alias_name = lambda cls: cls
            else_body = cst.IndentedBlock(
                [
                    cst.SimpleStatementLine(
                        [
                            cst.Assign(
                                targets=[cst.AssignTarget(cst.Name(alias_name))],
                                value=cst.Lambda(
                                    params=cst.Parameters(
                                        [
                                            cst.Param(cst.Name("cls")),
                                        ],
                                    ),
                                    body=cst.Name("cls"),
                                ),
                            ),
                        ],
                    ),
                ],
            )
        else:  # typing_dot
            # from typing import final as __typing_final
            if_body = cst.IndentedBlock(
                [
                    cst.SimpleStatementLine(
                        [
                            cst.ImportFrom(
                                module=cst.Name("typing"),
                                names=[
                                    cst.ImportAlias(
                                        name=cst.Name("final"),
                                        asname=cst.AsName(
                                            name=cst.Name("__typing_final"),
                                        ),
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            )

            # __typing_final = lambda cls: cls
            else_body = cst.IndentedBlock(
                [
                    cst.SimpleStatementLine(
                        [
                            cst.Assign(
                                targets=[cst.AssignTarget(cst.Name("__typing_final"))],
                                value=cst.Lambda(
                                    params=cst.Parameters(
                                        [
                                            cst.Param(cst.Name("cls")),
                                        ],
                                    ),
                                    body=cst.Name("cls"),
                                ),
                            ),
                        ],
                    ),
                ],
            )

        # Create the complete if statement
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
