import dataclasses

import libcst as cst

from ._transformations import (
    collections_abc,
    dataclass,
    lazy_imports,
    match_statement,
    pep585_builtins,
    pep585_imports,
    type_alias,
    typing_extensions,
    walrus,
)
from ._transformations.import_utils import EnhancedImportManager


def _subscript_name(node: cst.Subscript) -> str | None:
    value = node.value
    if isinstance(value, cst.Name):
        return value.value
    if isinstance(value, cst.Attribute):
        return value.attr.value
    return None


def _is_simple(ws: cst.BaseParenthesizableWhitespace) -> bool:
    # Comments and newlines only ever live in ParenthesizedWhitespace.
    return isinstance(ws, cst.SimpleWhitespace)


def _comma_for(op: cst.BitOr) -> cst.Comma:
    """A ``,`` carrying whatever layout (comments, newlines) the ``|`` had."""
    before, after = op.whitespace_before, op.whitespace_after
    if _is_simple(before) and _is_simple(after):
        return cst.Comma(whitespace_after=cst.SimpleWhitespace(" "))
    if _is_simple(after):
        # ``int  # comment\n | None``: keep the comma on the first line.
        return cst.Comma(whitespace_after=before)
    if _is_simple(before):
        return cst.Comma(whitespace_after=after)
    return cst.Comma(whitespace_before=before, whitespace_after=after)


def _parens_are_simple(node: cst.BaseExpression) -> bool:
    return all(_is_simple(lp.whitespace_after) for lp in node.lpar) and all(
        _is_simple(rp.whitespace_before) for rp in node.rpar
    )


class _UnionLowerer(cst.CSTTransformer):
    """Lower every PEP 604 ``|`` within a single type expression.

    Chains are flattened (``A | B | C`` -> ``typing.Union[A, B, C]``) and
    a union of exactly one type with ``None`` becomes ``typing.Optional``.
    Comments and line breaks around each ``|`` are carried over onto the
    ``,`` which replaces it; where that isn't possible (``Optional`` has
    no separator) the ``typing.Union`` form is used instead.
    """

    def __init__(self) -> None:
        # Unions this transformer emitted, keyed by node identity, mapped
        # to their members and separators so that an enclosing ``|`` can
        # flatten them.
        self._emitted: dict[
            int,
            tuple[list[cst.BaseExpression], list[cst.Comma]],
        ] = {}
        self._skip: set[int] = set()
        self.lowered = False

    def visit_Subscript(self, node: cst.Subscript) -> None:
        # ``|`` in Literal values / Annotated metadata is a value-level
        # bitwise-or, not a type union.
        name = _subscript_name(node)
        if name == "Literal":
            self._skip.update(id(el) for el in node.slice)
        elif name == "Annotated":
            self._skip.update(id(el) for el in node.slice[1:])

    def visit_SubscriptElement(self, node: cst.SubscriptElement) -> bool:
        return id(node) not in self._skip

    def _members(
        self,
        expr: cst.BaseExpression,
    ) -> tuple[list[cst.BaseExpression], list[cst.Comma]]:
        # Flattening drops the parentheses of ``(A | B) | C``, so only do
        # it when they hold no comments or line breaks.
        if id(expr) in self._emitted and _parens_are_simple(expr):
            return self._emitted[id(expr)]
        return [expr], []

    def leave_BinaryOperation(
        self,
        original_node: cst.BinaryOperation,
        updated_node: cst.BinaryOperation,
    ) -> cst.BaseExpression:
        if not isinstance(updated_node.operator, cst.BitOr):
            return updated_node
        self.lowered = True
        left_members, left_commas = self._members(updated_node.left)
        right_members, right_commas = self._members(updated_node.right)
        members = left_members + right_members
        commas = left_commas + [_comma_for(updated_node.operator)] + right_commas

        # ``None`` is a keyword, so a ``None`` name is always the singleton.
        non_none = [
            m for m in members if not (isinstance(m, cst.Name) and m.value == "None")
        ]
        lossless = all(
            _is_simple(c.whitespace_before) and _is_simple(c.whitespace_after)
            for c in commas
        )
        if len(non_none) == 1 and len(members) == 2 and lossless:
            new_node = cst.Subscript(
                cst.Attribute(cst.Name("typing"), cst.Name("Optional")),
                slice=(cst.SubscriptElement(cst.Index(non_none[0])),),
            )
        else:
            new_node = cst.Subscript(
                cst.Attribute(cst.Name("typing"), cst.Name("Union")),
                slice=tuple(
                    cst.SubscriptElement(cst.Index(m), comma=c)
                    for m, c in zip(members, commas + [cst.MaybeSentinel.DEFAULT])
                ),
            )
        new_node = new_node.with_changes(
            lpar=updated_node.lpar,
            rpar=updated_node.rpar,
        )
        self._emitted[id(new_node)] = (members, commas)
        return new_node


class TypingTransformer(cst.CSTTransformer):
    def __init__(self, scope):
        self._scope = scope
        self._require_typing = False
        self.import_manager = EnhancedImportManager()

    def _lower(self, expr: cst.BaseExpression) -> cst.BaseExpression:
        lowerer = _UnionLowerer()
        new_expr = expr.visit(lowerer)
        if lowerer.lowered:
            self._require_typing = True
            return new_expr
        return expr

    def leave_Annotation(
        self,
        node: cst.Annotation,
        updated_node: cst.Annotation,
    ) -> cst.Annotation:
        return updated_node.with_changes(
            annotation=self._lower(updated_node.annotation),
        )

    # PEP 695 type expressions which become runtime values once the
    # ``type`` statement / type parameters are lowered.
    def leave_TypeAlias(
        self,
        node: cst.TypeAlias,
        updated_node: cst.TypeAlias,
    ) -> cst.TypeAlias:
        return updated_node.with_changes(value=self._lower(updated_node.value))

    def leave_TypeVar(
        self,
        node: cst.TypeVar,
        updated_node: cst.TypeVar,
    ) -> cst.TypeVar:
        if updated_node.bound is None:
            return updated_node
        return updated_node.with_changes(bound=self._lower(updated_node.bound))

    def leave_Module(self, node: cst.Module, updated_node: cst.Module) -> cst.Module:
        if self._require_typing:
            # Scan existing imports and add typing import if needed
            self.import_manager.scan_imports(updated_node.body)
            new_body = list(updated_node.body)
            new_body = self.import_manager.ensure_direct_import(new_body, "typing")

            return dataclasses.replace(updated_node, body=tuple(new_body))
        return updated_node


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
    Convert the PEP 585 built-in generics such as `list[int]` to the
    `typing.List[int]` form, with the appropriate `typing` import included.
    """
    return pep585_builtins.convert(module)


def convert_walrus_operator(module: cst.Module) -> cst.Module:
    return module.visit(walrus.WalrusOperatorTransformer())


def convert_type_alias(module: cst.Module) -> cst.Module:
    return module.visit(type_alias.PEP695Transformer())


def convert_match_statement(module: cst.Module) -> cst.Module:
    return module.visit(match_statement.MatchStatementTransformer())


def convert_dataclass(module: cst.Module) -> cst.Module:
    return module.visit(dataclass.DataclassTransformer())


def convert_typing_extensions(module: cst.Module) -> cst.Module:
    return typing_extensions.convert(module)


def convert_collections_abc(module: cst.Module) -> cst.Module:
    return collections_abc.convert(module)


def convert_pep585_imports(module: cst.Module) -> cst.Module:
    return pep585_imports.convert(module)


def convert_lazy_imports(code: str) -> str:
    return lazy_imports.transform_lazy_imports(code)


def convert(code: str) -> str:
    # PEP 810 ``lazy`` syntax is not parseable by libcst, so the
    # tokenize-based rewrite must run on the raw source first.
    code = convert_lazy_imports(code)
    try:
        mod = cst.parse_module(code)
    except cst.ParserSyntaxError as exc:
        err = SyntaxError(getattr(exc, "message", None) or str(exc))
        err.lineno = getattr(exc, "raw_line", None)
        err.offset = getattr(exc, "raw_column", None)
        raise err from exc
    mod = convert_sequence_subscript(mod)
    mod = convert_walrus_operator(mod)
    # Must precede the PEP 695 lowering, which turns ``type`` statement
    # values and TypeVar bounds into plain runtime expressions.
    mod = convert_union(mod)
    mod = convert_type_alias(mod)
    mod = convert_dataclass(mod)
    mod = convert_typing_extensions(mod)
    mod = convert_collections_abc(mod)
    mod = convert_pep585_imports(mod)
    mod = convert_match_statement(mod)
    return mod.code
