from __future__ import annotations

from typing import List

import libcst as cst


class DataclassTransformer(cst.CSTTransformer):
    """
    Transform @dataclass decorated classes to include __match_args__ attribute.

    This transformation adds __match_args__ to dataclasses for Python < 3.10
    compatibility, unless match_args=False is explicitly specified.
    """

    def leave_Module(
        self,
        original_node: cst.Module,
        updated_node: cst.Module,
    ) -> cst.Module:
        """Transform the entire module to add __match_args__ handling after class definitions."""
        new_body = []

        for stmt in updated_node.body:
            if isinstance(stmt, cst.ClassDef):
                # Process the class
                processed_class, needs_deletion = self._process_dataclass(stmt)
                new_body.append(processed_class)

                # Add deletion statement if needed
                if needs_deletion:
                    delete_stmt = self._create_delete_statement(
                        processed_class.name.value,
                    )
                    # Add the delete statement with proper spacing
                    delete_stmt = delete_stmt.with_changes(
                        leading_lines=[
                            cst.EmptyLine(
                                whitespace=cst.SimpleWhitespace(""),
                                comment=None,
                            ),
                        ],
                    )
                    new_body.append(delete_stmt)
            else:
                new_body.append(stmt)

        return updated_node.with_changes(body=new_body)

    def _process_dataclass(self, class_def: cst.ClassDef) -> tuple[cst.ClassDef, bool]:
        """Process a class definition and return (modified_class, needs_deletion)."""

        # Check if this class has a @dataclass decorator
        if not self._has_dataclass_decorator(class_def):
            return class_def, False

        # Check if match_args=False is specified
        if self._has_match_args_false(class_def):
            # Remove match_args=False parameter and mark for deletion
            modified_class = self._remove_match_args_parameter(class_def)
            # Only need deletion if __match_args__ wasn't explicitly defined
            needs_deletion = not self._has_match_args_attribute(class_def)
            return modified_class, needs_deletion

        # Check if __match_args__ is already defined
        if self._has_match_args_attribute(class_def):
            return class_def, False

        # Add __match_args__ for regular dataclasses
        field_names = self._extract_field_names(class_def)
        if field_names:
            match_args_stmt = self._create_match_args_statement(field_names)
            new_body = list(class_def.body.body) + [match_args_stmt]
            new_body_node = class_def.body.with_changes(body=new_body)
            modified_class = class_def.with_changes(body=new_body_node)
            return modified_class, False

        return class_def, False

    def _create_delete_statement(self, class_name: str) -> cst.Try:
        """Create try/except block to delete __match_args__ from the specified class."""
        # try: del ClassName.__match_args__; except AttributeError: pass

        del_stmt = cst.Del(
            target=cst.Attribute(
                value=cst.Name(class_name),
                attr=cst.Name("__match_args__"),
            ),
        )

        try_body = cst.IndentedBlock([cst.SimpleStatementLine([del_stmt])])

        except_handler = cst.ExceptHandler(
            type=cst.Name("AttributeError"),
            body=cst.IndentedBlock([cst.SimpleStatementLine([cst.Pass()])]),
        )

        return cst.Try(
            body=try_body,
            handlers=[except_handler],
        )

    def _has_dataclass_decorator(self, class_def: cst.ClassDef) -> bool:
        """Check if class has @dataclass decorator."""
        for decorator in class_def.decorators:
            if isinstance(decorator.decorator, cst.Name):
                if decorator.decorator.value == "dataclass":
                    return True
            elif isinstance(decorator.decorator, cst.Call):
                if (
                    isinstance(decorator.decorator.func, cst.Name)
                    and decorator.decorator.func.value == "dataclass"
                ):
                    return True
        return False

    def _has_match_args_false(self, class_def: cst.ClassDef) -> bool:
        """Check if @dataclass has match_args=False."""
        for decorator in class_def.decorators:
            if isinstance(decorator.decorator, cst.Call):
                if (
                    isinstance(decorator.decorator.func, cst.Name)
                    and decorator.decorator.func.value == "dataclass"
                ):
                    # Check for match_args=False in arguments
                    for arg in decorator.decorator.args:
                        if (
                            isinstance(arg, cst.Arg)
                            and isinstance(arg.keyword, cst.Name)
                            and arg.keyword.value == "match_args"
                            and isinstance(arg.value, cst.Name)
                            and arg.value.value == "False"
                        ):
                            return True
        return False

    def _has_match_args_attribute(self, class_def: cst.ClassDef) -> bool:
        """Check if class already has __match_args__ attribute."""
        for stmt in class_def.body.body:
            if isinstance(stmt, cst.SimpleStatementLine):
                for inner_stmt in stmt.body:
                    if isinstance(inner_stmt, cst.Assign):
                        for target in inner_stmt.targets:
                            if (
                                isinstance(target.target, cst.Name)
                                and target.target.value == "__match_args__"
                            ):
                                return True
            elif isinstance(stmt, cst.AnnAssign):
                if (
                    isinstance(stmt.target, cst.Name)
                    and stmt.target.value == "__match_args__"
                ):
                    return True
        return False

    def _extract_field_names(self, class_def: cst.ClassDef) -> List[str]:
        """Extract field names from dataclass."""
        field_names = []

        for stmt in class_def.body.body:
            if isinstance(stmt, cst.AnnAssign):
                # Annotated assignment: x: int or x: int = 5
                if isinstance(stmt.target, cst.Name):
                    field_names.append(stmt.target.value)
            elif isinstance(stmt, cst.SimpleStatementLine):
                for inner_stmt in stmt.body:
                    if isinstance(inner_stmt, cst.Assign):
                        # Regular assignment: x = 5 (less common in dataclasses)
                        for target in inner_stmt.targets:
                            if isinstance(target.target, cst.Name):
                                field_names.append(target.target.value)
                    elif isinstance(inner_stmt, cst.AnnAssign):
                        # Annotated assignment in simple statement line
                        if isinstance(inner_stmt.target, cst.Name):
                            field_names.append(inner_stmt.target.value)

        return field_names

    def _create_match_args_statement(
        self,
        field_names: List[str],
    ) -> cst.SimpleStatementLine:
        """Create __match_args__ = ('field1', 'field2', ...) statement."""

        # Create tuple elements from field names
        elements = []
        for name in field_names:
            elements.append(cst.Element(cst.SimpleString(f"'{name}'")))

        # Create tuple
        if len(elements) == 1:
            # Single element tuple needs trailing comma
            tuple_value = cst.Tuple([elements[0].with_changes(comma=cst.Comma())])
        else:
            tuple_value = cst.Tuple(elements)

        # Create assignment: __match_args__ = (...)
        assignment = cst.Assign(
            targets=[cst.AssignTarget(cst.Name("__match_args__"))],
            value=tuple_value,
        )

        return cst.SimpleStatementLine([assignment])

    def _remove_match_args_parameter(self, class_def: cst.ClassDef) -> cst.ClassDef:
        """Remove match_args=False parameter from @dataclass decorator."""
        new_decorators = []

        for decorator in class_def.decorators:
            if isinstance(decorator.decorator, cst.Call):
                if (
                    isinstance(decorator.decorator.func, cst.Name)
                    and decorator.decorator.func.value == "dataclass"
                ):
                    # Filter out the match_args parameter
                    new_args = []
                    for arg in decorator.decorator.args:
                        if not (
                            isinstance(arg, cst.Arg)
                            and isinstance(arg.keyword, cst.Name)
                            and arg.keyword.value == "match_args"
                        ):
                            new_args.append(arg)

                    # If no args remain, convert back to simple @dataclass
                    if not new_args:
                        new_decorator = cst.Decorator(cst.Name("dataclass"))
                    else:
                        new_decorator = decorator.with_changes(
                            decorator=decorator.decorator.with_changes(args=new_args),
                        )

                    new_decorators.append(new_decorator)
                else:
                    new_decorators.append(decorator)
            else:
                new_decorators.append(decorator)

        return class_def.with_changes(decorators=new_decorators)
