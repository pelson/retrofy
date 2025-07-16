from __future__ import annotations

import libcst as cst


class WalrusOperatorTransformer(cst.CSTTransformer):
    """
    Transform walrus operator (:=) to compatible assignment syntax.

    Transformations:
    1. If statements:
       if (x := func()) > 0: ... -> x = func(); if x > 0: ...
    2. While loops:
       while (x := input()): ... -> while True: x = input(); if not x: break; ...
    3. Expressions:
       result = (x := calc()) -> x = calc(); result = x
    4. Comprehensions:
       [y for x in data if (y := f(x))] -> [y for x, y in ([x, f(x)] for x in data)]

    Limitations:
    - Complex assignment targets not supported
    - Nested comprehensions with walrus have restrictions
    - Short-circuiting behavior may differ in complex boolean expressions

    See PEP-572 for details.
    """

    def __init__(self) -> None:
        self.assignments_stack: list[list[cst.Assign]] = []
        super().__init__()

    def _creates_walrus_scope(self, node: cst.CSTNode) -> bool:
        """Check if this node type creates a scope for walrus assignments."""
        return isinstance(
            node,
            (cst.If, cst.While, cst.Assign, cst.Expr, cst.ListComp, cst.SetComp, cst.DictComp),
        )

    def _extract_assignment_target(self, assignment: cst.Assign) -> cst.Name:
        """Extract the target name from an assignment."""
        # We know from leave_NamedExpr that this is always a single Name target
        return assignment.targets[0].target  # type: ignore

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
        if isinstance(expr, (cst.BinaryOperation, cst.BooleanOperation, cst.Comparison)):
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
        else:
            # For now, raise error for set/dict comprehensions - can be implemented later
            raise ValueError(f"Walrus in {type(node).__name__} not yet supported")

    def _transform_list_comprehension(
        self,
        node: cst.ListComp,
        assignments: list[cst.Assign],
    ) -> cst.ListComp:
        """Transform list comprehension with walrus assignments."""
        # Check for unsupported nested comprehensions
        if node.for_in.inner_for_in is not None:
            raise ValueError("Walrus in nested comprehensions not supported")

        # Extract variable names and values from assignments
        var_names = [self._extract_assignment_target(a) for a in assignments]
        var_values = [a.value for a in assignments]

        # Create new target that includes both original and walrus variables
        original_target = node.for_in.target
        new_target = self._combine_targets(original_target, var_names)

        # Create new iterator that produces tuples of (original, walrus_values...)
        new_iter = cst.GeneratorExp(
            elt=self._create_tuple_expression(original_target, var_values),
            for_in=cst.CompFor(
                target=original_target,
                iter=node.for_in.iter,
                ifs=(),  # Move conditions to outer comprehension
            ),
        )

        # Update the comprehension
        return node.with_changes(
            for_in=cst.CompFor(
                target=new_target,
                iter=new_iter,
                ifs=node.for_in.ifs,
            ),
        )

    def _combine_targets(
        self,
        original: cst.BaseAssignTargetExpression,
        walrus_vars: list[cst.Name],
    ) -> cst.BaseAssignTargetExpression:
        """Combine original target with walrus variable names."""
        elements = [cst.Element(original)]
        for var in walrus_vars:
            elements.append(cst.Element(var))

        # Create tuple without parentheses for comprehension targets
        return cst.Tuple(elements=elements, lpar=[], rpar=[])

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
    ) -> cst.Name:
        """Transform walrus operator to assignment + variable reference."""
        if not isinstance(node.target, cst.Name):
            raise ValueError(f"Complex walrus targets not supported: {type(node.target)}")

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

        # Return just the variable name
        return cst.Name(target.value)

    def leave_If(
        self,
        original_node: cst.If,
        updated_node: cst.If,
    ) -> cst.If | cst.FlattenSentinel:
        """Transform if statement with walrus assignments."""
        assignments = self.assignments_stack.pop()
        if not assignments:
            return updated_node

        # Create assignment statements before the if
        # Combine all assignments into a single line with semicolons
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
