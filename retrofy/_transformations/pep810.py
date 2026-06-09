"""PEP 810 (lazy imports) backport rewriter.

Phase 1 (tokenize): strip ``lazy`` from ``lazy import`` / ``lazy from``
statements and replace each with assignments calling helpers from the
converted package's sibling ``_retrofy.lazy_runtime`` module. libcst
does not yet parse the 3.15 ``lazy`` soft keyword, so this phase must
run on the raw source.

Phase 2 (libcst + ``ScopeProvider``): wrap every read of a lazy-bound
module global with ``__lazy_resolve__(name)``, leaving locally-shadowed
references alone and skipping assignment LHS / ``for`` / ``with as``
binding targets.

Phase 3: inject the runtime helper import after the preamble
(shebang / encoding cookie / docstring / ``from __future__`` block).
The import is a *relative* one — ``from ._retrofy.lazy_runtime import
...`` — so the converted module never references ``retrofy`` at
runtime. The wheel-build hook drops a copy of the ``_retrofy``
sub-package into the converted package; the on-the-fly meta-path
converter synthesises it for editable / pytest contexts.

Helper names are mangled with dunder underscores
(``__lazy_import__`` etc.) and numbered (``__lazy_import_2__``) if a
user's own source already binds the un-suffixed form.
"""

from __future__ import annotations

from dataclasses import dataclass
import io
import re
import tokenize
from typing import List, Optional, Set, Tuple

import libcst as cst
from libcst.metadata import GlobalScope, ScopeProvider

_RUNTIME_MODULE = "._retrofy.lazy_runtime"

# Base helper names. ``_helper_names`` may append a ``_<n>`` suffix
# before the trailing ``__`` if the un-suffixed names collide with
# identifiers already present in the source being converted.
_BASE_HELPERS = {
    "lazy_import": "__lazy_import__",
    "lazy_import_as": "__lazy_import_as__",
    "lazy_from": "__lazy_from__",
    "resolve": "__lazy_resolve__",
}


@dataclass
class _HelperNames:
    lazy_import: str
    lazy_import_as: str
    lazy_from: str
    resolve: str

    @property
    def runtime_import_line(self) -> str:
        return (
            f"from {_RUNTIME_MODULE} import ("
            f"lazy_import as {self.lazy_import}, "
            f"lazy_import_as as {self.lazy_import_as}, "
            f"lazy_from as {self.lazy_from}, "
            f"resolve as {self.resolve}"
            f")\n"
        )


_NAME_RE = re.compile(r"\b[A-Za-z_]\w*\b")


def _helper_names(source: str) -> _HelperNames:
    """Pick non-colliding dunder helper names for *source*.

    All four helpers share the same numeric suffix so the emitted
    runtime-import line stays readable. The suffix increments until
    none of the four names appears in *source*.
    """
    existing = set(_NAME_RE.findall(source))

    def _name(base: str, suffix: str) -> str:
        # base is like ``__import_lazy__``; suffix is either ``""`` or
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


@dataclass
class _LazyStmt:
    bindings: List[str]
    replacement: str


# ---------------------------------------------------------------------------
# Phase 1: tokenize-level syntax rewrite
# ---------------------------------------------------------------------------


def _split_top_level_commas(s: str) -> List[str]:
    out: List[str] = []
    depth = 0
    buf: List[str] = []
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

    bindings: List[str] = []
    lines: List[str] = []
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
        else:
            # ``import foo.bar`` binds ``foo``
            name = clause.strip()
            top = name.partition(".")[0]
            bindings.append(top)
            lines.append(f"{top} = {helpers.lazy_import}({name!r}, {top!r})")
    return _LazyStmt(bindings=bindings, replacement="\n".join(lines))


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

    bindings: List[str] = []
    lines: List[str] = []
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
    return _LazyStmt(bindings=bindings, replacement="\n".join(lines))


def _is_statement_start(tokens: List[tokenize.TokenInfo], i: int) -> bool:
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


def _reconstruct_token_span(toks: List[tokenize.TokenInfo]) -> str:
    if not toks:
        return ""
    pieces: List[str] = []
    prev_end: Optional[Tuple[int, int]] = None
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
    edits: List[Tuple[Tuple[int, int], Tuple[int, int], str]],
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
) -> Tuple[str, List[str]]:
    readline = io.StringIO(source).readline
    tokens = list(tokenize.generate_tokens(readline))

    indent_depth = 0
    edits: List[Tuple[Tuple[int, int], Tuple[int, int], str]] = []
    lazy_names: List[str] = []

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
                edits.append(
                    (tok.start, stmt_tokens[-1].end, rewritten.replacement),
                )
                i = k
                continue
        i += 1

    if not edits:
        return source, []
    return _apply_edits(source, edits), lazy_names


# ---------------------------------------------------------------------------
# Phase 2: libcst Name-read wrapping
# ---------------------------------------------------------------------------


class _ResolveWrappingTransformer(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (ScopeProvider,)

    def __init__(self, lazy_names: Set[str], resolve_name: str) -> None:
        super().__init__()
        self._lazy_names = lazy_names
        self._resolve_name = resolve_name
        self._targets: Set[int] = set()

    def visit_Module(self, node: cst.Module) -> None:
        write_ids: Set[int] = set()
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
        out: Set[int],
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
        out: Set[int],
    ) -> None:
        if isinstance(target, cst.Name):
            out.add(id(target))
        elif isinstance(target, (cst.Tuple, cst.List)):
            for el in target.elements:
                self._collect_target_names(el.value, out)
        elif isinstance(target, cst.StarredElement):
            self._collect_target_names(target.value, out)

    def _iter_name_nodes(self, node: cst.CSTNode):
        stack: List[cst.CSTNode] = [node]
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
            func=cst.Name(self._resolve_name),
            args=[cst.Arg(value=updated_node)],
        )


def _wrap_lazy_reads(
    source: str,
    lazy_names: List[str],
    helpers: _HelperNames,
) -> str:
    if not lazy_names:
        return source
    module = cst.parse_module(source)
    wrapper = cst.metadata.MetadataWrapper(module)
    transformer = _ResolveWrappingTransformer(set(lazy_names), helpers.resolve)
    return wrapper.visit(transformer).code


# ---------------------------------------------------------------------------
# Phase 3: runtime-import injection
# ---------------------------------------------------------------------------


def _inject_runtime_import(source: str, helpers: _HelperNames) -> str:
    lines = source.splitlines(keepends=True)
    i = 0

    if i < len(lines) and lines[i].startswith("#!"):
        i += 1
    if i < len(lines) and lines[i].lstrip().startswith("#") and "coding" in lines[i]:
        i += 1

    while i < len(lines) and lines[i].strip() == "":
        i += 1

    if i < len(lines):
        stripped = lines[i].lstrip()
        if stripped.startswith(('"""', "'''")):
            quote = stripped[:3]
            rest = stripped[3:]
            if quote in rest:
                i += 1
            else:
                i += 1
                while i < len(lines) and quote not in lines[i]:
                    i += 1
                if i < len(lines):
                    i += 1

    while i < len(lines) and lines[i].lstrip().startswith("from __future__"):
        i += 1

    if i < len(lines) and lines[i].strip() == "":
        i += 1

    lines.insert(i, helpers.runtime_import_line)
    return "".join(lines)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def transform_lazy_imports(source: str) -> str:
    helpers = _helper_names(source)
    stripped, lazy_names = _strip_lazy_syntax(source, helpers)
    if not lazy_names:
        return source
    wrapped = _wrap_lazy_reads(stripped, lazy_names, helpers)
    return _inject_runtime_import(wrapped, helpers)
