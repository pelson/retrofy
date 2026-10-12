import builtins
import dataclasses

import libcst as cst
import libcst.matchers as m
from libcst.metadata import (
    BuiltinAssignment,
    QualifiedNameProvider,
    QualifiedNameSource,
    ScopeProvider,
)

from ._transformations import (
    collections_abc,
    dataclass,
    lazy_imports,
    match_statement,
    pep585_builtins,
    pep585_imports,
    pep617_with,
    pep758_except,
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


def _is_bitor(node: cst.BaseExpression) -> bool:
    return isinstance(node, cst.BinaryOperation) and isinstance(
        node.operator,
        cst.BitOr,
    )


def _flatten_bitor(
    node: cst.BaseExpression,
) -> tuple[list[cst.BaseExpression], list[cst.Comma]]:
    """The operands of a ``|`` chain and the commas which will replace each ``|``."""
    if not _is_bitor(node):
        return [node], []
    assert isinstance(node, cst.BinaryOperation)
    left_members, left_commas = (
        _flatten_bitor(node.left)
        if _parens_are_simple(node.left)
        else ([node.left], [])
    )
    right_members, right_commas = (
        _flatten_bitor(node.right)
        if _parens_are_simple(node.right)
        else ([node.right], [])
    )
    return (
        left_members + right_members,
        left_commas + [_comma_for(node.operator)] + right_commas,  # type: ignore[arg-type]
    )


# Names which, when they resolve to the built-in, are certainly classes.
_BUILTIN_CLASSES = frozenset(
    name
    for name, value in vars(builtins).items()
    if isinstance(value, type) and not name.startswith("_")
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
    METADATA_DEPENDENCIES = (QualifiedNameProvider, ScopeProvider)

    def __init__(self) -> None:
        super().__init__()
        self._require_typing = False
        self.import_manager = EnhancedImportManager()
        # Depth within annotations / PEP 695 type expressions, whose unions
        # are lowered as a whole on leaving.
        self._type_context = 0
        # The ``|``-bearing second arguments of isinstance / issubclass.
        self._isinstance_args: set[int] = set()
        # Runtime ``|`` chains whose operands are all certainly types.
        self._runtime_unions: set[int] = set()

    def _qualified_names(self, node: cst.CSTNode) -> set[str]:
        return {
            name.name
            for name in self.get_metadata(QualifiedNameProvider, node, set())
            if name.source != QualifiedNameSource.LOCAL
        }

    def _is_builtin(self, node: cst.BaseExpression, names: frozenset[str]) -> bool:
        return isinstance(node, cst.Name) and any(
            qn == f"builtins.{node.value}" and node.value in names
            for qn in self._qualified_names(node)
        )

    def _is_certainly_type(self, node: cst.BaseExpression) -> bool:
        """Whether ``node`` can only evaluate to a type (so ``|`` is a union)."""
        if _is_bitor(node):
            members, _ = _flatten_bitor(node)
            return all(self._is_certainly_type(m) for m in members)
        if isinstance(node, cst.Name) and node.value == "None":
            return True
        if isinstance(node, cst.Subscript):
            node = node.value
        if self._is_builtin(node, _BUILTIN_CLASSES):
            return True
        # ``typing.Any``, ``typing.List[int]`` (including from the PEP 585 pass).
        return isinstance(node, (cst.Name, cst.Attribute)) and any(
            qn.startswith("typing.") for qn in self._qualified_names(node)
        )

    def _none_type(self, node: cst.CSTNode) -> cst.BaseExpression:
        """``type(None)``, unless ``type`` is shadowed where ``node`` is."""
        scope = self.get_metadata(ScopeProvider, node, None)
        if scope is not None and all(
            isinstance(a, BuiltinAssignment) for a in scope["type"]
        ):
            return cst.Call(cst.Name("type"), [cst.Arg(cst.Name("None"))])
        return cst.Attribute(cst.Name("None"), cst.Name("__class__"))

    def _isinstance_form(
        self,
        expr: cst.BaseExpression,
        call: cst.Call,
    ) -> cst.BaseExpression:
        """``int | None`` -> ``(int, type(None))``, recursing into tuples."""
        if isinstance(expr, cst.Tuple):
            return expr.with_changes(
                elements=[
                    el.with_changes(value=self._isinstance_form(el.value, call))
                    for el in expr.elements
                ],
            )
        if not _is_bitor(expr):
            return expr
        members, commas = _flatten_bitor(expr)
        elements = []
        for member, comma in zip(members, commas + [cst.MaybeSentinel.DEFAULT]):
            if isinstance(member, cst.Name) and member.value == "None":
                member = self._none_type(call)
            else:
                member = self._isinstance_form(member, call)
            elements.append(cst.Element(member, comma=comma))
        if expr.lpar:
            return cst.Tuple(elements, lpar=expr.lpar, rpar=expr.rpar)
        return cst.Tuple(elements)

    def _lower(self, expr: cst.BaseExpression) -> cst.BaseExpression:
        lowerer = _UnionLowerer()
        new_expr = expr.visit(lowerer)
        if lowerer.lowered:
            self._require_typing = True
            return new_expr
        return expr

    def visit_Annotation(self, node: cst.Annotation) -> None:
        self._type_context += 1

    def visit_TypeAlias(self, node: cst.TypeAlias) -> None:
        self._type_context += 1

    def visit_TypeVar(self, node: cst.TypeVar) -> None:
        self._type_context += 1

    def leave_Annotation(
        self,
        node: cst.Annotation,
        updated_node: cst.Annotation,
    ) -> cst.Annotation:
        self._type_context -= 1
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
        self._type_context -= 1
        return updated_node.with_changes(value=self._lower(updated_node.value))

    def leave_TypeVar(
        self,
        node: cst.TypeVar,
        updated_node: cst.TypeVar,
    ) -> cst.TypeVar:
        self._type_context -= 1
        if updated_node.bound is None:
            return updated_node
        return updated_node.with_changes(bound=self._lower(updated_node.bound))

    # Runtime positions. In the second argument of isinstance / issubclass
    # a ``|`` can only be a union of types, and before 3.10 the equivalent
    # is a tuple (``typing.Union`` is rejected there too).
    def visit_Call(self, node: cst.Call) -> None:
        if (
            len(node.args) == 2
            and self._is_builtin(node.func, frozenset({"isinstance", "issubclass"}))
            and not node.args[1].keyword
            and node.args[1].star == ""
        ):
            self._isinstance_args.add(id(node.args[1]))

    def visit_Arg(self, node: cst.Arg) -> bool:
        # Handled as a whole by leave_Call.
        return id(node) not in self._isinstance_args

    def leave_Call(self, original_node: cst.Call, updated_node: cst.Call) -> cst.Call:
        if len(original_node.args) != 2:
            return updated_node
        if id(original_node.args[1]) not in self._isinstance_args:
            return updated_node
        arg = updated_node.args[1]
        new_value = self._isinstance_form(arg.value, original_node)
        if new_value is arg.value:
            return updated_node
        return updated_node.with_changes(
            args=[updated_node.args[0], arg.with_changes(value=new_value)],
        )

    # Elsewhere ``a | b`` may be a bitwise-or or set union, so it is only
    # lowered (to ``typing.Union``) when every operand is certainly a type.
    def visit_BinaryOperation(self, node: cst.BinaryOperation) -> bool:
        if self._type_context or not _is_bitor(node):
            return True
        if self._is_certainly_type(node):
            self._runtime_unions.add(id(node))
            return False
        return True

    def leave_BinaryOperation(
        self,
        original_node: cst.BinaryOperation,
        updated_node: cst.BinaryOperation,
    ) -> cst.BaseExpression:
        if id(original_node) in self._runtime_unions:
            return self._lower(updated_node)
        return updated_node

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

    # Scope analysis is comparatively expensive; skip it when there is no
    # ``|`` to lower.
    if not m.findall(module, m.BitOr()):
        return module
    return cst.MetadataWrapper(module).visit(TypingTransformer())


def convert_sequence_subscript(module: cst.Module) -> cst.Module:
    """
    Convert the PEP 585 built-in generics such as `list[int]` to the
    `typing.List[int]` form, with the appropriate `typing` import included.
    """
    return pep585_builtins.convert(module)


def convert_pep617_with(module: cst.Module) -> cst.Module:
    """Lower PEP 617 parenthesised context managers, keeping line numbers."""
    return pep617_with.convert(module)


def convert_pep758_except(module: cst.Module) -> cst.Module:
    """Lower PEP 758 ``except A, B:`` to ``except (A, B):``."""
    return pep758_except.convert(module)


def convert_walrus_operator(module: cst.Module) -> cst.Module:
    return module.visit(walrus.WalrusOperatorTransformer())


def convert_type_alias(module: cst.Module) -> cst.Module:
    return module.visit(type_alias.PEP695Transformer())


def convert_match_statement(module: cst.Module) -> cst.Module:
    # Scope analysis is comparatively expensive; skip it when there is no
    # ``match`` statement to lower.
    if not m.findall(module, m.Match()):
        return module
    return cst.MetadataWrapper(module).visit(
        match_statement.MatchStatementTransformer(),
    )


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
    mod = convert_pep617_with(mod)
    mod = convert_pep758_except(mod)
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
