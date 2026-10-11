from __future__ import annotations

import libcst as cst
import libcst.matchers as m

_SCOPED_STATEMENTS = (cst.BaseSmallStatement, cst.BaseCompoundStatement)


def _contains_walrus(node: cst.CSTNode) -> bool:
    return bool(m.findall(node, m.NamedExpr()))


def _parenthesize(expr: cst.BaseExpression) -> cst.BaseExpression:
    return expr.with_changes(lpar=[cst.LeftParen()], rpar=[cst.RightParen()])


def _standalone(expr: cst.BaseExpression) -> cst.BaseExpression:
    """Make *expr* valid on its own, outside the brackets it came from.

    A sub-expression that spans several lines relied on enclosing
    brackets for its line breaks. Once lifted into its own statement or
    condition, it needs parentheses of its own.
    """
    if not expr.lpar and "\n" in cst.Module(body=[]).code_for_node(expr):
        return _parenthesize(expr)
    return expr


def _negate(expr: cst.BaseExpression) -> cst.UnaryOperation:
    if (
        isinstance(
            expr,
            (cst.BinaryOperation, cst.BooleanOperation, cst.Comparison, cst.IfExp),
        )
        and not expr.lpar
    ):
        expr = _parenthesize(expr)
    return cst.UnaryOperation(operator=cst.Not(), expression=_standalone(expr))


def _assign(
    target: cst.BaseAssignTargetExpression,
    value: cst.BaseExpression,
) -> cst.SimpleStatementLine:
    return cst.SimpleStatementLine(
        body=[
            cst.Assign(
                targets=[cst.AssignTarget(target=target)],
                value=_standalone(value),
            ),
        ],
    )


def _and_operands(expr: cst.BaseExpression) -> list[cst.BaseExpression]:
    """Flatten a chain of ``and`` operations into its operands."""
    if isinstance(expr, cst.BooleanOperation) and isinstance(expr.operator, cst.And):
        return [*_and_operands(expr.left), *_and_operands(expr.right)]
    return [expr]


def _join_and(operands: list[cst.BaseExpression]) -> cst.BaseExpression:
    result = operands[0]
    for operand in operands[1:]:
        result = cst.BooleanOperation(left=result, operator=cst.And(), right=operand)
    return result


def _group_operands(
    operands: list[cst.BaseExpression],
) -> list[list[cst.BaseExpression]]:
    """Split ``and`` operands into groups that each start with a walrus.

    Only the first operand of a group (and of the leading group, possibly
    none) contains a walrus, so the rest of a group can stay in the same
    condition as that operand.
    """
    groups: list[list[cst.BaseExpression]] = [[]]
    for operand in operands:
        if groups[-1] and _contains_walrus(operand):
            groups.append([])
        groups[-1].append(operand)
    return groups


def _block_statements(block: cst.BaseSuite) -> list[cst.BaseStatement]:
    if isinstance(block, cst.SimpleStatementSuite):
        return [cst.SimpleStatementLine(body=block.body)]
    return list(block.body)


def _with_leading_lines(
    node: cst.BaseCompoundStatement,
    statements: list[cst.BaseStatement],
) -> list[cst.BaseStatement]:
    """Keep the comments and blank lines above *node* above its replacement."""
    first = statements[0].with_changes(leading_lines=node.leading_lines)
    return [first, *statements[1:]]


class WalrusOperatorTransformer(cst.CSTTransformer):
    """
    Transform walrus operator (:=) to compatible assignment syntax.

    Supported Transformations:

    1. If statements:
       if (x := func()) > 0: ... -> x = func(); if x > 0: ...

    2. While loops:
       while (x := input()): ... -> while True: x = input(); if not x: break; ...

    3. Statements:
       result = (x := calc()) -> x = calc(); result = x
       return (x := calc())   -> x = calc(); return x

    4. List comprehensions:
       [y for x in data if (y := f(x))] -> [y for x, y in ([x, f(x)] for x in data)]

    5. Set comprehensions:
       {y for x in data if (y := f(x))} -> {y for x, y in ([x, f(x)] for x in data)}

    6. Dict comprehensions:
       {k: v for x in data if (y := f(x))} -> {k: v for x, y in ([x, f(x)] for x in data)}

    7. Nested comprehensions:
       [z for x in data for y in items if (z := f(x, y))] ->
       [z for x, y, z in ([x, y, f(x, y)] for x in data for y in items)]


    8. Short-circuiting boolean expressions in if / while conditions:
       while (x := func()) and (y := g(x)) > 0: ... ->
       while True:
           x = func()
           if not x: break
           y = g(x)
           if not (y > 0): break
           ...

       ``or``, ``not`` and conditional expressions, an ``else`` / ``elif``
       clause, or ``while ... else`` are lowered via a temporary
       ``_retrofy_cond_<n>`` variable instead:

       if (x := f()) or (y := g()): ... ->
       x = f()
       _retrofy_cond_0 = x
       if not _retrofy_cond_0:
           y = g()
           _retrofy_cond_0 = y
       if _retrofy_cond_0: ...

    Remaining Limitations:
    - Complex assignment targets (libcst doesn't support obj.attr := value syntax)
    - Complex short-circuiting: Only simple two-assignment AND operations in
      comprehensions are fully supported for short-circuiting. More complex
      boolean expressions may fall back to non-short-circuiting behavior
    - Short-circuiting is only preserved through ``and``/``or``/``not`` and
      conditional expressions. Assignments inside any other expression
      (e.g. ``f((x := a()) and (y := b()))``) are hoisted in front of the
      statement, ahead of the rest of the expression.
    - Walrus in lambda expressions may have edge cases

    See PEP-572 for details.
    """

    def __init__(self) -> None:
        self.assignments_stack: list[list[cst.Assign]] = []
        self._temp_count = 0
        super().__init__()

    def _temp_name(self) -> cst.Name:
        name = cst.Name(f"_retrofy_cond_{self._temp_count}")
        self._temp_count += 1
        return name

    def _hoist(
        self,
        expr: cst.BaseExpression,
    ) -> tuple[list[cst.BaseStatement], cst.BaseExpression]:
        """Lower *expr* with the walrus assignments hoisted in front of it."""
        self.assignments_stack.append([])
        lowered = expr.visit(self)
        assignments = self.assignments_stack.pop()
        assert isinstance(lowered, cst.BaseExpression)
        if not assignments:
            # e.g. the walrus was inside a comprehension, which lowers it
            # in place.
            return [], lowered
        return [cst.SimpleStatementLine(body=assignments)], lowered

    def _lower_expr(
        self,
        expr: cst.BaseExpression,
    ) -> tuple[list[cst.BaseStatement], cst.BaseExpression]:
        """Lower the walruses in *expr*, preserving short-circuiting.

        Returns the statements to run first, and an equivalent walrus-free
        expression to evaluate after them.
        """
        if not _contains_walrus(expr):
            return [], expr

        if isinstance(expr, cst.NamedExpr):
            statements, value = self._lower_expr(expr.value)
            return [*statements, _assign(expr.target, value)], expr.target

        if isinstance(expr, cst.BooleanOperation):
            left_statements, left = self._lower_expr(expr.left)
            if not _contains_walrus(expr.right):
                return left_statements, expr.with_changes(left=left)
            # Only evaluate the right-hand side when the left doesn't
            # already decide the result.
            right_statements, right = self._lower_expr(expr.right)
            result = self._temp_name()
            if isinstance(expr.operator, cst.And):
                test: cst.BaseExpression = result
            else:
                test = _negate(result)
            evaluate_right = cst.If(
                test=test,
                body=cst.IndentedBlock(
                    body=[*right_statements, _assign(result, right)],
                ),
            )
            return [*left_statements, _assign(result, left), evaluate_right], result

        if isinstance(expr, cst.UnaryOperation):
            statements, operand = self._lower_expr(expr.expression)
            operator = expr.operator
            if isinstance(operator, cst.Not):
                # ``not(x := y)`` has no space after ``not``.
                operator = cst.Not()
            return statements, expr.with_changes(
                operator=operator,
                expression=operand,
            )

        if isinstance(expr, cst.IfExp):
            test_statements, test = self._lower_expr(expr.test)
            if not (_contains_walrus(expr.body) or _contains_walrus(expr.orelse)):
                return test_statements, expr.with_changes(test=test)
            body_statements, body = self._lower_expr(expr.body)
            orelse_statements, orelse = self._lower_expr(expr.orelse)
            result = self._temp_name()
            choose = cst.If(
                test=_standalone(test),
                body=cst.IndentedBlock(body=[*body_statements, _assign(result, body)]),
                orelse=cst.Else(
                    body=cst.IndentedBlock(
                        body=[*orelse_statements, _assign(result, orelse)],
                    ),
                ),
            )
            return [*test_statements, choose], result

        return self._hoist(expr)

    def _lower_and_groups(
        self,
        test: cst.BaseExpression,
    ) -> list[tuple[list[cst.BaseStatement], cst.BaseExpression]]:
        """Split an ``and`` chain into (statements, condition) steps.

        Each step's condition is only evaluated once the previous ones are
        true, which lets an ``if`` nest and a ``while`` break per step,
        without a temporary variable.
        """
        steps = []
        for group in _group_operands(_and_operands(test)):
            statements, first = self._lower_expr(group[0])
            steps.append((statements, _standalone(_join_and([first, *group[1:]]))))
        return steps

    def _lower_if(self, node: cst.If) -> list[cst.BaseStatement]:
        body = node.body.visit(self)
        orelse = self._lower_orelse(node.orelse)
        if not _contains_walrus(node.test):
            return [node.with_changes(body=body, orelse=orelse)]

        if orelse is None:
            statements: list[cst.BaseStatement] = []
            for step_statements, condition in reversed(
                self._lower_and_groups(node.test),
            ):
                if statements:
                    body = cst.IndentedBlock(body=statements)
                statements = [*step_statements, cst.If(test=condition, body=body)]
        else:
            test_statements, test = self._lower_expr(node.test)
            statements = [
                *test_statements,
                cst.If(test=_standalone(test), body=body, orelse=orelse),
            ]
        return _with_leading_lines(node, statements)

    def _lower_orelse(
        self,
        orelse: cst.If | cst.Else | None,
    ) -> cst.If | cst.Else | None:
        if isinstance(orelse, cst.If):
            statements = self._lower_if(orelse)
            if len(statements) == 1 and isinstance(statements[0], cst.If):
                return statements[0]
            # The elif condition needs statements of its own first.
            return cst.Else(body=cst.IndentedBlock(body=statements))
        if isinstance(orelse, cst.Else):
            return orelse.with_changes(body=orelse.body.visit(self))
        return orelse

    def _lower_while(self, node: cst.While) -> list[cst.BaseStatement]:
        body = node.body.visit(self)
        orelse = node.orelse
        if orelse is not None:
            orelse = orelse.with_changes(body=orelse.body.visit(self))
        if not _contains_walrus(node.test):
            return [node.with_changes(body=body, orelse=orelse)]

        # ``while True`` with a ``break`` per step of the condition. The
        # ``else`` clause would never run after that ``break``, so it is
        # moved after the loop and guarded by a flag set when the
        # condition (rather than the loop body) ends the loop.
        exhausted = self._temp_name() if orelse is not None else None
        stop: list[cst.BaseSmallStatement] = [cst.Break()]
        if exhausted is not None:
            stop.insert(0, _assign(exhausted, cst.Name("True")).body[0])
        checks: list[cst.BaseStatement] = []
        for step_statements, condition in self._lower_and_groups(node.test):
            checks += [
                *step_statements,
                cst.If(
                    test=_negate(condition),
                    body=cst.SimpleStatementSuite(body=stop),
                ),
            ]

        loop = node.with_changes(
            test=cst.Name("True"),
            body=cst.IndentedBlock(body=[*checks, *_block_statements(body)]),
            orelse=None,
        )
        if exhausted is None:
            return [loop]
        return _with_leading_lines(
            node,
            [
                _assign(exhausted, cst.Name("False")),
                loop.with_changes(leading_lines=[]),
                cst.If(test=exhausted, body=orelse.body),
            ],
        )

    def _extract_assignment_target(
        self,
        assignment: cst.Assign,
    ) -> cst.BaseAssignTargetExpression:
        """Extract the target from an assignment."""
        return assignment.targets[0].target

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

        # Collect if clauses from the original nested comprehension
        # These need to be moved to the outer comprehension since walrus variables
        # will be in scope there
        outer_ifs = []
        for for_clause in for_clauses:
            outer_ifs.extend(for_clause.ifs)

        # Update the comprehension with flattened structure
        return node.with_changes(
            for_in=cst.CompFor(
                target=new_target,
                iter=nested_gen,
                ifs=outer_ifs,  # Move if clauses to outer comprehension where walrus variables are in scope
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
        # NOTE: We remove if clauses from the inner generator because walrus variables
        # won't be in scope there. The if clauses should be moved to the outer comprehension.
        current_gen = None
        for i in range(len(for_clauses) - 1, -1, -1):
            current_gen = cst.CompFor(
                target=for_clauses[i].target,
                iter=for_clauses[i].iter,
                ifs=(),  # Remove if clauses from inner generator - they'll be handled by outer comprehension
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

        # Collect if clauses from the original nested comprehension
        # These need to be moved to the outer comprehension since walrus variables
        # will be in scope there
        outer_ifs = []
        for for_clause in for_clauses:
            outer_ifs.extend(for_clause.ifs)

        # Update the comprehension with flattened structure
        return node.with_changes(
            for_in=cst.CompFor(
                target=new_target,
                iter=nested_gen,
                ifs=outer_ifs,  # Move if clauses to outer comprehension where walrus variables are in scope
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

        # Collect if clauses from the original nested comprehension
        # These need to be moved to the outer comprehension since walrus variables
        # will be in scope there
        outer_ifs = []
        for for_clause in for_clauses:
            outer_ifs.extend(for_clause.ifs)

        # Update the comprehension with flattened structure
        return node.with_changes(
            for_in=cst.CompFor(
                target=new_target,
                iter=nested_gen,
                ifs=outer_ifs,  # Move if clauses to outer comprehension where walrus variables are in scope
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

    # Every statement collects the walrus assignments in its expressions,
    # and runs them just before itself.
    def on_visit(self, node: cst.CSTNode) -> bool:
        if isinstance(node, _SCOPED_STATEMENTS):
            self.assignments_stack.append([])
        return super().on_visit(node)

    def on_leave(self, original_node, updated_node):  # type: ignore[no-untyped-def]
        updated = super().on_leave(original_node, updated_node)
        if not isinstance(original_node, _SCOPED_STATEMENTS):
            return updated
        assignments = self.assignments_stack.pop()
        if not assignments:
            return updated
        nodes = (
            list(updated.nodes)
            if isinstance(updated, cst.FlattenSentinel)
            else [updated]
        )
        if isinstance(original_node, cst.BaseSmallStatement):
            return cst.FlattenSentinel([*assignments, *nodes])
        return cst.FlattenSentinel(
            _with_leading_lines(
                original_node,
                [
                    cst.SimpleStatementLine(body=assignments),
                    nodes[0].with_changes(leading_lines=[]),
                    *nodes[1:],
                ],
            ),
        )

    # ``if`` and ``while`` lower their own condition and children.
    def visit_If(self, node: cst.If) -> bool:
        return False

    def leave_If(
        self,
        original_node: cst.If,
        updated_node: cst.If,
    ) -> cst.BaseStatement | cst.FlattenSentinel:
        statements = self._lower_if(original_node)
        if len(statements) == 1:
            return statements[0]
        return cst.FlattenSentinel(statements)

    def visit_While(self, node: cst.While) -> bool:
        return False

    def leave_While(
        self,
        original_node: cst.While,
        updated_node: cst.While,
    ) -> cst.BaseStatement | cst.FlattenSentinel:
        statements = self._lower_while(original_node)
        if len(statements) == 1:
            return statements[0]
        return cst.FlattenSentinel(statements)

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

        self.assignments_stack[-1].append(
            cst.Assign(
                targets=[cst.AssignTarget(target=updated_node.target)],
                value=_standalone(updated_node.value),
            ),
        )
        # Reference the assigned value in place of the walrus.
        return updated_node.target

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
