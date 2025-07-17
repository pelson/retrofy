from __future__ import annotations

import libcst as cst


class PEP695Transformer(cst.CSTTransformer):
    """
    A transformer that replaces PEP 695 syntax with pre-3.12 equivalent syntax.

    Transforms:
    1. Type statements:
        type Point = tuple[float, float]
        -> Point = tuple[float, float]

        type GenericPoint[T] = tuple[T, T]
        -> T = typing.TypeVar("T"); GenericPoint: typing.TypeAlias = tuple[T, T]

    2. Generic classes:
        class ClassA[T: str]:
            def method1(self) -> T: ...
        -> class ClassA(typing.Generic[T]):
               def method1(self) -> T: ...
           (with T = typing.TypeVar("T", bound=str) added before the class)

    3. Generic functions:
        def func[T](a: T) -> T:
            return a
        -> def func(a: T) -> T:
               return a
           (with T = typing.TypeVar("T") added before the function)

    This follows the patterns described in PEP 695 for backward compatibility.
    """

    def __init__(self) -> None:
        self.needs_typing_import = False
        self.needs_generic_import = False
        self.type_vars_to_create: list[str] = []
        super().__init__()

    def _create_type_var(self, param: cst.TypeParam) -> cst.SimpleStatementLine:
        """Create a TypeVar declaration from a TypeParam."""
        if not isinstance(param.param, cst.TypeVar):
            raise ValueError(f"Expected TypeVar, got {type(param.param)}")

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

        # Always use typing.TypeVar
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

        return cst.SimpleStatementLine(
            body=[type_var_assign],
            trailing_whitespace=cst.TrailingWhitespace(
                whitespace=cst.SimpleWhitespace(""),
                comment=None,
                newline=cst.Newline(),
            ),
        )

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

                        # Always use typing.TypeVar
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

                # Always use typing.TypeAlias
                type_alias_annotation = cst.Attribute(
                    value=cst.Name("typing"),
                    attr=cst.Name("TypeAlias"),
                )

                type_alias_assign = cst.AnnAssign(
                    target=cst.Name(name),
                    annotation=cst.Annotation(annotation=type_alias_annotation),
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

    def leave_ClassDef(
        self,
        original_node: cst.ClassDef,
        updated_node: cst.ClassDef,
    ) -> cst.ClassDef | cst.FlattenSentinel:
        """Transform generic class definitions."""
        if not updated_node.type_parameters:
            return updated_node

        self.needs_typing_import = True
        self.needs_generic_import = True

        # Create TypeVar declarations for each type parameter
        type_var_statements = []
        type_var_names = []

        for param in updated_node.type_parameters.params:
            if isinstance(param, cst.TypeParam) and isinstance(
                param.param,
                cst.TypeVar,
            ):
                param_name = param.param.name.value
                type_var_names.append(param_name)

                type_var_stmt = self._create_type_var(param)
                type_var_statements.append(type_var_stmt)

        # Create Generic[T, U, ...] base class
        if type_var_names:
            # Create subscript arguments for Generic
            generic_args = []
            for name in type_var_names:
                generic_args.append(
                    cst.SubscriptElement(
                        slice=cst.Index(value=cst.Name(name)),
                    ),
                )

            # Create Generic[T, U, ...] arg - always use typing.Generic
            generic_base = cst.Attribute(
                value=cst.Name("typing"),
                attr=cst.Name("Generic"),
            )

            generic_arg = cst.Arg(
                value=cst.Subscript(
                    value=generic_base,
                    slice=generic_args,
                ),
            )

            # Add Generic to the class bases
            new_bases = list(updated_node.bases) if updated_node.bases else []
            new_bases.append(generic_arg)

            # Create new class without type parameters
            new_class = updated_node.with_changes(
                type_parameters=None,
                bases=new_bases,
            )

            # Return flattened statements: TypeVar declarations + class
            if type_var_statements:
                # Add the class with proper leading lines
                class_stmt = new_class.with_changes(
                    leading_lines=original_node.leading_lines,
                )
                return cst.FlattenSentinel(type_var_statements + [class_stmt])
            else:
                return new_class

        return updated_node

    def leave_FunctionDef(
        self,
        original_node: cst.FunctionDef,
        updated_node: cst.FunctionDef,
    ) -> cst.FunctionDef | cst.FlattenSentinel:
        """Transform generic function definitions."""
        if not updated_node.type_parameters:
            return updated_node

        self.needs_typing_import = True

        # Create TypeVar declarations for each type parameter
        type_var_statements = []

        for param in updated_node.type_parameters.params:
            if isinstance(param, cst.TypeParam) and isinstance(
                param.param,
                cst.TypeVar,
            ):
                type_var_stmt = self._create_type_var(param)
                type_var_statements.append(type_var_stmt)

        # Create new function without type parameters
        new_function = updated_node.with_changes(
            type_parameters=None,
        )

        # Return flattened statements: TypeVar declarations + function
        if type_var_statements:
            # Add the function with proper leading lines
            func_stmt = new_function.with_changes(
                leading_lines=original_node.leading_lines,
            )
            return cst.FlattenSentinel(type_var_statements + [func_stmt])
        else:
            return new_function

    def leave_Module(
        self,
        original_node: cst.Module,
        updated_node: cst.Module,
    ) -> cst.Module:
        """Add typing import if needed."""
        if not self.needs_typing_import:
            return updated_node

        # Check if "import typing" is already present (we need this specific import)
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

        if has_typing_import:
            return updated_node

        # Always add "import typing" since we use typing.TypeVar and typing.TypeAlias
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
