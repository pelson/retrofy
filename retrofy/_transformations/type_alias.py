from __future__ import annotations

import libcst as cst


class TypeAliasTransformer(cst.CSTTransformer):
    """
    A transformer that replaces type statements (PEP 695) with pre-3.12 equivalent syntax.

    Transforms:
        type Point = tuple[float, float]
    Into:
        Point = tuple[float, float]

    And for generic type aliases:
        type GenericPoint[T] = tuple[T, T]
    Into:
        T = typing.TypeVar("T")
        GenericPoint = typing.TypeAlias = tuple[T, T]

    This follows the patterns described in PEP 695 for backward compatibility.
    """

    def __init__(self) -> None:
        self.needs_typing_import = False
        self.type_vars_to_create: list[str] = []
        super().__init__()

    def leave_SimpleStatementLine(
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ) -> cst.SimpleStatementLine | cst.FlattenSentinel:
        """Transform SimpleStatementLine containing TypeAlias."""
        # Check if this line contains a type alias
        if len(updated_node.body) == 1 and isinstance(
            updated_node.body[0],
            cst.TypeAlias,
        ):
            type_alias = updated_node.body[0]
            name = type_alias.name.value
            statements: list[cst.SimpleStatementLine] = []

            # Handle generic type parameters if present
            if type_alias.type_parameters:
                self.needs_typing_import = True

                # Create TypeVar declarations for each type parameter
                for param in type_alias.type_parameters.params:
                    if isinstance(param, cst.TypeParam) and isinstance(
                        param.param,
                        cst.TypeVar,
                    ):
                        param_name = param.param.name.value

                        # Create TypeVar call
                        type_var_args = [cst.Arg(cst.SimpleString(f'"{param_name}"'))]

                        # Handle bound if present
                        if param.param.bound:
                            type_var_args.append(
                                cst.Arg(
                                    value=param.param.bound,
                                    keyword=cst.Name("bound"),
                                    equal=cst.AssignEqual(
                                        whitespace_before=cst.SimpleWhitespace(""),
                                        whitespace_after=cst.SimpleWhitespace(""),
                                    ),
                                ),
                            )

                        type_var_call = cst.Call(
                            func=cst.Attribute(
                                value=cst.Name("typing"),
                                attr=cst.Name("TypeVar"),
                            ),
                            args=type_var_args,
                        )

                        # Create assignment for TypeVar
                        type_var_assign = cst.Assign(
                            targets=[cst.AssignTarget(target=cst.Name(param_name))],
                            value=type_var_call,
                        )

                        statements.append(
                            cst.SimpleStatementLine(
                                body=[type_var_assign],
                                leading_lines=(
                                    updated_node.leading_lines
                                    if len(statements) == 0
                                    else ()
                                ),
                                trailing_whitespace=cst.TrailingWhitespace(
                                    whitespace=cst.SimpleWhitespace(""),
                                    comment=None,
                                    newline=cst.Newline(),
                                ),
                            ),
                        )

            # Create the main type alias assignment
            # For non-generic aliases, we just create a simple assignment
            # For generic aliases, we need to annotate with TypeAlias
            if type_alias.type_parameters:
                # Generic type alias - annotate with TypeAlias
                self.needs_typing_import = True
                type_alias_assign = cst.AnnAssign(
                    target=cst.Name(name),
                    annotation=cst.Annotation(
                        annotation=cst.Attribute(
                            value=cst.Name("typing"),
                            attr=cst.Name("TypeAlias"),
                        ),
                    ),
                    value=type_alias.value,
                )
            else:
                # Simple type alias - just assignment
                type_alias_assign = cst.Assign(
                    targets=[cst.AssignTarget(target=cst.Name(name))],
                    value=type_alias.value,
                )

            statements.append(
                cst.SimpleStatementLine(
                    body=[type_alias_assign],
                    leading_lines=(
                        updated_node.leading_lines if len(statements) == 0 else ()
                    ),
                    trailing_whitespace=updated_node.trailing_whitespace,
                ),
            )

            # Return as multiple statements if we have TypeVar declarations
            if len(statements) > 1:
                return cst.FlattenSentinel(statements)
            else:
                return statements[0]

        return updated_node

    def leave_Module(
        self,
        original_node: cst.Module,
        updated_node: cst.Module,
    ) -> cst.Module:
        """Add typing import if needed."""
        if not self.needs_typing_import:
            return updated_node

        # Check if typing is already imported
        has_typing_import = False
        for stmt in updated_node.body:
            if isinstance(stmt, cst.SimpleStatementLine):
                for simple_stmt in stmt.body:
                    if isinstance(simple_stmt, cst.Import):
                        for name_item in simple_stmt.names:
                            if isinstance(name_item, cst.ImportAlias):
                                if (
                                    isinstance(name_item.name, cst.Name)
                                    and name_item.name.value == "typing"
                                ):
                                    has_typing_import = True
                                    break
                    elif isinstance(simple_stmt, cst.ImportFrom):
                        if (
                            isinstance(simple_stmt.module, cst.Name)
                            and simple_stmt.module.value == "typing"
                        ):
                            has_typing_import = True
                            break

        if has_typing_import:
            return updated_node

        # Add typing import at the beginning
        typing_import = cst.SimpleStatementLine(
            body=[
                cst.Import(
                    names=[cst.ImportAlias(name=cst.Name("typing"))],
                ),
            ],
        )

        # Find the right place to insert the import (after __future__ imports if any)
        insert_pos = 0
        for i, stmt in enumerate(updated_node.body):
            if isinstance(stmt, cst.SimpleStatementLine):
                for simple_stmt in stmt.body:
                    if isinstance(simple_stmt, cst.ImportFrom):
                        if (
                            isinstance(simple_stmt.module, cst.Name)
                            and simple_stmt.module.value == "__future__"
                        ):
                            insert_pos = i + 1
                            break

        new_body = list(updated_node.body)
        new_body.insert(insert_pos, typing_import)

        return updated_node.with_changes(body=new_body)
