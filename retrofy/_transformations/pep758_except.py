"""PEP 758 (3.14): ``except`` / ``except*`` with several exception types
and no parentheses.

``except A, B:`` is lowered to ``except (A, B):``, which every supported
Python accepts with the same meaning.
"""

from __future__ import annotations

import typing

import libcst as cst

_Handler = typing.TypeVar("_Handler", cst.ExceptHandler, cst.ExceptStarHandler)


def _parenthesize_type(handler: _Handler) -> _Handler:
    if isinstance(handler.type, cst.Tuple) and not handler.type.lpar:
        return handler.with_changes(
            type=handler.type.with_changes(
                lpar=[cst.LeftParen()],
                rpar=[cst.RightParen()],
            ),
        )
    return handler


class PEP758ExceptTransformer(cst.CSTTransformer):
    def leave_ExceptHandler(
        self,
        original_node: cst.ExceptHandler,
        updated_node: cst.ExceptHandler,
    ) -> cst.ExceptHandler:
        return _parenthesize_type(updated_node)

    def leave_ExceptStarHandler(
        self,
        original_node: cst.ExceptStarHandler,
        updated_node: cst.ExceptStarHandler,
    ) -> cst.ExceptStarHandler:
        return _parenthesize_type(updated_node)


def convert(module: cst.Module) -> cst.Module:
    return module.visit(PEP758ExceptTransformer())
