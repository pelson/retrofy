from __future__ import annotations
import typing
import libcst as cst


class WalrusOperatorTransformer(cst.CSTTransformer):
    """
    A transformer that replaces walrus operator (`:=`) with classic assignment syntax.

    See PEP-572 for details.
    """
    def __init__(self):
        self.assignments_stack: typing.List[typing.List[cst.Expr]] = []
        super().__init__()

    def leave_NamedExpr(self, node: cst.NamedExpr, updated_node: cst.NamedExpr) -> cst.NamedExpr:
        if isinstance(node.target, cst.Name):
            target = node.target
            value = node.value

            assign_stmt = cst.Assign(
                targets=[cst.AssignTarget(target=target)],
                value=value
            )
            self.assignments_stack[-1].append(assign_stmt)
            updated_node = cst.Expr(cst.Name(target.value))
        return updated_node

    def visit_If(self, node: cst.If):
        self.assignments_stack.append([])

    def leave_If(self, original_node: cst.BaseCompoundStatement, updated_node: cst.BaseCompoundStatement) -> cst.BaseCompoundStatement | cst.FlattenSentinel:
        assignments = self.assignments_stack.pop()
        if assignments:
            new_nodes = cst.FlattenSentinel([cst.SimpleStatementLine([t for t in assignments])] + [updated_node])
            return new_nodes
        else:
            return updated_node

    def visit_While(self, node: cst.While):
        self.assignments_stack.append([])

    def leave_While(
        self, original_node: cst.While, updated_node: cst.While,
    ):
        assignments = self.assignments_stack.pop()
        if assignments:
            new_body = list(updated_node.body.body)
            assn = [cst.SimpleStatementLine([t for t in assignments])]

            if isinstance(updated_node.test, cst.BaseExpression):
                new_test = updated_node.test.with_changes(
                    lpar=[cst.LeftParen()], rpar=[cst.RightParen()],
                )
            else:
                new_test = updated_node.test

            new_test = cst.If(
                test=cst.UnaryOperation(
                    operator=cst.Not(),
                    expression=new_test,
                ),
                body=cst.SimpleStatementSuite([cst.Break()]),
            )
            updated_node = updated_node.with_changes(
                body=updated_node.body.with_changes(
                    body=assn + [new_test] + new_body,
                ),
                test=cst.parse_expression('True'),
            )
            return updated_node
        else:
            return updated_node

    def visit_Assign(self, node: cst.Assign):
        self.assignments_stack.append([])

    def leave_Assign(self, original_node: cst.Assign, updated_node: cst.Assign) -> cst.Assign | cst.FlattenSentinel:
        assignments = self.assignments_stack.pop()
        if assignments:
            new_nodes = cst.FlattenSentinel(assignments + [updated_node])
            return new_nodes
        else:
            return updated_node

    def visit_Expr(self, node: cst.Expr):
        self.assignments_stack.append([])

    def leave_Expr(
        self, original_node: cst.Expr, updated_node: cst.Expr,
    ) -> cst.Expr:
        assignments = self.assignments_stack.pop()
        if assignments:
            new_nodes = cst.FlattenSentinel(assignments + [updated_node])
            return new_nodes
        else:
            return updated_node

    def visit_ListComp(self, node: cst.ListComp):
        self.assignments_stack.append([])

    def leave_ListComp(self, node: cst.ListComp, updated_node: cst.ListComp):
        assignments = self.assignments_stack.pop()
        if assignments and node.for_in.inner_for_in is not None:
            raise ValueError("Unable to transform comprehensions with inner for loops")

        if not assignments:
            return updated_node

        def join_list(left: cst.Node | cst.List, right: cst.Node | cst.List, with_pars=False):
            if with_pars:
                lpar, rpar = [cst.LeftSquareBracket()], [cst.RightSquareBracket()]
            else:
                lpar = rpar = ()

            if isinstance(left, cst.List):
                pass
            elif not isinstance(left, cst.BaseElement):
                left = cst.Tuple(elements=[cst.Element(left)])
            if isinstance(right, cst.List):
                pass
            elif not isinstance(right, cst.BaseElement):
                right = cst.Tuple(elements=[cst.Element(right)])
            return cst.Tuple(elements=left.elements + right.elements, lpar=lpar, rpar=rpar)

        ifs = updated_node.for_in.ifs
        inner = cst.CompFor(
            target=join_list(updated_node.for_in.target, cst.List([cst.Element(a.targets[0].target) for a in assignments])),
            iter=cst.GeneratorExp(
                elt=join_list(updated_node.for_in.target, cst.List([cst.Element(a.value) for a in assignments]), with_pars=True),
                for_in=cst.CompFor(
                    target=updated_node.for_in.target,
                    iter=updated_node.for_in.iter,
                ),
            ),
            ifs=ifs,
        )
        updated_node = updated_node.with_changes(for_in=inner)

        return updated_node
