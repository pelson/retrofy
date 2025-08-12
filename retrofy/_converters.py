import dataclasses
import typing

import libcst as cst

from ._transformations import match_statement, type_alias, walrus


class TypingTransformer(cst.CSTTransformer):
    def __init__(self, scope):
        self._scope = scope
        self._require_typing = False
        self._has_typing = False  # TODO: We can figure this out.

    def leave_Annotation(
        self,
        node: cst.Annotation,
        updated_node: cst.Annotation,
    ) -> cst.Annotation:
        if isinstance(updated_node.annotation, cst.BinaryOperation):
            # TODO: Use a matcher here.
            if isinstance(updated_node.annotation.operator, cst.BitOr):
                # print('ANNO scope', self._scope[node.annotation])
                self._require_typing = True
                new_node = cst.Subscript(
                    cst.Attribute(
                        cst.Name("typing"),
                        cst.Name("Union"),
                    ),
                    slice=(
                        cst.SubscriptElement(
                            updated_node.annotation.left,  # type: ignore
                        ),
                        cst.SubscriptElement(
                            updated_node.annotation.right,  # type: ignore
                        ),
                    ),
                )
                return cst.Annotation(new_node)

        return updated_node

    def leave_Module(self, node: cst.Module, updated_node: cst.Module) -> cst.Module:
        if self._require_typing:
            # Find the correct position to insert the typing import
            # It should be after any docstrings and __future__ imports
            insert_position = self._find_typing_import_position(updated_node.body)

            typing_import = cst.SimpleStatementLine(
                [
                    cst.Import(
                        [
                            cst.ImportAlias(
                                cst.Name("typing"),
                            ),
                        ],
                    ),
                ],
                trailing_whitespace=cst.TrailingWhitespace(
                    newline=cst.Newline(),
                ),
            )

            new_stmts = list(updated_node.body)
            new_stmts.insert(insert_position, typing_import)

            return dataclasses.replace(updated_node, body=tuple(new_stmts))
        return updated_node

    def _find_typing_import_position(
        self,
        body: typing.Tuple[cst.BaseStatement, ...],
    ) -> int:
        """Find the correct position to insert the typing import."""
        position = 0

        # Skip module docstrings
        if body and isinstance(body[0], cst.SimpleStatementLine):
            if (
                len(body[0].body) == 1
                and isinstance(body[0].body[0], cst.Expr)
                and isinstance(body[0].body[0].value, cst.SimpleString)
            ):
                position = 1

        # Skip __future__ imports
        for i in range(position, len(body)):
            stmt = body[i]
            if isinstance(stmt, cst.SimpleStatementLine):
                for substmt in stmt.body:
                    if (
                        isinstance(substmt, cst.ImportFrom)
                        and substmt.module
                        and isinstance(substmt.module, cst.Attribute)
                        and substmt.module.attr.value == "__future__"
                    ):
                        position = i + 1
                        break
                    elif (
                        isinstance(substmt, cst.ImportFrom)
                        and substmt.module
                        and isinstance(substmt.module, cst.Name)
                        and substmt.module.value == "__future__"
                    ):
                        position = i + 1
                        break
                else:
                    # If we didn't find a __future__ import in this statement, stop looking
                    break
            else:
                # If we hit a non-simple statement, stop looking
                break

        return position


def convert_union(module: cst.Module) -> cst.Module:
    """
    Given typing such as `SomeClass | AnotherClass`, convert this to
    `typing.Union[SomeClass, AnotherClass]`, with the appropriate `typing`
    import included.

    Note that this typically doesn't need to be done at runtime, and mostly is
    a type checking feature.

    """

    wrapper = cst.metadata.MetadataWrapper(module)
    metadata = wrapper.resolve(cst.metadata.ScopeProvider)

    transformer = TypingTransformer(metadata)
    return module.visit(transformer)


def convert_sequence_subscript(module: cst.Module) -> cst.Module:
    """
    Convert the built-in sequence types such as `list[int]` to the
    `typing.List[int]` form.

    Currently only `list[int]` is supported.
    """
    # TODO: Do this correctly. Including adding the necessry typing import.
    orig = module.code
    new = orig.replace("list[str]", "typing.List[str]")
    if new != orig:
        return cst.parse_module(new)
    return module


def convert_walrus_operator(module: cst.Module) -> cst.Module:
    return module.visit(walrus.WalrusOperatorTransformer())


def convert_type_alias(module: cst.Module) -> cst.Module:
    return module.visit(type_alias.PEP695Transformer())


def convert_match_statement(module: cst.Module) -> cst.Module:
    return module.visit(match_statement.MatchStatementTransformer())


def convert(code: str) -> str:
    mod = cst.parse_module(code)
    mod = convert_sequence_subscript(mod)
    mod = convert_walrus_operator(mod)
    mod = convert_type_alias(mod)
    mod = convert_union(mod)
    return mod.code
