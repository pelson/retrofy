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


def _helper_names(source: str) -> _HelperNames:
    """Pick non-colliding dunder helper names for *source*.

    All four helpers share the same numeric suffix so the emitted
    runtime-import line stays readable. The suffix increments until
    none of the four names appears in *source*.

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

    n = 1
    while True:
        suffix = "" if n == 1 else f"_{n}"
        candidates = {role: _name(base, suffix) for role, base in _BASE_HELPERS.items()}
        if not (set(candidates.values()) & existing):
            return _HelperNames(**candidates)
        n += 1


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


def _format_lazy_import(stmt_src: str, helpers: _HelperNames) -> _LazyStmt:
    body = stmt_src.strip()
    assert body.startswith("import"), body
    body = body[len("import") :].strip()

    bindings: list[str] = []
    lines: list[str] = []
    used: set[str] = set()
    for clause in _split_top_level_commas(body):
        clause = clause.strip()
        if " as " in clause:
            name, _, alias = clause.partition(" as ")
            name = name.strip()
            alias = alias.strip()
            bindings.append(alias)
            lines.append(
                f"{alias} = {helpers.lazy_import_as}({name!r}, {alias!r})",
            )
            used.add("lazy_import_as")
        else:
            # ``import foo.bar`` binds ``foo``
            name = clause.strip()
            top = name.partition(".")[0]
            bindings.append(top)
            lines.append(f"{top} = {helpers.lazy_import}({name!r}, {top!r})")
            used.add("lazy_import")
    return _LazyStmt(
        bindings=bindings,
        replacement="\n".join(lines),
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
    lines: list[str] = []
    for clause in _split_top_level_commas(names_part):
        clause = clause.strip()
        if not clause:
            continue
        if " as " in clause:
            attr, _, alias = clause.partition(" as ")
            attr = attr.strip()
            alias = alias.strip()
        else:
            attr = clause
            alias = clause
        bindings.append(alias)
        if module.startswith("."):
            # Relative ``lazy from ... import`` needs the calling
            # module's ``__package__`` so ``importlib.import_module``
            # can resolve the relative target.
            args = f"{module!r}, {attr!r}, {alias!r}, package=__package__"
        else:
            args = f"{module!r}, {attr!r}, {alias!r}"
        lines.append(f"{alias} = {helpers.lazy_from}({args})")
    return _LazyStmt(
        bindings=bindings,
        replacement="\n".join(lines),
        used_helpers={"lazy_from"} if bindings else set(),
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
                i = k
                continue
        i += 1

    if not edits:
        return source, [], set()
    return _apply_edits(source, edits), lazy_names, used_helpers


# ---------------------------------------------------------------------------
# Phase 2: libcst Name-read wrapping
# ---------------------------------------------------------------------------


class _ReifyWrappingTransformer(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (ScopeProvider,)

    def __init__(self, lazy_names: set[str], reify_name: str) -> None:
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
    """
    if not lazy_names:
        return source, False
    module = cst.parse_module(source)
    wrapper = cst.metadata.MetadataWrapper(module)
    transformer = _ReifyWrappingTransformer(set(lazy_names), helpers.reify)
    rewritten = wrapper.visit(transformer).code
    return rewritten, bool(transformer._targets)


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


def _inject_runtime_import(
    source: str,
    helpers: _HelperNames,
    used: set[str],
) -> str:
    """Insert the runtime-import statement after the module's preamble.

    The preamble is the optional docstring plus any
    ``from __future__ import ...`` statements (which must remain at
    the top of the module to be effective). Everything else stays put.
    Any blank line that originally separated the preamble from the
    body is transferred to lead the injected import, so the
    docstring-blank-import-body shape is preserved.
    """
    if not used:
        return source

    module = cst.parse_module(source)
    body = list(module.body)
    insert_at = 0
    if body and _is_module_docstring(body[0]):
        insert_at = 1
    while insert_at < len(body) and _is_future_import(body[insert_at]):
        insert_at += 1

    new_import = _build_runtime_import(helpers, used)
    if insert_at < len(body):
        # Transfer the blank lines that originally separated the
        # preamble from the first body statement onto our injected
        # import, so the docstring → blank → import shape is kept.
        following = body[insert_at]
        leading = getattr(following, "leading_lines", ())
        if leading:
            new_import = new_import.with_changes(leading_lines=leading)
            body[insert_at] = following.with_changes(leading_lines=())
    body.insert(insert_at, new_import)
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
    return _inject_runtime_import(wrapped, helpers, used)
