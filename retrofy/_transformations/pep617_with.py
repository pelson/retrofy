"""PEP 617 (3.10; CPython 3.9's parser already accepts it): parenthesised
context managers.

``with (a as x, b as y):`` is lowered to ``with a as x, b as y:``. Line
breaks and comments inside the parentheses move into parentheses around
the individual items (and ``as`` targets), which every Python accepts, so
both the comments and every line number (for tracebacks) are kept::

    with (                      with (
        open(a) as fa,  # c         open(a)) as fa, (  # c
        open(b) as fb,      ->      open(b)) as (fb
    ):                          ):
"""

from __future__ import annotations

import libcst as cst

_EMPTY = cst.SimpleWhitespace("")


def _is_simple(ws: cst.BaseParenthesizableWhitespace) -> bool:
    # Line breaks and comments only ever live in ParenthesizedWhitespace.
    return isinstance(ws, cst.SimpleWhitespace)


def _join(
    first: cst.BaseParenthesizableWhitespace,
    second: cst.BaseParenthesizableWhitespace,
) -> cst.BaseParenthesizableWhitespace:
    """Whitespace equivalent to *first* followed by *second*."""
    if isinstance(first, cst.SimpleWhitespace) and isinstance(
        second,
        cst.SimpleWhitespace,
    ):
        return cst.SimpleWhitespace(first.value + second.value)
    if isinstance(first, cst.SimpleWhitespace):
        assert isinstance(second, cst.ParenthesizedWhitespace)
        return second.with_changes(
            first_line=second.first_line.with_changes(
                whitespace=cst.SimpleWhitespace(
                    first.value + second.first_line.whitespace.value,
                ),
            ),
        )
    assert isinstance(first, cst.ParenthesizedWhitespace)
    if isinstance(second, cst.SimpleWhitespace):
        return first.with_changes(
            last_line=cst.SimpleWhitespace(first.last_line.value + second.value),
        )
    assert isinstance(second, cst.ParenthesizedWhitespace)
    # The second's first line continues the first's last line.
    joined_line = cst.EmptyLine(
        indent=first.indent,
        whitespace=cst.SimpleWhitespace(
            first.last_line.value + second.first_line.whitespace.value,
        ),
        comment=second.first_line.comment,
        newline=second.first_line.newline,
    )
    return first.with_changes(
        empty_lines=[*first.empty_lines, joined_line, *second.empty_lines],
        indent=second.indent,
        last_line=second.last_line,
    )


def _wrap(
    node: cst.BaseExpression,
    before: cst.BaseParenthesizableWhitespace,
    after: cst.BaseParenthesizableWhitespace,
) -> cst.BaseExpression:
    """*node* in a new pair of parentheses holding *before* / *after*."""
    return node.with_changes(
        lpar=[cst.LeftParen(whitespace_after=before), *node.lpar],
        rpar=[*node.rpar, cst.RightParen(whitespace_before=after)],
    )


def _parses_alone(code: str) -> bool:
    try:
        cst.parse_expression(code)
    except cst.ParserSyntaxError:
        return False
    return True


class PEP617WithTransformer(cst.CSTTransformer):
    def leave_With(self, original_node: cst.With, updated_node: cst.With) -> cst.With:
        if not isinstance(updated_node.lpar, cst.LeftParen):
            return updated_node
        assert isinstance(updated_node.rpar, cst.RightParen)
        module = cst.Module(body=[])
        items = list(updated_node.items)

        # The whitespace before / after each item, as found between the
        # enclosing parentheses and commas.
        befores: list[cst.BaseParenthesizableWhitespace] = [
            updated_node.lpar.whitespace_after,
        ]
        afters: list[cst.BaseParenthesizableWhitespace] = []
        for i, item in enumerate(items):
            comma = item.comma
            last = i == len(items) - 1
            if isinstance(comma, cst.Comma):
                if last:
                    # A trailing comma is only valid inside the parentheses.
                    afters.append(
                        _join(
                            _join(comma.whitespace_before, comma.whitespace_after),
                            updated_node.rpar.whitespace_before,
                        ),
                    )
                else:
                    afters.append(comma.whitespace_before)
                    befores.append(comma.whitespace_after)
            else:
                afters.append(updated_node.rpar.whitespace_before)

        new_items = []
        for i, item in enumerate(items):
            before, after = befores[i], afters[i]
            expr = item.item
            asname = item.asname
            if asname is None:
                if not (_is_simple(before) and _is_simple(after)):
                    expr = _wrap(expr, before, after)
                elif "\n" in module.code_for_node(expr) and not _parses_alone(
                    module.code_for_node(expr),
                ):
                    expr = _wrap(expr, _EMPTY, _EMPTY)
            else:
                # Plain spaces around ``as`` stay where they are; line
                # breaks and comments move inside the new parentheses.
                before_as = asname.whitespace_before_as
                after_as = asname.whitespace_after_as
                if not (_is_simple(before) and _is_simple(before_as)):
                    inner = _EMPTY if _is_simple(before_as) else before_as
                    expr = _wrap(expr, before, inner)
                    if not _is_simple(before_as):
                        before_as = cst.SimpleWhitespace(" ")
                elif "\n" in module.code_for_node(expr) and not _parses_alone(
                    module.code_for_node(expr),
                ):
                    expr = _wrap(expr, _EMPTY, _EMPTY)
                target = asname.name
                if not (_is_simple(after_as) and _is_simple(after)):
                    inner = _EMPTY if _is_simple(after_as) else after_as
                    target = _wrap(target, inner, after)
                    if not _is_simple(after_as):
                        after_as = cst.SimpleWhitespace(" ")
                asname = asname.with_changes(
                    name=target,
                    whitespace_before_as=before_as,
                    whitespace_after_as=after_as,
                )
            comma = item.comma
            if i == len(items) - 1:
                comma = cst.MaybeSentinel.DEFAULT
            elif isinstance(comma, cst.Comma):
                comma = comma.with_changes(
                    whitespace_before=_EMPTY,
                    whitespace_after=cst.SimpleWhitespace(" "),
                )
            new_items.append(item.with_changes(item=expr, asname=asname, comma=comma))

        return updated_node.with_changes(
            items=new_items,
            lpar=cst.MaybeSentinel.DEFAULT,
            rpar=cst.MaybeSentinel.DEFAULT,
        )


def convert(module: cst.Module) -> cst.Module:
    return module.visit(PEP617WithTransformer())
