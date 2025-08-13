from __future__ import annotations

from typing import List

import libcst as cst


class DataclassTransformer(cst.CSTTransformer):
    """
    Transform @dataclass decorated classes to include __match_args__ attribute.

    This transformation adds __match_args__ to dataclasses for Python < 3.10
    compatibility, unless match_args=False is explicitly specified.
    """

    def leave_ClassDef(
        self,
        original_node: cst.ClassDef,
        updated_node: cst.ClassDef,
    ) -> cst.ClassDef:
        """Transform dataclass to add __match_args__ if needed."""

        # Check if this class has a @dataclass decorator
        if not self._has_dataclass_decorator(updated_node):
            return updated_node

        # Check if match_args=False is specified
        if self._has_match_args_false(updated_node):
            return updated_node

        # Check if __match_args__ is already defined
        if self._has_match_args_attribute(updated_node):
            return updated_node

        # Extract field names from the class
        field_names = self._extract_field_names(updated_node)

        if not field_names:
            return updated_node

        # Create __match_args__ attribute
        match_args_stmt = self._create_match_args_statement(field_names)

        # Add the __match_args__ attribute to the class body
        new_body = list(updated_node.body.body) + [match_args_stmt]
        new_body_node = updated_node.body.with_changes(body=new_body)

        return updated_node.with_changes(body=new_body_node)

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
