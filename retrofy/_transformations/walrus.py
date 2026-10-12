from __future__ import annotations

import typing

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


_COMPREHENSIONS = (cst.ListComp, cst.SetComp, cst.DictComp, cst.GeneratorExp)
_Comprehension = typing.Union[cst.ListComp, cst.SetComp, cst.DictComp, cst.GeneratorExp]


class _WalrusTargets(cst.CSTVisitor):
    """Collect the names bound by walruses, outside nested lambdas (whose
    walruses bind in the lambda's own scope)."""

    def __init__(self) -> None:
        self.names: list[str] = []

    def visit_Lambda(self, node: cst.Lambda) -> bool:
        return False

    def visit_NamedExpr(self, node: cst.NamedExpr) -> None:
        if isinstance(node.target, cst.Name) and node.target.value not in self.names:
            self.names.append(node.target.value)


class _ScopeDeclarations(cst.CSTVisitor):
    """The ``global`` / ``nonlocal`` names declared directly in a function
    body (not in nested functions or classes)."""

    def __init__(self, body: cst.CSTNode) -> None:
        self.body = body
        self.globals: set[str] = set()
        self.nonlocals: set[str] = set()

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        return False

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        return False

    def visit_Global(self, node: cst.Global) -> None:
        self.globals.update(item.name.value for item in node.names)

    def visit_Nonlocal(self, node: cst.Nonlocal) -> None:
        self.nonlocals.update(item.name.value for item in node.names)


def _mangle(name: str, class_name: str | None) -> str:
    """The name the compiler uses for *name* inside class *class_name*:
    private names (``__x``, but not ``__x__``) are mangled to ``_Class__x``.
    """
    if class_name is None or not name.startswith("__") or name.endswith("__"):
        return name
    stripped = class_name.lstrip("_")
    return f"_{stripped}{name}" if stripped else name


def _first_evaluated(expr: cst.BaseExpression) -> cst.BaseExpression:
    """The sub-expression of *expr* that is evaluated first."""
    while True:
        if isinstance(
            expr,
            (cst.Comparison, cst.BooleanOperation, cst.BinaryOperation),
        ):
            expr = expr.left
        elif isinstance(expr, cst.UnaryOperation):
            expr = expr.expression
        elif isinstance(expr, (cst.Attribute, cst.Subscript)):
            expr = expr.value
        elif isinstance(expr, cst.Call):
            expr = expr.func
        else:
            return expr


_DYNAMIC_SCOPE_ACCESS = m.Name(
    value=m.MatchIfTrue(lambda name: name in {"locals", "vars", "eval", "exec"}),
)


def _comprehension_is_async(node: _Comprehension) -> bool:
    """Whether *node* needs an async generator: ``async for`` anywhere, or
    ``await`` outside the first iterable (which runs in the enclosing
    scope)."""
    for_in: cst.CompFor | None = node.for_in
    parts: list[cst.CSTNode] = []
    first = True
    while for_in is not None:
        if for_in.asynchronous is not None:
            return True
        if not first:
            parts.append(for_in.iter)
        parts.extend(for_in.ifs)
        first = False
        for_in = for_in.inner_for_in
    if isinstance(node, cst.DictComp):
        parts += [node.key, node.value]
    else:
        parts.append(node.elt)
    return any(m.findall(part, m.Await()) for part in parts)


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

    4. Comprehensions and generator expressions (list / set / dict / genexp).
       A walrus target binds in the enclosing function (or module) scope and
       is assigned per item. Where nothing else can see the binding (one
       walrus, evaluated first in the innermost clause's first filter, its
       target unused elsewhere in the scope), the value is threaded through
       an inner generator, keeping the comprehension in place:

       [y for x in data if (y := f(x)) is not None] ->
       [y for x, y in ((x, f(x)) for x in data) if y is not None]

       Otherwise the comprehension becomes a call to a helper generator
       defined before the statement (with the original as a comment),
       declaring the targets ``nonlocal`` (or ``global``):

       total = [t := t + x for x in data] ->
       def _retrofy_comp_0(_retrofy_iter):
           # [t := t + x for x in data]
           nonlocal t
           for x in _retrofy_iter:
               t = t + x; yield t
       total = [_retrofy_item for _retrofy_item in _retrofy_comp_0(data)]

       In a function, a never-run ``if False: t = None`` gives ``nonlocal`` a
       binding. Generator expressions stay lazy; async comprehensions get an
       ``async def`` helper.

    5. Short-circuiting boolean expressions in if / while conditions:
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
    - A walrus in a comprehension inside a ``lambda`` binds in the lambda's
      scope, which the helper can't reach; such comprehensions are left to
      plain hoisting.
    - Short-circuiting is only preserved through ``and``/``or``/``not`` and
      conditional expressions. Assignments inside any other expression
      (e.g. ``f((x := a()) and (y := b()))``) are hoisted in front of the
      statement, ahead of the rest of the expression.
    - Walrus in lambda expressions may have edge cases

    See PEP-572 for details.
    """

    def __init__(self) -> None:
        self.assignments_stack: list[list[cst.Assign]] = []
        # Statements (helper functions) to run before the current statement.
        self._preludes: list[list[cst.BaseStatement]] = []
        # Enclosing scopes, innermost last: ("function", _ScopeDeclarations),
        # ("lambda", None), ("class", <class name>) or ("generated", None)
        # for the helpers below. Module scope is the empty stack.
        self._scopes: list[tuple[str, _ScopeDeclarations | str | None]] = []
        # Nodes whose visit enters a new scope (function / lambda / class
        # bodies), mapped to the scope entry to push.
        self._scope_entries: dict[
            int,
            tuple[str, _ScopeDeclarations | str | None],
        ] = {}
        self._module: cst.Module | None = None
        self._temp_count = 0
        self._comp_count = 0
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

    def _enclosing_scope(self) -> tuple[str, _ScopeDeclarations | None, bool]:
        """The innermost real (non-helper) scope, and whether the current
        position is inside a generated helper."""
        in_helper = False
        for kind, data in reversed(self._scopes):
            if kind == "generated":
                in_helper = True
                continue
            declarations = data if isinstance(data, _ScopeDeclarations) else None
            return kind, declarations, in_helper
        return "module", None, in_helper

    def _enclosing_class(self) -> str | None:
        for kind, data in reversed(self._scopes):
            if kind == "class":
                assert isinstance(data, str)
                return data
        return None

    def _thread_through_generator(
        self,
        node: _Comprehension,
        scope: cst.CSTNode,
    ) -> _Comprehension | None:
        """The one-line lowering, where it is provably equivalent.

        ``[y for x in data if (y := f(x)) is not None]`` becomes
        ``[y for x, y in ((x, f(x)) for x in data) if y is not None]``: the
        walrus value is computed alongside the loop variables in an inner
        generator. That keeps the comprehension on its own lines (and lazy),
        but ``y`` then only exists inside the comprehension, so it is only
        used when nothing else in *scope* refers to it, and when the walrus
        is evaluated first in its clause, so moving it into the inner
        generator doesn't change what runs.
        """
        walruses = m.findall(node, m.NamedExpr())
        if len(walruses) != 1 or _comprehension_is_async(node):
            return None
        walrus = walruses[0]
        assert isinstance(walrus, cst.NamedExpr)
        if not isinstance(walrus.target, cst.Name):
            return None
        name = walrus.target.value

        clauses: list[cst.CompFor] = []
        for_in: cst.CompFor | None = node.for_in
        while for_in is not None:
            clauses.append(for_in)
            for_in = for_in.inner_for_in
        last = clauses[-1]
        # The walrus must come first in the first ``if`` of the innermost
        # clause: nothing evaluated before it in an item is then reordered.
        if not last.ifs or _first_evaluated(last.ifs[0].test) is not walrus:
            return None

        # Within an item, ``name`` may only be read after the walrus: in the
        # innermost clause's ``if``s or in the produced value(s).
        target = m.Name(value=name)
        after = [
            *last.ifs,
            *([node.key, node.value] if isinstance(node, cst.DictComp) else [node.elt]),
        ]
        inside = len(m.findall(node, target))
        allowed = sum(len(m.findall(part, target)) for part in after)
        if inside != allowed or m.findall(walrus.value, target):
            return None
        # Nothing else in the scope may see the binding.
        if len(m.findall(scope, target)) != inside or m.findall(
            scope,
            _DYNAMIC_SCOPE_ACCESS,
        ):
            return None

        def element(target_expr: cst.BaseExpression) -> cst.Element:
            if isinstance(target_expr, cst.Tuple) and not target_expr.lpar:
                target_expr = _parenthesize(target_expr)
            return cst.Element(target_expr)

        loop_targets = [element(clause.target) for clause in clauses]
        inner_for_in = node.for_in.deep_replace(last, last.with_changes(ifs=[]))
        assert isinstance(inner_for_in, cst.CompFor)
        inner = cst.GeneratorExp(
            elt=cst.Tuple(elements=[*loop_targets, cst.Element(walrus.value)]),
            # The original layout before ``for`` goes to the outer clause.
            for_in=inner_for_in.with_changes(
                whitespace_before=cst.SimpleWhitespace(" "),
            ),
        )
        first_if = last.ifs[0]
        outer = cst.CompFor(
            whitespace_before=node.for_in.whitespace_before,
            target=cst.Tuple(
                elements=[*loop_targets, cst.Element(walrus.target)],
                lpar=[],
                rpar=[],
            ),
            iter=inner,
            ifs=[
                first_if.with_changes(
                    test=first_if.test.deep_replace(walrus, walrus.target),
                ),
                *last.ifs[1:],
            ],
        )
        return node.with_changes(for_in=outer)

    def _lower_comprehension(self, node: _Comprehension) -> cst.BaseExpression:
        """Lower a comprehension containing walruses to a helper generator.

        PEP 572: a walrus target in a comprehension binds in the enclosing
        function (or module) scope, and is assigned once per item. The
        comprehension becomes a call to a generator function defined just
        before the statement, which declares the targets ``nonlocal`` (or
        ``global``) and loops exactly like the comprehension. The first
        iterable is passed in, so it is still evaluated eagerly and in
        the enclosing scope. Defining the helper has no side effects, so
        hoisting it is safe; generator expressions stay lazy.
        """
        kind, declarations, in_helper = self._enclosing_scope()
        targets = _WalrusTargets()
        node.visit(targets)
        prelude: list[cst.BaseStatement] = []
        header: list[cst.BaseStatement] = []
        if kind == "module":
            global_names, nonlocal_names = targets.names, []
        else:
            assert declarations is not None
            # Compare as the compiler does: ``global __x`` in a method
            # declares ``_Class__x``.
            class_name = self._enclosing_class()
            declared_globals = {_mangle(n, class_name) for n in declarations.globals}
            declared_nonlocals = {
                _mangle(n, class_name) for n in declarations.nonlocals
            }
            global_names = [
                n for n in targets.names if _mangle(n, class_name) in declared_globals
            ]
            nonlocal_names = [n for n in targets.names if n not in global_names]
            if not in_helper:
                # ``nonlocal`` needs a binding in the enclosing function. The
                # walrus makes the name local to it anyway, so a never-run
                # assignment changes nothing else.
                for name in nonlocal_names:
                    if _mangle(name, class_name) not in declared_nonlocals:
                        prelude.append(
                            cst.If(
                                test=cst.Name("False"),
                                body=cst.SimpleStatementSuite(
                                    body=[
                                        cst.Assign(
                                            targets=[cst.AssignTarget(cst.Name(name))],
                                            value=cst.Name("None"),
                                        ),
                                    ],
                                ),
                            ),
                        )
        if global_names:
            header.append(
                cst.SimpleStatementLine(
                    body=[
                        cst.Global(
                            names=[cst.NameItem(cst.Name(n)) for n in global_names],
                        ),
                    ],
                ),
            )
        if nonlocal_names:
            header.append(
                cst.SimpleStatementLine(
                    body=[
                        cst.Nonlocal(
                            names=[cst.NameItem(cst.Name(n)) for n in nonlocal_names],
                        ),
                    ],
                ),
            )

        if isinstance(node, cst.DictComp):
            produced: cst.BaseExpression = cst.Tuple(
                elements=[cst.Element(node.key), cst.Element(node.value)],
            )
        else:
            produced = node.elt
            if isinstance(produced, cst.NamedExpr) and not produced.lpar:
                produced = _parenthesize(produced)
        produce = cst.SimpleStatementLine(body=[cst.Expr(cst.Yield(value=produced))])
        iterable = cst.Name("_retrofy_iter")
        is_async = _comprehension_is_async(node)
        helper_name = cst.Name(f"_retrofy_comp_{self._comp_count}")
        self._comp_count += 1
        # Keep the original comprehension visible, as a comment.
        original = [
            cst.EmptyLine(comment=cst.Comment(f"# {line}".rstrip()))
            for line in cst.Module(body=[]).code_for_node(node).splitlines()
        ]
        body = [*header, *self._comprehension_loops(node.for_in, iterable, produce)]
        body[0] = body[0].with_changes(leading_lines=original)
        helper = cst.FunctionDef(
            name=helper_name,
            params=cst.Parameters(params=[cst.Param(iterable)]),
            body=cst.IndentedBlock(body=body),
            asynchronous=cst.Asynchronous() if is_async else None,
        )
        # Lower the walruses (and any nested comprehensions) in the helper.
        self._scopes.append(("generated", None))
        helper = helper.with_changes(body=helper.body.visit(self))
        self._scopes.pop()
        self._preludes[-1].extend([*prelude, helper])

        call = cst.Call(func=helper_name, args=[cst.Arg(_standalone(node.for_in.iter))])
        if isinstance(node, cst.GeneratorExp):
            return call.with_changes(lpar=node.lpar, rpar=node.rpar)
        item = cst.Name("_retrofy_item")
        for_in = cst.CompFor(
            target=item,
            iter=call,
            asynchronous=cst.Asynchronous() if is_async else None,
        )
        if isinstance(node, cst.DictComp):
            key, value = cst.Name("_retrofy_key"), cst.Name("_retrofy_value")
            return node.with_changes(
                key=key,
                value=value,
                for_in=for_in.with_changes(
                    target=cst.Tuple(
                        elements=[cst.Element(key), cst.Element(value)],
                        lpar=[],
                        rpar=[],
                    ),
                ),
            )
        return node.with_changes(elt=item, for_in=for_in)

    def _comprehension_loops(
        self,
        for_in: cst.CompFor,
        iterable: cst.BaseExpression,
        innermost: cst.BaseStatement,
    ) -> list[cst.BaseStatement]:
        """The nested ``for`` / ``if`` statements equivalent to *for_in*."""
        if for_in.inner_for_in is None:
            body = [innermost]
        else:
            body = self._comprehension_loops(
                for_in.inner_for_in,
                _standalone(for_in.inner_for_in.iter),
                innermost,
            )
        for condition in reversed(for_in.ifs):
            body = [
                cst.If(
                    test=_standalone(condition.test),
                    body=cst.IndentedBlock(body=body),
                ),
            ]
        return [
            cst.For(
                target=_standalone(for_in.target),
                iter=iterable,
                body=cst.IndentedBlock(body=body),
                asynchronous=for_in.asynchronous,
            ),
        ]

    # Every statement collects the walrus assignments in its expressions,
    # and runs them just before itself.
    # Statement lines (and one-line suites) collect the helper functions
    # needed by their expressions, and run them just before themselves.
    def on_visit(self, node: cst.CSTNode) -> bool:
        if id(node) in self._scope_entries:
            self._scopes.append(self._scope_entries[id(node)])
        if isinstance(node, _SCOPED_STATEMENTS):
            self.assignments_stack.append([])
        if isinstance(
            node,
            (
                cst.SimpleStatementLine,
                cst.SimpleStatementSuite,
                cst.BaseCompoundStatement,
            ),
        ):
            self._preludes.append([])
        return super().on_visit(node)

    def on_leave(self, original_node, updated_node):  # type: ignore[no-untyped-def]
        updated = super().on_leave(original_node, updated_node)
        if id(original_node) in self._scope_entries:
            self._scopes.pop()
        if isinstance(original_node, _SCOPED_STATEMENTS):
            updated = self._prepend_assignments(original_node, updated)
        if isinstance(
            original_node,
            (
                cst.SimpleStatementLine,
                cst.SimpleStatementSuite,
                cst.BaseCompoundStatement,
            ),
        ):
            prelude = self._preludes.pop()
            if prelude:
                if isinstance(original_node, cst.SimpleStatementSuite):
                    # ``if c: x = [...]``: the helper needs a block of its own.
                    return cst.IndentedBlock(
                        body=[*prelude, cst.SimpleStatementLine(body=updated.body)],
                    )
                nodes = (
                    list(updated.nodes)
                    if isinstance(updated, cst.FlattenSentinel)
                    else [updated]
                )
                return cst.FlattenSentinel(
                    _with_leading_lines(
                        original_node,
                        [
                            *prelude,
                            nodes[0].with_changes(leading_lines=[]),
                            *nodes[1:],
                        ],
                    ),
                )
        return updated

    def _prepend_assignments(self, original_node, updated):  # type: ignore[no-untyped-def]
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

    # Scopes start at a function / lambda / class *body*: decorators,
    # defaults and bases run in the enclosing scope.
    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        declarations = _ScopeDeclarations(node.body)
        node.body.visit(declarations)
        self._scope_entries[id(node.body)] = ("function", declarations)

    def visit_Lambda(self, node: cst.Lambda) -> None:
        self._scope_entries[id(node.body)] = ("lambda", None)

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        self._scope_entries[id(node.body)] = ("class", node.name.value)

    def _visit_comprehension(self, node: _Comprehension) -> bool:
        # Handled as a whole in _leave_comprehension. Inside a lambda the
        # targets bind in the lambda's scope, which a helper defined before
        # the statement can't reach, so fall back to plain hoisting.
        if not _contains_walrus(node):
            return True
        return self._enclosing_scope()[0] == "lambda"

    def _leave_comprehension(self, original_node, updated_node):  # type: ignore[no-untyped-def]
        kind, declarations, _ = self._enclosing_scope()
        if not _contains_walrus(original_node) or kind == "lambda":
            return updated_node
        scope = declarations.body if declarations is not None else self._module
        assert scope is not None
        threaded = self._thread_through_generator(original_node, scope)
        if threaded is not None:
            return threaded
        return self._lower_comprehension(original_node)

    def visit_Module(self, node: cst.Module) -> None:
        self._module = node

    def visit_ListComp(self, node: cst.ListComp) -> bool:
        return self._visit_comprehension(node)

    def visit_SetComp(self, node: cst.SetComp) -> bool:
        return self._visit_comprehension(node)

    def visit_DictComp(self, node: cst.DictComp) -> bool:
        return self._visit_comprehension(node)

    def visit_GeneratorExp(self, node: cst.GeneratorExp) -> bool:
        return self._visit_comprehension(node)

    def leave_ListComp(self, original_node, updated_node):  # type: ignore[no-untyped-def]
        return self._leave_comprehension(original_node, updated_node)

    def leave_SetComp(self, original_node, updated_node):  # type: ignore[no-untyped-def]
        return self._leave_comprehension(original_node, updated_node)

    def leave_DictComp(self, original_node, updated_node):  # type: ignore[no-untyped-def]
        return self._leave_comprehension(original_node, updated_node)

    def leave_GeneratorExp(self, original_node, updated_node):  # type: ignore[no-untyped-def]
        return self._leave_comprehension(original_node, updated_node)

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
