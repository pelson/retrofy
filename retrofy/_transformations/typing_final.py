from __future__ import annotations

import libcst as cst

from .import_utils import EnhancedImportManager


class TypingFinalTransformer(cst.CSTTransformer):
    """
    Transform @final decorators to use sys.version_info checks for compatibility.

    This transformation wraps typing.final usage in version checks for Python < 3.8
    compatibility, using lambda cls: cls as fallback.
    """

    def __init__(self) -> None:
        self.import_manager = EnhancedImportManager()
        self.has_final_usage = False
        self.final_import_style: str | None = None  # "from_typing" or "typing_dot"
        self.final_alias: str | None = None

    def leave_Module(
        self,
        original_node: cst.Module,
        updated_node: cst.Module,
    ) -> cst.Module:
        """Transform the entire module to add version compatibility checks."""
        # Scan for imports to detect final usage and aliases
        self.import_manager.scan_imports(updated_node.body)
        self._detect_final_usage()

        if not self.has_final_usage:
            return updated_node

        # Start with the updated body as a list
        new_body = list(updated_node.body)

        # Remove final from existing imports if needed
        if self.final_import_style == "from_typing":
            new_body = self.import_manager.remove_from_imports(
                new_body,
                "typing",
                "final",
            )

        # Ensure sys import is present
        new_body = self.import_manager.ensure_sys_import(new_body)

        # Add version check after imports
        version_check_position = self.import_manager.find_post_import_position(new_body)
        version_check = self._create_version_check_block()
        new_body.insert(version_check_position, version_check)

        return updated_node.with_changes(body=new_body)

    def _detect_final_usage(self) -> None:
        """Detect if final is imported and determine usage style."""
        if self.import_manager.has_import("typing", "final"):
            self.has_final_usage = True
            self.final_import_style = "from_typing"
            self.final_alias = self.import_manager.get_import_alias("typing", "final")

    def leave_Decorator(
        self,
        original_node: cst.Decorator,
        updated_node: cst.Decorator,
    ) -> cst.Decorator:
        """Transform @final decorators to use the version-compatible alias."""

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
