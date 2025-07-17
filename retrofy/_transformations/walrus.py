from __future__ import annotations

import libcst as cst


class WalrusOperatorTransformer(cst.CSTTransformer):
    """
    Transform walrus operator (:=) to compatible assignment syntax.

    Supported Transformations:

    1. If statements:
       if (x := func()) > 0: ... -> x = func(); if x > 0: ...

    2. While loops:
       while (x := input()): ... -> while True: x = input(); if not x: break; ...

    3. Expressions:
       result = (x := calc()) -> x = calc(); result = x

    4. List comprehensions:
       [y for x in data if (y := f(x))] -> [y for x, y in ([x, f(x)] for x in data)]

    5. Set comprehensions:
       {y for x in data if (y := f(x))} -> {y for x, y in ([x, f(x)] for x in data)}

    6. Dict comprehensions:
       {k: v for x in data if (y := f(x))} -> {k: v for x, y in ([x, f(x)] for x in data)}

    7. Nested comprehensions:
       [z for x in data for y in items if (z := f(x, y))] ->
       [z for x, y, z in ([x, y, f(x, y)] for x in data for y in items)]


    8. Short-circuiting boolean expressions:
       while (x := func()) and (y := g(x)) > 0: ... ->
       while True:
           x = func()
           if not x: break
           y = g(x)
           if not (y > 0): break
           ...

    Remaining Limitations:
    - Complex assignment targets (libcst doesn't support obj.attr := value syntax)
    - Complex short-circuiting: Only simple two-assignment AND operations in
      comprehensions are fully supported for short-circuiting. More complex
      boolean expressions may fall back to non-short-circuiting behavior
    - Walrus in lambda expressions may have edge cases

    See PEP-572 for details.
    """

    def __init__(self) -> None:
        self.assignments_stack: list[list[cst.Assign]] = []
        super().__init__()

    def _creates_walrus_scope(self, node: cst.CSTNode) -> bool:
        """Check if this node type creates a scope for walrus assignments."""
        return isinstance(
            node,
            (
                cst.If,
                cst.While,
                cst.Assign,
                cst.Expr,
                cst.ListComp,
                cst.SetComp,
                cst.DictComp,
            ),
        )

    def _extract_assignment_target(
        self,
        assignment: cst.Assign,
    ) -> cst.BaseAssignTargetExpression:
        """Extract the target from an assignment."""
        return assignment.targets[0].target

    def _transform_while_with_walrus(
        self,
        node: cst.While,
        assignments: list[cst.Assign],
    ) -> cst.While:
        """Transform while loop containing walrus assignments."""
        # Create assignment statements (combine into single line with semicolons)
        assignment_line = cst.SimpleStatementLine(body=assignments)

        # Create break condition: if not (original_test): break
        break_condition = cst.If(
            test=cst.UnaryOperation(
                operator=cst.Not(),
                expression=self._ensure_parentheses(node.test),
            ),
            body=cst.SimpleStatementSuite([cst.Break()]),
        )

        # Reconstruct while loop body
        new_body = [assignment_line, break_condition] + list(node.body.body)

        return node.with_changes(
            test=cst.parse_expression("True"),
            body=node.body.with_changes(body=new_body),
        )

    def _ensure_parentheses(self, expr: cst.BaseExpression) -> cst.BaseExpression:
        """Ensure expression is properly parenthesized when needed."""
        # Add parentheses for any complex expression in break conditions
        if isinstance(
            expr,
            (cst.BinaryOperation, cst.BooleanOperation, cst.Comparison),
        ):
            return expr.with_changes(
                lpar=[cst.LeftParen()],
                rpar=[cst.RightParen()],
            )
        return expr

    def _transform_comprehension_with_walrus(
        self,
        node: cst.ListComp | cst.SetComp | cst.DictComp,
        assignments: list[cst.Assign],
    ) -> cst.CSTNode:
        """Transform comprehension containing walrus assignments."""
        if isinstance(node, cst.ListComp):
            return self._transform_list_comprehension(node, assignments)
        elif isinstance(node, cst.SetComp):
            return self._transform_set_comprehension(node, assignments)
        elif isinstance(node, cst.DictComp):
            return self._transform_dict_comprehension(node, assignments)
        else:
            raise ValueError(f"Unsupported comprehension type: {type(node).__name__}")

    def _transform_list_comprehension(
        self,
        node: cst.ListComp,
        assignments: list[cst.Assign],
    ) -> cst.ListComp:
        """Transform list comprehension with walrus assignments."""
        return self._generic_comprehension_transform(
            node,
            assignments,
            is_short_circuit=True,
        )

    def _transform_nested_list_comprehension(
        self,
        node: cst.ListComp,
        assignments: list[cst.Assign],
    ) -> cst.ListComp:
        """Transform nested list comprehension with walrus assignments."""
        # Extract variable targets and values from assignments
        var_targets = [self._extract_assignment_target(a) for a in assignments]
        var_values = [a.value for a in assignments]

        # Collect all for-in clauses including nested ones
        for_clauses = []
        current_for = node.for_in
        while current_for is not None:
            for_clauses.append(current_for)
            current_for = current_for.inner_for_in

        # Create new target that includes all original targets plus walrus variables
        all_targets = []
        for for_clause in for_clauses:
            all_targets.append(for_clause.target)
        all_targets.extend(var_targets)

        new_target = self._combine_multiple_targets(all_targets)

        # Create new iterator that produces tuples of all values
        all_values = []
        for for_clause in for_clauses:
            all_values.append(for_clause.target)
        all_values.extend(var_values)

        # Build nested generator expression
        nested_gen = self._build_nested_generator(for_clauses, all_values)

        # Update the comprehension with flattened structure
        return node.with_changes(
            for_in=cst.CompFor(
                target=new_target,
                iter=nested_gen,
                ifs=(),  # Conditions are preserved in the nested generator
            ),
        )

    def _transform_set_comprehension(
        self,
        node: cst.SetComp,
        assignments: list[cst.Assign],
    ) -> cst.SetComp:
        """Transform set comprehension with walrus assignments."""
        return self._generic_comprehension_transform(
            node,
            assignments,
            is_short_circuit=True,
        )

    def _transform_dict_comprehension(
        self,
        node: cst.DictComp,
        assignments: list[cst.Assign],
    ) -> cst.DictComp:
        """Transform dict comprehension with walrus assignments."""
        return self._generic_comprehension_transform(
            node,
            assignments,
            is_short_circuit=True,
        )

    def _combine_targets(
        self,
        original: cst.BaseAssignTargetExpression,
        walrus_targets: list[cst.BaseAssignTargetExpression],
    ) -> cst.BaseAssignTargetExpression:
        """Combine original target with walrus variable targets."""
        elements = [cst.Element(original)]
        for target in walrus_targets:
            elements.append(cst.Element(target))

        # Create tuple without parentheses for comprehension targets
        return cst.Tuple(elements=elements, lpar=[], rpar=[])

    def _combine_multiple_targets(
        self,
        targets: list[cst.BaseAssignTargetExpression],
    ) -> cst.BaseAssignTargetExpression:
        """Combine multiple targets into a single tuple target."""
        elements = [cst.Element(target) for target in targets]
        return cst.Tuple(elements=elements, lpar=[], rpar=[])

    def _build_nested_generator(
        self,
        for_clauses: list[cst.CompFor],
        all_values: list[cst.BaseExpression],
    ) -> cst.GeneratorExp:
        """Build a nested generator expression that produces tuples of all values."""
        # Create the tuple of all values
        tuple_expr = cst.Tuple(
            elements=[cst.Element(value) for value in all_values],
            lpar=[cst.LeftSquareBracket()],
            rpar=[cst.RightSquareBracket()],
        )

        # Build the generator preserving the original nested structure
        # Start from the innermost and build outward
        current_gen = None
        for i in range(len(for_clauses) - 1, -1, -1):
            current_gen = cst.CompFor(
                target=for_clauses[i].target,
                iter=for_clauses[i].iter,
                ifs=for_clauses[i].ifs,
                inner_for_in=current_gen,
            )

        return cst.GeneratorExp(elt=tuple_expr, for_in=current_gen)

    def _transform_nested_set_comprehension(
        self,
        node: cst.SetComp,
        assignments: list[cst.Assign],
    ) -> cst.SetComp:
        """Transform nested set comprehension with walrus assignments."""
        # Extract variable targets and values from assignments
        var_targets = [self._extract_assignment_target(a) for a in assignments]
        var_values = [a.value for a in assignments]

        # Collect all for-in clauses including nested ones
        for_clauses = []
        current_for = node.for_in
        while current_for is not None:
            for_clauses.append(current_for)
            current_for = current_for.inner_for_in

        # Create new target that includes all original targets plus walrus variables
        all_targets = []
        for for_clause in for_clauses:
            all_targets.append(for_clause.target)
        all_targets.extend(var_targets)

        new_target = self._combine_multiple_targets(all_targets)

        # Create new iterator that produces tuples of all values
        all_values = []
        for for_clause in for_clauses:
            all_values.append(for_clause.target)
        all_values.extend(var_values)

        # Build nested generator expression
        nested_gen = self._build_nested_generator(for_clauses, all_values)

        # Update the comprehension with flattened structure
        return node.with_changes(
            for_in=cst.CompFor(
                target=new_target,
                iter=nested_gen,
                ifs=(),  # Conditions are preserved in the nested generator
            ),
        )

    def _transform_nested_dict_comprehension(
        self,
        node: cst.DictComp,
        assignments: list[cst.Assign],
    ) -> cst.DictComp:
        """Transform nested dict comprehension with walrus assignments."""
        # Extract variable targets and values from assignments
        var_targets = [self._extract_assignment_target(a) for a in assignments]
        var_values = [a.value for a in assignments]

        # Collect all for-in clauses including nested ones
        for_clauses = []
        current_for = node.for_in
        while current_for is not None:
            for_clauses.append(current_for)
            current_for = current_for.inner_for_in

        # Create new target that includes all original targets plus walrus variables
        all_targets = []
        for for_clause in for_clauses:
            all_targets.append(for_clause.target)
        all_targets.extend(var_targets)

        new_target = self._combine_multiple_targets(all_targets)

        # Create new iterator that produces tuples of all values
        all_values = []
        for for_clause in for_clauses:
            all_values.append(for_clause.target)
        all_values.extend(var_values)

        # Build nested generator expression
        nested_gen = self._build_nested_generator(for_clauses, all_values)

        # Update the comprehension with flattened structure
        return node.with_changes(
            for_in=cst.CompFor(
                target=new_target,
                iter=nested_gen,
                ifs=(),  # Conditions are preserved in the nested generator
            ),
        )

    def _create_tuple_expression(
        self,
        original: cst.BaseAssignTargetExpression,
        values: list[cst.BaseExpression],
    ) -> cst.BaseExpression:
        """Create tuple expression combining original and walrus values."""
        elements = [cst.Element(original)]
        for value in values:
            elements.append(cst.Element(value))

        return cst.Tuple(
            elements=elements,
            lpar=[cst.LeftSquareBracket()],
            rpar=[cst.RightSquareBracket()],
        )

    def _needs_short_circuiting(
        self,
        test_expr: cst.BaseExpression,
        assignments: list[cst.Assign],
    ) -> bool:
        """Check if the test expression contains boolean operators that need short-circuiting."""
        if len(assignments) <= 1:
            return False

        # Only enable short-circuiting for AND operations for now
        # OR operations are more complex and the existing behavior is often acceptable
        return self._contains_and_operations(test_expr)

    def _contains_and_operations(self, node: cst.CSTNode) -> bool:
        """Recursively check if a node contains AND operations."""
        if isinstance(node, cst.BooleanOperation) and isinstance(
            node.operator,
            cst.And,
        ):
            return True

        # Check children
        for child in node.children:
            if hasattr(child, "children") and self._contains_and_operations(child):
                return True

        return False

    def _transform_short_circuit_if(
        self,
        original_node: cst.If,
        updated_node: cst.If,
        assignments: list[cst.Assign],
    ) -> cst.FlattenSentinel:
        """Transform if statement with proper short-circuiting."""
        # For now, implement simple short-circuiting for AND operations
        # More complex logic can be added later

        # Extract the boolean structure and create nested if statements
        nested_ifs = self._build_short_circuit_structure(
            updated_node.test,
            assignments,
            updated_node.body,
            updated_node.orelse,
        )

        return cst.FlattenSentinel(nested_ifs)

    def _build_short_circuit_structure(
        self,
        test_expr: cst.BaseExpression,
        assignments: list[cst.Assign],
        body: cst.BaseSuite,
        orelse: cst.Else | None,
    ) -> list[cst.SimpleStatementLine | cst.If]:
        """Build the nested if structure for short-circuiting."""
        # This is a simplified implementation
        # For a full implementation, we'd need to parse the boolean expression tree

        if len(assignments) == 2:
            # Simple case: two assignments with AND
            first_assign, second_assign = assignments

            # Create first assignment
            first_line = cst.SimpleStatementLine(body=[first_assign])

            # Extract the first condition (before AND)
            # This is simplified - a full implementation would parse the tree
            first_condition = self._extract_assignment_target(first_assign)

            # Create the inner if with second assignment + original body
            second_line = cst.SimpleStatementLine(body=[second_assign])
            inner_if = cst.If(
                test=self._build_second_condition(test_expr, first_condition),
                body=body,
                orelse=orelse,
            )

            # Create outer if
            outer_if = cst.If(
                test=self._build_first_condition(test_expr, first_condition),
                body=cst.IndentedBlock(body=[second_line, inner_if]),
                orelse=orelse,
            )

            return [first_line, outer_if]

        # Fallback: simple assignment before if
        assignment_line = cst.SimpleStatementLine(body=assignments)
        simple_if = cst.If(test=test_expr, body=body, orelse=orelse)
        return [assignment_line, simple_if]

    def _build_first_condition(
        self,
        test_expr: cst.BaseExpression,
        first_var: cst.BaseAssignTargetExpression,
    ) -> cst.BaseExpression:
        """Extract the first condition from a boolean AND expression."""
        # Simplified: assume the first condition involves the first variable
        # A full implementation would parse the expression tree
        if isinstance(test_expr, cst.BooleanOperation) and isinstance(
            test_expr.operator,
            cst.And,
        ):
            return test_expr.left
        return first_var

    def _build_second_condition(
        self,
        test_expr: cst.BaseExpression,
        first_var: cst.BaseAssignTargetExpression,
    ) -> cst.BaseExpression:
        """Extract the second condition from a boolean AND expression."""
        # Simplified: assume the second condition is the right side of AND
        if isinstance(test_expr, cst.BooleanOperation) and isinstance(
            test_expr.operator,
            cst.And,
        ):
            return test_expr.right
        return test_expr

    def _comprehension_needs_short_circuiting(
        self,
        condition: cst.CompIf,
    ) -> bool:
        """Check if a comprehension condition needs short-circuiting."""
        return isinstance(condition.test, cst.BooleanOperation) and isinstance(
            condition.test.operator,
            cst.And,
        )

    def _extract_first_condition_from_and(
        self,
        condition: cst.BaseExpression,
    ) -> cst.BaseExpression | None:
        """Extract the first condition from an AND expression."""
        if isinstance(condition, cst.BooleanOperation) and isinstance(
            condition.operator,
            cst.And,
        ):
            return condition.left
        return None

    def _extract_second_condition_from_and(
        self,
        condition: cst.BaseExpression,
    ) -> cst.BaseExpression | None:
        """Extract the second condition from an AND expression."""
        if isinstance(condition, cst.BooleanOperation) and isinstance(
            condition.operator,
            cst.And,
        ):
            return condition.right
        return None

    def _generic_comprehension_transform(
        self,
        node: cst.ListComp | cst.SetComp | cst.DictComp,
        assignments: list[cst.Assign],
        is_short_circuit: bool = False,
    ) -> cst.ListComp | cst.SetComp | cst.DictComp:
        """Generic comprehension transformation that works for all comprehension types."""
        # Handle nested comprehensions
        if node.for_in.inner_for_in is not None:
            return self._handle_nested_comprehension(node, assignments)

        # Check if we need short-circuiting for comprehensions
        if (
            is_short_circuit
            and len(assignments) == 2
            and node.for_in.ifs
            and self._comprehension_needs_short_circuiting(node.for_in.ifs[0])
        ):
            return self._generic_two_assignment_short_circuit(node, assignments)

        # Standard transformation
        return self._generic_standard_comprehension_transform(node, assignments)

    def _generic_two_assignment_short_circuit(
        self,
        node: cst.ListComp | cst.SetComp | cst.DictComp,
        assignments: list[cst.Assign],
    ) -> cst.ListComp | cst.SetComp | cst.DictComp:
        """Generic two-assignment short-circuiting for any comprehension type."""
        first_assign, second_assign = assignments

        # Extract targets and values
        first_target = self._extract_assignment_target(first_assign)
        first_value = first_assign.value
        second_target = self._extract_assignment_target(second_assign)
        second_value = second_assign.value

        original_target = node.for_in.target

        # Create inner generator: ((x, f(x)) for x in data)
        inner_gen = cst.GeneratorExp(
            elt=cst.Tuple(
                elements=[cst.Element(original_target), cst.Element(first_value)],
                lpar=[cst.LeftParen()],
                rpar=[cst.RightParen()],
            ),
            for_in=cst.CompFor(
                target=original_target,
                iter=node.for_in.iter,
                ifs=(),
            ),
        )

        # Create middle generator: ((x, y, g(x, y)) for x, y in inner_gen if y)
        middle_target = cst.Tuple(
            elements=[cst.Element(original_target), cst.Element(first_target)],
            lpar=[],
            rpar=[],
        )

        # Extract the condition for the first variable from the original if clause
        first_condition = self._extract_first_condition_from_and(
            node.for_in.ifs[0].test,
        )

        middle_gen = cst.GeneratorExp(
            elt=cst.Tuple(
                elements=[
                    cst.Element(original_target),
                    cst.Element(first_target),
                    cst.Element(second_value),
                ],
                lpar=[cst.LeftParen()],
                rpar=[cst.RightParen()],
            ),
            for_in=cst.CompFor(
                target=middle_target,
                iter=inner_gen,
                ifs=[cst.CompIf(test=first_condition)] if first_condition else [],
            ),
        )

        # Create outer target that includes all variables
        outer_target = cst.Tuple(
            elements=[
                cst.Element(original_target),
                cst.Element(first_target),
                cst.Element(second_target),
            ],
            lpar=[],
            rpar=[],
        )

        # Extract the condition for the second variable
        second_condition = self._extract_second_condition_from_and(
            node.for_in.ifs[0].test,
        )

        # Update the comprehension
        return node.with_changes(
            for_in=cst.CompFor(
                target=outer_target,
                iter=middle_gen,
                ifs=[cst.CompIf(test=second_condition)] if second_condition else [],
            ),
        )

    def _generic_standard_comprehension_transform(
        self,
        node: cst.ListComp | cst.SetComp | cst.DictComp,
        assignments: list[cst.Assign],
    ) -> cst.ListComp | cst.SetComp | cst.DictComp:
        """Generic standard comprehension transformation."""
        var_targets = [self._extract_assignment_target(a) for a in assignments]
        var_values = [a.value for a in assignments]
        original_target = node.for_in.target
        new_target = self._combine_targets(original_target, var_targets)

        new_iter = cst.GeneratorExp(
            elt=self._create_tuple_expression(original_target, var_values),
            for_in=cst.CompFor(
                target=original_target,
                iter=node.for_in.iter,
                ifs=(),
            ),
        )

        return node.with_changes(
            for_in=cst.CompFor(
                target=new_target,
                iter=new_iter,
                ifs=node.for_in.ifs,
            ),
        )

    def _handle_nested_comprehension(
        self,
        node: cst.ListComp | cst.SetComp | cst.DictComp,
        assignments: list[cst.Assign],
    ) -> cst.ListComp | cst.SetComp | cst.DictComp:
        """Handle nested comprehensions by delegating to specific methods."""
        if isinstance(node, cst.ListComp):
            return self._transform_nested_list_comprehension(node, assignments)
        elif isinstance(node, cst.SetComp):
            return self._transform_nested_set_comprehension(node, assignments)
        elif isinstance(node, cst.DictComp):
            return self._transform_nested_dict_comprehension(node, assignments)
        else:
            raise ValueError(f"Unsupported comprehension type: {type(node).__name__}")

    # Core visitor methods - push scope for each statement type
    def visit_If(self, node: cst.If) -> None:
        """Push scope for if statements."""
        self.assignments_stack.append([])

    def visit_While(self, node: cst.While) -> None:
        """Push scope for while loops."""
        self.assignments_stack.append([])

    def visit_Assign(self, node: cst.Assign) -> None:
        """Push scope for assignments."""
        self.assignments_stack.append([])

    def visit_Expr(self, node: cst.Expr) -> None:
        """Push scope for expressions."""
        self.assignments_stack.append([])

    def visit_ListComp(self, node: cst.ListComp) -> None:
        """Push scope for list comprehensions."""
        self.assignments_stack.append([])

    def visit_SetComp(self, node: cst.SetComp) -> None:
        """Push scope for set comprehensions."""
        self.assignments_stack.append([])

    def visit_DictComp(self, node: cst.DictComp) -> None:
        """Push scope for dict comprehensions."""
        self.assignments_stack.append([])

    def leave_NamedExpr(
        self,
        node: cst.NamedExpr,
        updated_node: cst.NamedExpr,
    ) -> cst.BaseExpression:
        """Transform walrus operator to assignment + variable reference."""
        if not self.assignments_stack:
            raise RuntimeError("Walrus operator found outside valid context")

        target = node.target
        value = node.value

        # Create assignment statement
        assign_stmt = cst.Assign(
            targets=[cst.AssignTarget(target=target)],
            value=value,
        )

        # Add to current scope
        self.assignments_stack[-1].append(assign_stmt)

        # Return the target expression (for referencing the assigned value)
        return target

    def leave_If(
        self,
        original_node: cst.If,
        updated_node: cst.If,
    ) -> cst.If | cst.FlattenSentinel:
        """Transform if statement with walrus assignments."""
        assignments = self.assignments_stack.pop()
        if not assignments:
            return updated_node

        # Check if we need short-circuiting behavior
        if self._needs_short_circuiting(updated_node.test, assignments):
            return self._transform_short_circuit_if(
                original_node,
                updated_node,
                assignments,
            )

        # Simple case: just put assignments before the if
        assignment_line = cst.SimpleStatementLine(body=assignments)
        return cst.FlattenSentinel([assignment_line, updated_node])

    def leave_While(
        self,
        original_node: cst.While,
        updated_node: cst.While,
    ) -> cst.While:
        """Transform while loop with walrus assignments."""
        assignments = self.assignments_stack.pop()
        if not assignments:
            return updated_node

        return self._transform_while_with_walrus(updated_node, assignments)

    def leave_Assign(
        self,
        original_node: cst.Assign,
        updated_node: cst.Assign,
    ) -> cst.Assign | cst.FlattenSentinel:
        """Transform assignment with walrus expressions."""
        assignments = self.assignments_stack.pop()
        if not assignments:
            return updated_node

        # Put walrus assignments before the main assignment
        return cst.FlattenSentinel(assignments + [updated_node])

    def leave_Expr(
        self,
        original_node: cst.Expr,
        updated_node: cst.Expr,
    ) -> cst.Expr | cst.FlattenSentinel:
        """Transform expression statement with walrus."""
        assignments = self.assignments_stack.pop()
        if not assignments:
            return updated_node

        # Put walrus assignments before the expression
        return cst.FlattenSentinel(assignments + [updated_node])

    def leave_ListComp(
        self,
        original_node: cst.ListComp,
        updated_node: cst.ListComp,
    ) -> cst.ListComp:
        """Transform list comprehension with walrus assignments."""
        assignments = self.assignments_stack.pop()
        if not assignments:
            return updated_node

        return self._transform_comprehension_with_walrus(updated_node, assignments)

    # Add support for other comprehension types
    def leave_SetComp(
        self,
        original_node: cst.SetComp,
        updated_node: cst.SetComp,
    ) -> cst.SetComp:
        """Transform set comprehension with walrus assignments."""
        assignments = self.assignments_stack.pop()
        if not assignments:
            return updated_node

        return self._transform_comprehension_with_walrus(updated_node, assignments)

    def leave_DictComp(
        self,
        original_node: cst.DictComp,
        updated_node: cst.DictComp,
    ) -> cst.DictComp:
        """Transform dict comprehension with walrus assignments."""
        assignments = self.assignments_stack.pop()
        if not assignments:
            return updated_node

        return self._transform_comprehension_with_walrus(updated_node, assignments)
