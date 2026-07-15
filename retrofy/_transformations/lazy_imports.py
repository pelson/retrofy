"""PEP 810 (lazy imports) backport rewriter.

Phase 1 (tokenize): strip ``lazy`` from ``lazy import`` / ``lazy from``
statements and replace each with assignments calling helpers from the
converted package's sibling ``_retrofy_rt.lazy_imports`` module. libcst
does not yet parse the 3.15 ``lazy`` soft keyword (see #13), so this
phase has to run on the raw source. Once libcst grows ``lazy``
support upstream — or someone contributes it — Phase 1 collapses
into Phase 2 and the bespoke tokenize parser here can go away.

Phase 2 (libcst + ``ScopeProvider``): wrap every read of a lazy-bound
module global with ``__lazy_reify__(name)``, leaving locally-shadowed
references alone and skipping assignment LHS / ``for`` / ``with as``
binding targets.

Phase 3: inject the runtime helper import after the preamble (module
docstring + ``from __future__`` block), using libcst to find the
insertion point in the AST rather than poking at lines. The import
is a *relative* one — ``from ._retrofy_rt.lazy_imports import ...`` —
so the converted module never references ``retrofy`` at runtime. The
wheel-build hook drops a copy of the ``_retrofy_rt`` sub-package into
the converted package; the on-the-fly meta-path converter synthesises
it for editable / pytest contexts. Only the helpers a particular
module actually uses are imported.

Helper names are mangled with dunder underscores (``__lazy_import__``
etc.) and numbered (``__lazy_import_2__``) if a user's own source
already binds the un-suffixed form.
"""

from __future__ import annotations

from dataclasses import dataclass
import io
import re
import tokenize
import typing
import warnings

import libcst as cst
from libcst.metadata import GlobalScope, ScopeProvider

_RUNTIME_MODULE_RELATIVE_PACKAGE = "_retrofy_rt"
_RUNTIME_MODULE_NAME = "lazy_imports"

# Base helper names. ``_helper_names`` may append a ``_<n>`` suffix
# before the trailing ``__`` if the un-suffixed names collide with
# identifiers already present in the source being converted. Keys are
# the public function names in ``lazy_imports.py``; values are the
# default mangled aliases used in converted code.
_BASE_HELPERS = {
    "lazy_import": "__lazy_import__",
    "lazy_import_as": "__lazy_import_as__",
    "lazy_from": "__lazy_from__",
    "reify": "__lazy_reify__",
}


@dataclass
class _HelperNames:
    lazy_import: str
    lazy_import_as: str
    lazy_from: str
    reify: str
    # Placeholder name emitted in phase 1's ``if <name>.TYPE_CHECKING:``
    # blocks. The libcst pass in phase 3 rewrites this to ``typing`` if
    # ``typing`` is safely bound at module scope, or leaves it alone
    # and injects ``import typing as <name>`` if not. Making it a
    # mangled dunder makes it collision-safe and unambiguously ours.
    typing_module: str


def _helper_names(source: str) -> _HelperNames:
    """Pick non-colliding dunder helper names for *source*.

    All four ``_retrofy_rt`` helpers share the same numeric suffix so
    the emitted runtime-import line stays readable — bumping one bumps
    all. The ``__lazy_typing__`` placeholder is orthogonal (it may or
    may not become an alias in phase 3), so its suffix is chosen
    independently.

    "Appears" is checked against a coarse word-boundary regex — we
    deliberately do **not** parse the source for real identifier
    usages. A name showing up only in a comment, docstring, or
    f-string literal is still enough to push us to the next suffix.
    Costs us nothing (helper names are mechanical, the suffix is
    invisible) and avoids any risk of clashing with names a future
    reader of the source might assume are "obviously safe".
    """
    name_re = re.compile(r"\b[A-Za-z_]\w*\b")
    existing = set(name_re.findall(source))

    def _name(base: str, suffix: str) -> str:
        # base is like ``__lazy_import__``; suffix is either ``""`` or
        # ``"_2"`` etc. Insert the suffix before the trailing ``__``.
        assert base.endswith("__")
        return base[:-2] + suffix + "__"

    def _pick_suffix(bases: list[str]) -> str:
        n = 1
        while True:
            suffix = "" if n == 1 else f"_{n}"
            if not (set(_name(b, suffix) for b in bases) & existing):
                return suffix
            n += 1

    helper_suffix = _pick_suffix(list(_BASE_HELPERS.values()))
    typing_suffix = _pick_suffix(["__lazy_typing__"])
    return _HelperNames(
        **{role: _name(base, helper_suffix) for role, base in _BASE_HELPERS.items()},
        typing_module=_name("__lazy_typing__", typing_suffix),
    )


class LazyImportSyntaxError(SyntaxError):
    """Raised for misuse of the ``lazy`` soft keyword."""


class LazyModulesIgnoredWarning(UserWarning):
    """Emitted when a module declares ``__lazy_modules__`` — the
    declarative form of PEP 810 that retrofy does not backport.

    Retrofy supports the explicit ``lazy import`` / ``lazy from``
    keyword form only; ``__lazy_modules__`` is left in the converted
    source as an inert variable assignment (which is exactly what older
    Python interpreters see anyway). Users who want laziness should
    rewrite the relevant ``import`` statements to use the ``lazy``
    keyword — otherwise the declaration is dead weight and you don't
    get the lazy-import capability that retrofy backports.
    """


def _find_lazy_modules_declaration(
    tokens: list[tokenize.TokenInfo],
) -> int | None:
    """Return the 1-based line number of a top-level
    ``__lazy_modules__ = ...`` assignment, or ``None`` if none.

    Only module-scope assignments to the bare name are recognised —
    attribute writes (``mod.__lazy_modules__ = ...``) and writes inside
    function / class bodies do not trigger the warning.
    """
    indent_depth = 0
    for i, tok in enumerate(tokens):
        if tok.type == tokenize.INDENT:
            indent_depth += 1
            continue
        if tok.type == tokenize.DEDENT:
            indent_depth -= 1
            continue
        if indent_depth != 0:
            continue
        if (
            tok.type == tokenize.NAME
            and tok.string == "__lazy_modules__"
            and _is_statement_start(tokens, i)
        ):
            j = i + 1
            while j < len(tokens) and tokens[j].type in (
                tokenize.NL,
                tokenize.COMMENT,
            ):
                j += 1
            if (
                j < len(tokens)
                and tokens[j].type == tokenize.OP
                and tokens[j].string == "="
            ):
                return tok.start[0]
    return None


@dataclass
class _LazyStmt:
    bindings: list[str]
    replacement: str
    # Roles in ``_BASE_HELPERS`` that this statement's emitted code
    # references. Used so the final ``from ._retrofy_rt.lazy_imports
    # import ...`` only pulls in helpers that are actually called.
    used_helpers: set[str]


# ---------------------------------------------------------------------------
# Phase 1: tokenize-level syntax rewrite
# ---------------------------------------------------------------------------


def _split_top_level_commas(s: str) -> list[str]:
    out: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    return out


def _format_type_checking_block(
    helpers: _HelperNames,
    tc_lines: list[str],
    runtime_lines: list[str],
) -> str:
    """Emit the ``if <placeholder>.TYPE_CHECKING: <real imports>\\n
    else: <runtime bindings>`` block that type checkers need to
    resolve lazy-bound names to their real types while keeping
    runtime laziness.

    The runtime branch is emitted in an ``else`` so type checkers
    never see the lazy-helper assignment poison the name's type — see
    issue #45.

    The header uses a mangled dunder placeholder (not ``typing``)
    because the source hasn't been parsed into a CST yet — we can't
    tell here whether the user already imports ``typing`` or shadows
    it. Phase 3 (libcst) inspects the module scope and either rewrites
    the placeholder back to ``typing`` (safe case) or leaves it as-is
    and injects ``import typing as <placeholder>`` (shadowed case).
    """
    tc_body = "\n".join(f"    {line}" for line in tc_lines)
    else_body = "\n".join(f"    {line}" for line in runtime_lines)
    return f"if {helpers.typing_module}.TYPE_CHECKING:\n{tc_body}\nelse:\n{else_body}"


def _format_lazy_import(stmt_src: str, helpers: _HelperNames) -> _LazyStmt:
    body = stmt_src.strip()
    assert body.startswith("import"), body
    body = body[len("import") :].strip()

    bindings: list[str] = []
    tc_lines: list[str] = []
    runtime_lines: list[str] = []
    used: set[str] = set()
    for clause in _split_top_level_commas(body):
        clause = clause.strip()
        if " as " in clause:
            name, _, alias = clause.partition(" as ")
            name = name.strip()
            alias = alias.strip()
            bindings.append(alias)
            tc_lines.append(f"import {name} as {alias}")
            runtime_lines.append(
                f"{alias} = {helpers.lazy_import_as}({name!r}, {alias!r})",
            )
            used.add("lazy_import_as")
        else:
            # ``import foo.bar`` binds ``foo``
            name = clause.strip()
            top = name.partition(".")[0]
            bindings.append(top)
            tc_lines.append(f"import {name}")
            runtime_lines.append(
                f"{top} = {helpers.lazy_import}({name!r}, {top!r})",
            )
            used.add("lazy_import")
    return _LazyStmt(
        bindings=bindings,
        replacement=_format_type_checking_block(helpers, tc_lines, runtime_lines),
        used_helpers=used,
    )


def _format_lazy_from(stmt_src: str, helpers: _HelperNames) -> _LazyStmt:
    body = stmt_src.strip()
    assert body.startswith("from"), body
    body = body[len("from") :].strip()

    if " import " not in body:
        raise LazyImportSyntaxError(
            f"malformed lazy from-import: {stmt_src!r}",
        )
    module_part, _, names_part = body.partition(" import ")
    module = module_part.strip()
    names_part = names_part.strip().strip("()").strip()

    if names_part == "*":
        raise LazyImportSyntaxError("lazy from-import does not support '*'")

    bindings: list[str] = []
    tc_names: list[str] = []
    runtime_lines: list[str] = []
    for clause in _split_top_level_commas(names_part):
        clause = clause.strip()
        if not clause:
            continue
        if " as " in clause:
            attr, _, alias = clause.partition(" as ")
            attr = attr.strip()
            alias = alias.strip()
            tc_names.append(f"{attr} as {alias}")
        else:
            attr = clause
            alias = clause
            tc_names.append(attr)
        bindings.append(alias)
        if module.startswith("."):
            # Relative ``lazy from ... import`` needs the calling
            # module's ``__package__`` so ``importlib.import_module``
            # can resolve the relative target.
            args = f"{module!r}, {attr!r}, {alias!r}, package=__package__"
        else:
            args = f"{module!r}, {attr!r}, {alias!r}"
        runtime_lines.append(f"{alias} = {helpers.lazy_from}({args})")
    if not bindings:
        return _LazyStmt(bindings=[], replacement="", used_helpers=set())
    tc_lines = [f"from {module} import {', '.join(tc_names)}"]
    return _LazyStmt(
        bindings=bindings,
        replacement=_format_type_checking_block(helpers, tc_lines, runtime_lines),
        used_helpers={"lazy_from"},
    )


def _is_statement_start(tokens: list[tokenize.TokenInfo], i: int) -> bool:
    for j in range(i - 1, -1, -1):
        t = tokens[j].type
        if t in (
            tokenize.NEWLINE,
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.ENCODING,
        ):
            return True
        # ``;`` separates simple statements on a single logical line.
        if t == tokenize.OP and tokens[j].string == ";":
            return True
        if t in (tokenize.NL, tokenize.COMMENT):
            continue
        return False
    return True


def _reconstruct_token_span(toks: list[tokenize.TokenInfo]) -> str:
    if not toks:
        return ""
    pieces: list[str] = []
    prev_end: tuple[int, int] | None = None
    for t in toks:
        if t.type in (tokenize.NL, tokenize.COMMENT):
            continue
        if prev_end is not None:
            if t.start[0] != prev_end[0]:
                pieces.append(" ")
            else:
                gap = t.start[1] - prev_end[1]
                if gap > 0:
                    pieces.append(" " * gap)
        pieces.append(t.string)
        prev_end = t.end
    return "".join(pieces)


def _apply_edits(
    source: str,
    edits: list[tuple[tuple[int, int], tuple[int, int], str]],
) -> str:
    lines = source.splitlines(keepends=True)
    edits = sorted(edits, key=lambda e: (e[0][0], e[0][1]), reverse=True)
    for (sr, sc), (er, ec), text in edits:
        sr_idx = sr - 1
        er_idx = er - 1
        if sr_idx == er_idx:
            line = lines[sr_idx]
            lines[sr_idx] = line[:sc] + text + line[ec:]
        else:
            start_line = lines[sr_idx]
            end_line = lines[er_idx]
            merged = start_line[:sc] + text + end_line[ec:]
            lines[sr_idx : er_idx + 1] = [merged]
    return "".join(lines)


def _strip_lazy_syntax(
    source: str,
    helpers: _HelperNames,
) -> tuple[str, list[str], set[str]]:
    readline = io.StringIO(source).readline
    tokens = list(tokenize.generate_tokens(readline))

    indent_depth = 0
    edits: list[tuple[tuple[int, int], tuple[int, int], str]] = []
    # `;` positions already handled by a semicolon-split edit, so we
    # don't emit two edits for the same `;` when it sits between two
    # adjacent `lazy` statements.
    handled_semis: set[tuple[int, int]] = set()
    lazy_names: list[str] = []
    used_helpers: set[str] = set()

    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok.type == tokenize.INDENT:
            indent_depth += 1
            i += 1
            continue
        if tok.type == tokenize.DEDENT:
            indent_depth -= 1
            i += 1
            continue

        if (
            tok.type == tokenize.NAME
            and tok.string == "lazy"
            and _is_statement_start(tokens, i)
        ):
            j = i + 1
            while j < n and tokens[j].type in (tokenize.NL, tokenize.COMMENT):
                j += 1
            if (
                j < n
                and tokens[j].type == tokenize.NAME
                and tokens[j].string in ("import", "from")
            ):
                if indent_depth != 0:
                    lineno = tok.start[0]
                    msg = f"`lazy` is only permitted at module level (line {lineno})"
                    raise LazyImportSyntaxError(msg)

                k = j
                while k < n:
                    t = tokens[k]
                    if t.type == tokenize.NEWLINE:
                        break
                    if t.type == tokenize.OP and t.string == ";":
                        break
                    k += 1
                stmt_tokens = tokens[j:k]
                stmt_src = _reconstruct_token_span(stmt_tokens)

                if stmt_tokens[0].string == "import":
                    rewritten = _format_lazy_import(stmt_src, helpers)
                else:
                    rewritten = _format_lazy_from(stmt_src, helpers)

                lazy_names.extend(rewritten.bindings)
                used_helpers |= rewritten.used_helpers
                edits.append(
                    (tok.start, stmt_tokens[-1].end, rewritten.replacement),
                )

                # The new emit is a multi-line ``if TYPE_CHECKING: ...
                # else: ...`` block. If the ``lazy`` statement abuts a
                # simple-statement chain via ``;`` on the same physical
                # line, either side of it would splice the block into
                # invalid Python (the block's last emitted line would
                # continue into the else-branch of the compound stmt).
                # Break those semicolons into newlines instead.
                _add_semicolon_split_edits(
                    tokens,
                    i,
                    k,
                    edits,
                    handled_semis,
                )
                i = k
                continue
        i += 1

    if not edits:
        return source, [], set()
    return _apply_edits(source, edits), lazy_names, used_helpers


def _add_semicolon_split_edits(
    tokens: list[tokenize.TokenInfo],
    i: int,
    k: int,
    edits: list[tuple[tuple[int, int], tuple[int, int], str]],
    handled: set[tuple[int, int]],
) -> None:
    """Emit ``; <ws>`` → ``\\n`` edits for any semicolon adjacent to the
    ``lazy`` statement at token index *i* (whose last body token index
    is *k*). *handled* dedupes semicolons touched from both sides when
    two adjacent ``lazy`` statements share a separator.
    """
    lazy_tok = tokens[i]

    # Preceding semicolon: ``X; lazy Y`` — walk back through NL/COMMENT
    # to find the previous meaningful token.
    for jj in range(i - 1, -1, -1):
        prev = tokens[jj]
        if prev.type in (tokenize.NL, tokenize.COMMENT):
            continue
        if (
            prev.type == tokenize.OP
            and prev.string == ";"
            and prev.start[0] == lazy_tok.start[0]
            and prev.start not in handled
        ):
            edits.append((prev.start, lazy_tok.start, "\n"))
            handled.add(prev.start)
        break

    # Trailing semicolon: ``lazy X; Y`` — look at the token that
    # terminated the statement scan.
    n = len(tokens)
    if k < n:
        trailing = tokens[k]
        if (
            trailing.type == tokenize.OP
            and trailing.string == ";"
            and trailing.start not in handled
        ):
            m = k + 1
            while m < n and tokens[m].type in (tokenize.NL, tokenize.COMMENT):
                m += 1
            if m < n and tokens[m].start[0] == trailing.start[0]:
                edits.append((trailing.start, tokens[m].start, "\n"))
                handled.add(trailing.start)


# ---------------------------------------------------------------------------
# Phase 2: libcst Name-read wrapping
# ---------------------------------------------------------------------------


class _ReifyWrappingTransformer(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (ScopeProvider,)

    def __init__(
        self,
        lazy_names: set[str],
        reify_name: str,
    ) -> None:
        super().__init__()
        self._lazy_names = lazy_names
        self._reify_name = reify_name
        self._targets: set[int] = set()

    def visit_Module(self, node: cst.Module) -> None:
        write_ids: set[int] = set()
        self._collect_write_names(node, write_ids)

        for nd in self._iter_name_nodes(node):
            if nd.value not in self._lazy_names:
                continue
            if id(nd) in write_ids:
                continue
            try:
                scope = self.get_metadata(ScopeProvider, nd)
            except KeyError:
                continue
            if scope is None:
                continue
            assignments = scope[nd.value]
            if not assignments:
                continue
            if not all(isinstance(a.scope, GlobalScope) for a in assignments):
                continue
            self._targets.add(id(nd))

    def _collect_write_names(
        self,
        node: cst.CSTNode,
        out: set[int],
    ) -> None:
        if isinstance(node, cst.AssignTarget):
            self._collect_target_names(node.target, out)
        elif isinstance(node, cst.AnnAssign):
            self._collect_target_names(node.target, out)
        elif isinstance(node, cst.AugAssign):
            self._collect_target_names(node.target, out)
        elif isinstance(node, cst.For):
            self._collect_target_names(node.target, out)
        elif isinstance(node, cst.NamedExpr):
            self._collect_target_names(node.target, out)
        elif isinstance(node, cst.Del):
            self._collect_target_names(node.target, out)
        elif isinstance(node, cst.AsName):
            self._collect_target_names(node.name, out)
        for child in node.children:
            self._collect_write_names(child, out)

    def _collect_target_names(
        self,
        target: cst.CSTNode,
        out: set[int],
    ) -> None:
        if isinstance(target, cst.Name):
            out.add(id(target))
        elif isinstance(target, (cst.Tuple, cst.List)):
            for el in target.elements:
                self._collect_target_names(el.value, out)
        elif isinstance(target, cst.StarredElement):
            self._collect_target_names(target.value, out)

    def _iter_name_nodes(self, node: cst.CSTNode):
        stack: list[cst.CSTNode] = [node]
        while stack:
            current = stack.pop()
            if isinstance(current, cst.Name):
                yield current
            for child in current.children:
                stack.append(child)

    def leave_Name(
        self,
        original_node: cst.Name,
        updated_node: cst.Name,
    ) -> cst.BaseExpression:
        if id(original_node) not in self._targets:
            return updated_node
        return cst.Call(
            func=cst.Name(self._reify_name),
            args=[cst.Arg(value=updated_node)],
        )


def _wrap_lazy_reads(
    source: str,
    lazy_names: list[str],
    helpers: _HelperNames,
) -> tuple[str, bool]:
    """Wrap every read of a lazy-bound name with ``helpers.reify(name)``.

    Returns the rewritten source plus a flag — ``True`` iff at least
    one read was actually wrapped. The flag lets Phase 3 skip the
    ``reify`` import when the lazy bindings are declared but never
    read (uncommon but possible).

    Wraps include reads inside ``cst.Annotation`` slots — those reads
    force reification at eval time so that ``__annotations__`` /
    ``typing.get_type_hints`` see the real class rather than the
    ``LazyProxy``, matching native PEP 810 semantics. Mypy would
    reject the wrapped call as ``[valid-type]``, so phase 2b (below)
    duplicates each annotation-carrying construct under a
    ``if TYPE_CHECKING: <clean stub> / else: <wrapped>`` pair, giving
    static checkers a clean signature to type-check against.
    """
    if not lazy_names:
        return source, False
    module = cst.parse_module(source)
    wrapper = cst.metadata.MetadataWrapper(module)
    transformer = _ReifyWrappingTransformer(set(lazy_names), helpers.reify)
    rewritten = wrapper.visit(transformer).code
    return rewritten, bool(transformer._targets)


# ---------------------------------------------------------------------------
# Phase 2b: TYPE_CHECKING duplication of annotation-carrying constructs
# ---------------------------------------------------------------------------


class _ReifyStripper(cst.CSTTransformer):
    """Replace ``<reify_name>(X)`` calls with the bare ``X`` argument.

    Used to build the ``if TYPE_CHECKING:`` clean-stub form of a
    construct — the stub carries the original names (which type
    checkers resolve via the top-level TYPE_CHECKING re-import)
    instead of the runtime ``__lazy_reify__(...)`` call.
    """

    def __init__(self, reify_name: str) -> None:
        super().__init__()
        self._reify_name = reify_name

    def leave_Call(
        self,
        original_node: cst.Call,
        updated_node: cst.Call,
    ) -> cst.BaseExpression:
        if (
            isinstance(updated_node.func, cst.Name)
            and updated_node.func.value == self._reify_name
            and len(updated_node.args) == 1
        ):
            return updated_node.args[0].value
        return updated_node


def _node_contains_reify_call(node: cst.CSTNode, reify_name: str) -> bool:
    """Return True if *node* contains any ``<reify_name>(X)`` call."""
    if (
        isinstance(node, cst.Call)
        and isinstance(node.func, cst.Name)
        and node.func.value == reify_name
    ):
        return True
    for child in node.children:
        if _node_contains_reify_call(child, reify_name):
            return True
    return False


def _def_has_wrapped_annotation(
    def_node: cst.FunctionDef,
    reify_name: str,
) -> bool:
    """Return True if any of *def_node*'s annotations contains a
    ``<reify_name>(X)`` call.

    Only annotations are inspected — body-level wraps are not a
    reason to duplicate the def under ``TYPE_CHECKING`` (type
    checkers accept ``__lazy_reify__(X)`` at value positions via the
    generic ``T->T`` signature; only annotation slots trip
    ``[valid-type]``).
    """
    params = def_node.params
    for p in (
        list(params.params) + list(params.kwonly_params) + list(params.posonly_params)
    ):
        if p.annotation is not None and _node_contains_reify_call(
            p.annotation,
            reify_name,
        ):
            return True
    for star in (params.star_arg, params.star_kwarg):
        if (
            isinstance(star, cst.Param)
            and star.annotation is not None
            and _node_contains_reify_call(star.annotation, reify_name)
        ):
            return True
    if def_node.returns is not None and _node_contains_reify_call(
        def_node.returns,
        reify_name,
    ):
        return True
    return False


def _annassign_has_wrap(
    annassign: cst.AnnAssign,
    reify_name: str,
) -> bool:
    """Return True if the annotation (or, for TypeAlias-style
    declarations, the value) of *annassign* contains a
    ``<reify_name>(X)`` call.
    """
    if _node_contains_reify_call(annassign.annotation, reify_name):
        return True
    if annassign.value is not None and _node_contains_reify_call(
        annassign.value,
        reify_name,
    ):
        return True
    return False


_TYPE_CHECKING_MIRROR_COMMENT_LINES = (
    "# retrofy: type-checking mirror of the def below; body is",
    "# duplicated so type checkers see attribute assignments etc.",
)


def _stub_from_def(
    def_node: cst.FunctionDef,
    reify_name: str,
) -> cst.FunctionDef:
    """Build the ``if TYPE_CHECKING:`` mirror of *def_node*.

    Strip reify calls throughout (so annotations are clean for type
    checkers), and keep the original body intact — a bare ``...``
    stub would hide attribute assignments (``self._x = x`` inside
    ``__init__``, etc.) and downstream ``[attr-defined]`` errors
    would follow. See retrofy#54. A leading comment inside the
    ``TYPE_CHECKING`` branch flags the duplication so a reader
    doesn't wonder why the def appears twice.
    """
    stripped = def_node.visit(_ReifyStripper(reify_name))
    assert isinstance(stripped, cst.FunctionDef)
    comment_lines = tuple(
        cst.EmptyLine(comment=cst.Comment(text))
        for text in _TYPE_CHECKING_MIRROR_COMMENT_LINES
    )
    return stripped.with_changes(
        leading_lines=comment_lines + tuple(stripped.leading_lines),
    )


def _clean_annassign_line(
    line: cst.SimpleStatementLine,
    reify_name: str,
) -> cst.SimpleStatementLine:
    """Return *line* with any ``<reify_name>(X)`` calls stripped."""
    stripped = line.visit(_ReifyStripper(reify_name))
    assert isinstance(stripped, cst.SimpleStatementLine)
    return stripped


def _build_type_checking_pair(
    tc_expr: cst.BaseExpression,
    stub: cst.BaseCompoundStatement | cst.SimpleStatementLine,
    real: cst.BaseCompoundStatement | cst.SimpleStatementLine,
    leading_lines: typing.Sequence[cst.EmptyLine] = (),
) -> cst.If:
    """Build ``if <tc_expr>: <stub>\\nelse: <real>``, carrying any
    *leading_lines* to sit before the ``if`` (so blank lines that
    preceded the original construct don't get duplicated into both
    branches).
    """
    return cst.If(
        test=tc_expr,
        body=cst.IndentedBlock(body=[stub]),
        orelse=cst.Else(body=cst.IndentedBlock(body=[real])),
        leading_lines=leading_lines,
    )


def _apply_type_checking_duplication(
    source: str,
    helpers: _HelperNames,
) -> str:
    """Reparse *source* and apply :func:`_duplicate_annotated_constructs`.

    Convenience wrapper for :func:`transform_lazy_imports` — phase 2b
    is a CST-level pass, but the surrounding phases speak source
    strings, so we materialise the CST here.
    """
    module = cst.parse_module(source)
    module = _duplicate_annotated_constructs(module, helpers)
    return module.code


def _duplicate_annotated_constructs(
    module: cst.Module,
    helpers: _HelperNames,
) -> cst.Module:
    """Phase 2b: walk module and class bodies, replacing each
    annotation-carrying construct whose annotation contains a
    ``<reify>`` wrap with an ``if <tc>.TYPE_CHECKING: <clean stub>``
    / ``else: <wrapped>`` pair. This gives type checkers a clean
    signature to read from the ``if`` branch while runtime executes
    the ``else``
    branch's wrapped form (which reifies proxies at eval time so
    ``typing.get_type_hints`` / ``inspect.signature`` see the real
    class rather than a ``LazyProxy``).
    """
    reify_name = helpers.reify
    tc_expr = cst.Attribute(
        value=cst.Name(helpers.typing_module),
        attr=cst.Name("TYPE_CHECKING"),
    )

    def _process_stmt(stmt):
        if isinstance(stmt, cst.FunctionDef):
            # Recurse into the body first: in-function ``AnnAssign``
            # statements (``x: Foo`` without a value) also need the
            # TYPE_CHECKING/else pair even when the enclosing def's
            # signature has no wrapped annotations.
            new_body = _process_block(stmt.body)
            stmt = stmt.with_changes(body=new_body)
            if _def_has_wrapped_annotation(stmt, reify_name):
                # Take the def's leading blank lines and put them on
                # the new ``if`` — otherwise they'd sit inside both
                # branches as visual noise.
                leading = stmt.leading_lines
                stub = _stub_from_def(
                    stmt.with_changes(leading_lines=()),
                    reify_name,
                )
                real = stmt.with_changes(leading_lines=())
                return _build_type_checking_pair(
                    tc_expr,
                    stub,
                    real,
                    leading_lines=leading,
                )
            return stmt
        if isinstance(stmt, cst.SimpleStatementLine):
            if len(stmt.body) == 1 and isinstance(stmt.body[0], cst.AnnAssign):
                inner = stmt.body[0]
                if _annassign_has_wrap(inner, reify_name):
                    leading = stmt.leading_lines
                    stmt_no_lead = stmt.with_changes(leading_lines=())
                    clean = _clean_annassign_line(stmt_no_lead, reify_name)
                    return _build_type_checking_pair(
                        tc_expr,
                        clean,
                        stmt_no_lead,
                        leading_lines=leading,
                    )
            return stmt
        if isinstance(stmt, cst.ClassDef):
            new_body = _process_block(stmt.body)
            return stmt.with_changes(body=new_body)
        if isinstance(stmt, (cst.If, cst.For, cst.While, cst.With, cst.Try)):
            # Compound statements that carry an ``IndentedBlock`` body
            # can also nest annotation-carrying constructs. Recurse.
            return _recurse_compound(stmt)
        return stmt

    def _process_block(block):
        if isinstance(block, cst.IndentedBlock):
            new_stmts = tuple(_process_stmt(s) for s in block.body)
            return block.with_changes(body=new_stmts)
        return block

    def _recurse_compound(stmt):
        # Walk every body-carrying attribute on the compound stmt so
        # nested constructs (AnnAssigns inside try/except/finally,
        # etc.) go through phase 2b too.
        changes = {}
        for attr in ("body", "orelse", "finalbody"):
            if not hasattr(stmt, attr):
                continue
            child = getattr(stmt, attr)
            if isinstance(child, cst.IndentedBlock):
                changes[attr] = _process_block(child)
            elif isinstance(child, (cst.Else, cst.Finally)) and isinstance(
                child.body,
                cst.IndentedBlock,
            ):
                changes[attr] = child.with_changes(
                    body=_process_block(child.body),
                )
        # ``Try`` also carries per-handler bodies in ``handlers``.
        if isinstance(stmt, cst.Try) and stmt.handlers:
            new_handlers = tuple(
                h.with_changes(body=_process_block(h.body))
                if isinstance(h.body, cst.IndentedBlock)
                else h
                for h in stmt.handlers
            )
            if any(nh is not oh for nh, oh in zip(new_handlers, stmt.handlers)):
                changes["handlers"] = new_handlers
        if not changes:
            return stmt
        return stmt.with_changes(**changes)

    new_body = tuple(_process_stmt(s) for s in module.body)
    return module.with_changes(body=new_body)


# ---------------------------------------------------------------------------
# Phase 3: runtime-import injection
# ---------------------------------------------------------------------------


def _is_module_docstring(stmt: cst.BaseStatement) -> bool:
    if not isinstance(stmt, cst.SimpleStatementLine):
        return False
    if len(stmt.body) != 1:
        return False
    expr = stmt.body[0]
    return isinstance(expr, cst.Expr) and isinstance(
        expr.value,
        (cst.SimpleString, cst.ConcatenatedString),
    )


def _is_future_import(stmt: cst.BaseStatement) -> bool:
    if not isinstance(stmt, cst.SimpleStatementLine):
        return False
    if len(stmt.body) != 1:
        return False
    inner = stmt.body[0]
    if not isinstance(inner, cst.ImportFrom):
        return False
    module = inner.module
    return isinstance(module, cst.Name) and module.value == "__future__"


def _build_runtime_import(
    helpers: _HelperNames,
    used: set[str],
) -> cst.SimpleStatementLine:
    """Build ``from ._retrofy_rt.lazy_imports import (<helpers>)`` as a
    libcst node, importing only the helpers that *used* names."""
    # Stable order: matches ``_BASE_HELPERS`` declaration order so the
    # generated source diff stays predictable.
    aliases = [
        cst.ImportAlias(
            name=cst.Name(role),
            asname=cst.AsName(name=cst.Name(getattr(helpers, role))),
        )
        for role in _BASE_HELPERS
        if role in used
    ]
    return cst.SimpleStatementLine(
        body=[
            cst.ImportFrom(
                relative=[cst.Dot()],
                module=cst.Attribute(
                    value=cst.Name(_RUNTIME_MODULE_RELATIVE_PACKAGE),
                    attr=cst.Name(_RUNTIME_MODULE_NAME),
                ),
                names=aliases,
            ),
        ],
    )


def _build_typing_import(alias: str | None) -> cst.SimpleStatementLine:
    """Build ``import typing`` (or ``import typing as <alias>``) as a
    libcst node.

    Every rewritten ``lazy`` statement expands to an
    ``if <name>.TYPE_CHECKING: ... else: ...`` block, so the ``typing``
    module has to be resolvable at module scope under whichever name
    phase 3 settled on.
    """
    asname = cst.AsName(name=cst.Name(alias)) if alias is not None else None
    return cst.SimpleStatementLine(
        body=[
            cst.Import(
                names=[
                    cst.ImportAlias(name=cst.Name("typing"), asname=asname),
                ],
            ),
        ],
    )


def _typing_is_shadowed(module: cst.Module) -> bool:
    """Return True iff ``typing`` at module scope is bound to
    something *other* than the stdlib module.

    A ``typing`` binding is fine when every assignment to it comes
    from an unaliased ``import typing`` (any number of those is OK —
    they all bind to the same stdlib module). Anything else — an
    aliased import (``import typing as t``), a ``from`` import
    (``from x import typing``), a plain assignment (``typing = 5``),
    a ``for typing in ...`` at module scope, etc. — means the name
    ``typing`` is not reliably the stdlib module at the emit site,
    and we have to fall back to a mangled alias.
    """
    wrapper = cst.metadata.MetadataWrapper(module)
    scopes = wrapper.resolve(cst.metadata.ScopeProvider)
    global_scope: cst.metadata.GlobalScope | None = None
    for scope in scopes.values():
        if isinstance(scope, cst.metadata.GlobalScope):
            global_scope = scope
            break
    if global_scope is None:
        return False

    for assignment in global_scope["typing"]:
        if not isinstance(assignment, cst.metadata.ImportAssignment):
            return True
        # ``ImportAssignment.node`` is the outer ``Import`` /
        # ``ImportFrom`` statement — walk its aliases to check if one
        # binds the bare name ``typing`` unaliased.
        node = assignment.node
        if not isinstance(node, cst.Import):
            return True
        if not any(
            isinstance(a.name, cst.Name)
            and a.name.value == "typing"
            and a.asname is None
            for a in node.names
        ):
            return True
    return False


def _module_has_import_typing(module: cst.Module) -> bool:
    """Return True if the module has a top-level, unaliased
    ``import typing`` — so we don't need to inject a duplicate.
    """
    for stmt in module.body:
        if not isinstance(stmt, cst.SimpleStatementLine):
            continue
        for inner in stmt.body:
            if not isinstance(inner, cst.Import):
                continue
            for alias in inner.names:
                if (
                    isinstance(alias.name, cst.Name)
                    and alias.name.value == "typing"
                    and alias.asname is None
                ):
                    return True
    return False


class _RewritePlaceholderToTyping(cst.CSTTransformer):
    """Rewrite ``<placeholder>.TYPE_CHECKING`` back to
    ``typing.TYPE_CHECKING`` throughout the module.

    Used when phase 3 has decided ``typing`` at module scope is
    reliably the stdlib module and no injected alias is needed.
    """

    def __init__(self, placeholder: str) -> None:
        super().__init__()
        self._placeholder = placeholder

    def leave_Attribute(
        self,
        original_node: cst.Attribute,
        updated_node: cst.Attribute,
    ) -> cst.BaseExpression:
        if (
            isinstance(updated_node.value, cst.Name)
            and updated_node.value.value == self._placeholder
            and isinstance(updated_node.attr, cst.Name)
            and updated_node.attr.value == "TYPE_CHECKING"
        ):
            return updated_node.with_changes(value=cst.Name("typing"))
        return updated_node


def _inject_runtime_import(
    source: str,
    helpers: _HelperNames,
    used: set[str],
) -> str:
    """Insert the runtime-import statements after the module's preamble.

    The preamble is the optional docstring plus any
    ``from __future__ import ...`` statements (which must remain at
    the top of the module to be effective). Everything else stays put.
    Any blank line that originally separated the preamble from the
    body is transferred to lead the first injected import, so the
    docstring-blank-import-body shape is preserved.

    Injects up to two imports when any ``lazy`` statement was rewritten:

    * ``from ._retrofy_rt.lazy_imports import ...`` — the runtime
      helpers referenced from the ``else`` branch of each block.
    * ``import typing as <placeholder>`` — needed so each block's
      ``if <placeholder>.TYPE_CHECKING:`` header resolves. Skipped
      (and the placeholder rewritten to plain ``typing``) when
      ``typing`` at module scope is exclusively bound to the stdlib
      module.
    """
    if not used:
        return source

    module = cst.parse_module(source)

    # Decide whether emitted blocks reference plain ``typing`` or the
    # mangled ``__lazy_typing__`` alias. Plain works whenever
    # ``typing`` at module scope isn't bound to something other than
    # the stdlib module; the mangled alias is only needed when the
    # user's source actively shadows the name.
    #
    # ``ImportManager`` in ``import_utils`` is the closest existing
    # helper but doesn't dedupe or handle aliases — the deeper
    # refactor is tracked at #51. For now, ``typing`` gets injected
    # in the plain case only if not already present, and the aliased
    # case emits its own ``import typing as <mangled>``.
    if _typing_is_shadowed(module):
        typing_import: cst.SimpleStatementLine | None = _build_typing_import(
            helpers.typing_module,
        )
    else:
        module = cst.ensure_type(
            module.visit(_RewritePlaceholderToTyping(helpers.typing_module)),
            cst.Module,
        )
        typing_import = (
            None if _module_has_import_typing(module) else _build_typing_import(None)
        )

    body = list(module.body)
    insert_at = 0
    if body and _is_module_docstring(body[0]):
        insert_at = 1
    while insert_at < len(body) and _is_future_import(body[insert_at]):
        insert_at += 1

    injected: list[cst.SimpleStatementLine] = []
    if typing_import is not None:
        injected.append(typing_import)
    injected.append(_build_runtime_import(helpers, used))

    if insert_at < len(body):
        # Transfer the blank lines that originally separated the
        # preamble from the first body statement onto the first
        # injected import, so the docstring → blank → import shape is
        # kept.
        following = body[insert_at]
        leading = getattr(following, "leading_lines", ())
        if leading:
            injected[0] = injected[0].with_changes(leading_lines=leading)
            body[insert_at] = following.with_changes(leading_lines=())
    body[insert_at:insert_at] = injected
    return module.with_changes(body=tuple(body)).code


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def transform_lazy_imports(source: str) -> str:
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except tokenize.TokenError as exc:
        msg, pos = exc.args
        err = SyntaxError(msg)
        if isinstance(pos, tuple) and len(pos) == 2:
            err.lineno, err.offset = pos
        raise err from exc
    lazy_modules_lineno = _find_lazy_modules_declaration(tokens)
    if lazy_modules_lineno is not None:
        warnings.warn(
            f"line {lazy_modules_lineno}: ``__lazy_modules__`` is the "
            "declarative form of PEP 810 (lazy imports) and is ignored "
            "by retrofy — use the ``lazy import`` / ``lazy from`` "
            "keyword form instead. The declaration is left in place "
            "as an inert variable assignment, so you don't get the "
            "lazy-import capability that retrofy backports.",
            category=LazyModulesIgnoredWarning,
            stacklevel=2,
        )

    helpers = _helper_names(source)
    stripped, lazy_names, used = _strip_lazy_syntax(source, helpers)
    if not lazy_names:
        return source
    wrapped, reify_used = _wrap_lazy_reads(stripped, lazy_names, helpers)
    if reify_used:
        used.add("reify")
        wrapped = _apply_type_checking_duplication(wrapped, helpers)
    return _inject_runtime_import(wrapped, helpers, used)
