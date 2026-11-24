"""Scan a repository into files, symbols, calls and imports.

Python gets a real AST scan. Symbol hashes are *semantic*: they come from
``ast.dump`` without the symbol's own name or docstring, so reformatting or
comment edits never invalidate a memory, and a renamed or moved function keeps
its hash, which is how Receipts follows renames instead of crying wolf.

JavaScript/TypeScript get a lighter regex scan. Every other text file is
tracked at file level, which is enough for quote-anchored citations such as
``tax_rate: 0.08`` in a YAML file.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import os
import re
import subprocess
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

CODE_EXT = {".py": "python", ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
            ".cjs": "javascript", ".ts": "typescript", ".tsx": "typescript"}
TEXT_EXT = {".md", ".rst", ".txt", ".yaml", ".yml", ".toml", ".json", ".cfg", ".ini", ".sql",
            ".sh", ".go", ".rs", ".java", ".rb", ".kt", ".swift", ".c", ".h", ".cpp", ".cs",
            ".html", ".css", ".env.example", ".graphql", ".proto", ".tf"}
SKIP_DIRS = {".git", ".venv", "venv", "env", "node_modules", "__pycache__", ".receipts", "dist", "build",
             ".mypy_cache", ".pytest_cache", ".tox", ".ruff_cache", "site-packages", ".next", ".idea",
             ".vscode", "coverage", ".cache"}
MAX_BYTES = 512 * 1024


def sha(text: str | bytes) -> str:
    if isinstance(text, str):
        text = text.encode("utf-8", "replace")
    return hashlib.sha256(text).hexdigest()[:16]


@dataclass
class SymbolInfo:
    key: str            # "<path>::<qualname>"
    name: str
    qualname: str
    kind: str           # function | method | class | constant
    path: str
    start: int
    end: int
    signature: str
    sig_hash: str
    body_hash: str
    source: str
    calls: list[str] = field(default_factory=list)
    types_hash: str = ""   # canonical type hints; changes are recorded but never invalidate


@dataclass
class FileInfo:
    path: str           # repo-relative, posix
    lang: str
    hash: str
    lines: int
    text: str
    symbols: list[SymbolInfo] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)   # dotted module names (python) or relative specs (js)
    size: int = 0       # bytes on disk, with mtime a cheap "did this file change?" check
    mtime: int = 0      # st_mtime_ns


# --------------------------------------------------------------------------- #
# File discovery
# --------------------------------------------------------------------------- #


def list_files(root: Path) -> list[str]:
    """Repo-relative paths, honouring .gitignore when the repo uses git."""
    paths: list[str] = []
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
            capture_output=True, timeout=20,
        )
        if out.returncode == 0:
            paths = [p for p in out.stdout.decode("utf-8", "replace").split("\0") if p]
    except (OSError, subprocess.SubprocessError):
        paths = []
    if not paths:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in filenames:
                paths.append(os.path.relpath(os.path.join(dirpath, fn), root))
    keep = []
    for p in paths:
        parts = Path(p).parts
        if any(part in SKIP_DIRS for part in parts):
            continue
        suffix = Path(p).suffix.lower()
        if suffix in CODE_EXT or suffix in TEXT_EXT or Path(p).name in {"Dockerfile", "Makefile"}:
            keep.append(Path(p).as_posix())
    return sorted(set(keep))


def read_text(root: Path, rel: str) -> str | None:
    full = root / rel
    try:
        if full.stat().st_size > MAX_BYTES:
            return None
        return full.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# Python
# --------------------------------------------------------------------------- #


def _strip_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        return body[1:]
    return body


def _dump(nodes) -> str:
    if isinstance(nodes, list):
        return "[" + ",".join(ast.dump(n, include_attributes=False) for n in nodes) + "]"
    return ast.dump(nodes, include_attributes=False) if nodes is not None else ""


# Bump when the hashing changes; stores built with an older version are
# migrated by graph.sync (citations are re-hashed from their saved snapshots).
HASH_VERSION = 2

_TYPING_MODULES = {"t", "typing", "te", "typing_extensions", "cabc", "abc"}
_GENERIC_ALIASES = {"List": "list", "Dict": "dict", "Set": "set", "FrozenSet": "frozenset", "Tuple": "tuple",
                    "Type": "type"}


class _TypeExpr(ast.NodeTransformer):
    """Canonical form of a type annotation, so equivalent spellings hash the same:
    ``t.Optional[str]`` == ``str | None``, ``t.Dict[str, int]`` == ``dict[str, int]``,
    ``"Money"`` == ``Money``, and union member order doesn't matter."""

    def visit_Constant(self, n):
        if isinstance(n.value, str):
            try:
                return self.visit(ast.parse(n.value, mode="eval").body)
            except SyntaxError:
                return n
        return n

    def visit_Attribute(self, n):
        self.generic_visit(n)
        if isinstance(n.value, ast.Name) and n.value.id in _TYPING_MODULES:
            return self.visit_Name(ast.Name(id=n.attr, ctx=ast.Load()))
        return n

    def visit_Name(self, n):
        return ast.Name(id=_GENERIC_ALIASES.get(n.id, n.id), ctx=ast.Load())

    def visit_Subscript(self, n):
        self.generic_visit(n)
        name = n.value.id if isinstance(n.value, ast.Name) else None
        args = list(n.slice.elts) if isinstance(n.slice, ast.Tuple) else [n.slice]
        if name == "Optional":
            return _union([args[0], ast.Constant(None)])
        if name == "Union":
            return _union(args)
        return n

    def visit_BinOp(self, n):
        self.generic_visit(n)
        if isinstance(n.op, ast.BitOr):
            return _union([n.left, n.right])
        return n


def _union(items: list[ast.expr]) -> ast.expr:
    members: dict[str, ast.expr] = {}
    for it in items:
        parts = it.slice.elts if (isinstance(it, ast.Subscript) and isinstance(it.value, ast.Name)
                                  and it.value.id == "__union__") else [it]
        for p in parts:
            members.setdefault(ast.dump(p, include_attributes=False), p)
    ordered = [members[k] for k in sorted(members)]
    return ast.Subscript(value=ast.Name(id="__union__", ctx=ast.Load()),
                         slice=ast.Tuple(elts=ordered, ctx=ast.Load()), ctx=ast.Load())


class _Normalize(ast.NodeTransformer):
    """What a symbol *does*: type hints and nested docstrings removed.

    Memories describe behaviour, so a hint changing from ``dict`` to
    ``dict[str, Any]`` must not make one stale. Hint changes are tracked
    separately (``types_hash``) and recorded, but don't invalidate anything.
    """

    def _type(self, n):
        return None

    def visit_arg(self, n):
        n.annotation = self._type(n.annotation)
        return n

    def _strip(self, n):
        self.generic_visit(n)
        n.body = _strip_docstring(n.body) or [ast.Pass()]
        return n

    def visit_FunctionDef(self, n):
        n = self._strip(n)
        n.returns = self._type(n.returns)
        return n

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = _strip

    def visit_AnnAssign(self, n):
        self.generic_visit(n)
        n.annotation = self._type(n.annotation)
        return n


def _normalized(node):
    return _Normalize().visit(copy.deepcopy(node))


def _types_hash(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Canonical type hints of a function: equivalent spellings hash the same."""
    canon = lambda n: "" if n is None else _dump(_TypeExpr().visit(copy.deepcopy(n)))  # noqa: E731
    a = node.args
    params = [*a.posonlyargs, *a.args, *a.kwonlyargs, *([a.vararg] if a.vararg else []), *([a.kwarg] if a.kwarg else [])]
    return sha("|".join([*(f"{p.arg}:{canon(p.annotation)}" for p in params), "->" + canon(node.returns)]))


def _func_hashes(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, str, str]:
    sig = f"{node.name}({ast.unparse(node.args)})" + (f" -> {ast.unparse(node.returns)}" if node.returns else "")
    norm = _normalized(node)
    sig_material = _dump(norm.args) + "|" + _dump(norm.returns)
    body_material = sig_material + "|" + _dump(norm.decorator_list) + "|" + _dump(norm.body)
    return sig, sha(sig_material), sha(body_material)


def _class_hashes(node: ast.ClassDef) -> tuple[str, str, str]:
    bases = ", ".join(ast.unparse(b) for b in node.bases)
    sig = f"class {node.name}({bases})" if bases else f"class {node.name}"
    norm = _normalized(node)
    sig_material = _dump(norm.bases) + "|" + _dump(norm.keywords)
    body_material = sig_material + "|" + _dump(norm.decorator_list) + "|" + _dump(norm.body)
    return sig, sha(sig_material), sha(body_material)


def hash_snippet(source: str) -> tuple[str, str] | None:
    """(sig_hash, body_hash) of the first symbol in a saved source snippet, or None."""
    try:
        tree = ast.parse(textwrap.dedent(source or ""))
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return _func_hashes(node)[1:]
        if isinstance(node, ast.ClassDef):
            return _class_hashes(node)[1:]
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            h = sha(_dump(node.value))
            return h, h
    return None


def _calls_in(node: ast.AST) -> list[str]:
    names: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            if isinstance(f, ast.Name):
                names.append(f.id)
            elif isinstance(f, ast.Attribute):
                names.append(f.attr)
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _segment(lines: list[str], node: ast.AST) -> str:
    start = getattr(node, "lineno", 1)
    if getattr(node, "decorator_list", None):
        start = min(start, *(d.lineno for d in node.decorator_list))
    end = getattr(node, "end_lineno", start)
    return "\n".join(lines[start - 1:end])


def scan_python(rel: str, text: str) -> FileInfo:
    info = FileInfo(rel, "python", sha(text), text.count("\n") + 1, text)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return info
    lines = text.splitlines()

    def add_symbol(node, qualname: str, kind: str, sig: str, sig_h: str, body_h: str, calls: list[str]):
        start = getattr(node, "lineno", 1)
        info.symbols.append(SymbolInfo(
            key=f"{rel}::{qualname}", name=qualname.split(".")[-1], qualname=qualname, kind=kind, path=rel,
            start=start, end=getattr(node, "end_lineno", start), signature=sig, sig_hash=sig_h,
            body_hash=body_h, source=_segment(lines, node), calls=calls,
            types_hash=_types_hash(node) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else "",
        ))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sig, sh, bh = _func_hashes(node)
            add_symbol(node, node.name, "function", sig, sh, bh, _calls_in(node))
        elif isinstance(node, ast.ClassDef):
            sig, sh, bh = _class_hashes(node)
            add_symbol(node, node.name, "class", sig, sh, bh, [])
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    sig2, sh2, bh2 = _func_hashes(sub)
                    add_symbol(sub, f"{node.name}.{sub.name}", "method", sig2, sh2, bh2, _calls_in(sub))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and re.fullmatch(r"[A-Z][A-Z0-9_]*", t.id):
                    value = _dump(node.value)
                    src = _segment(lines, node)
                    add_symbol(node, t.id, "constant", src.strip()[:120], sha(value), sha(value), [])
        elif isinstance(node, ast.Import):
            info.imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = ("." * node.level) + (node.module or "")
            info.imports.append(mod)
            info.imports.extend(f"{mod}.{a.name}" if mod and not mod.endswith(".") else f"{mod}{a.name}"
                                for a in node.names)
    return info


# --------------------------------------------------------------------------- #
# JavaScript / TypeScript (regex, best effort)
# --------------------------------------------------------------------------- #

JS_SYMBOL_RES = [
    (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(", re.M), "function"),
    (re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*(?::[^=]+)?=>", re.M), "function"),
    (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)", re.M), "class"),
]
JS_IMPORT_RE = re.compile(r"""(?:import\s[^'"]*from\s*|require\(\s*|import\(\s*)['"]([^'"]+)['"]""")
JS_CALL_RE = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\(")
JS_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)


def scan_js(rel: str, text: str, lang: str) -> FileInfo:
    info = FileInfo(rel, lang, sha(text), text.count("\n") + 1, text)
    starts: list[tuple[int, str, str]] = []
    for rx, kind in JS_SYMBOL_RES:
        for m in rx.finditer(text):
            starts.append((m.start(), m.group(1), kind))
    starts.sort()
    for i, (pos, name, kind) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        block = text[pos:end]
        norm = re.sub(r"\s+", " ", JS_COMMENT_RE.sub("", block)).strip()
        body_material = norm.replace(name, "§", 1)  # name-independent, so renames are followed
        head = block.split("{", 1)[0].split("=>", 1)[0].strip()
        start_line = text.count("\n", 0, pos) + 1
        info.symbols.append(SymbolInfo(
            key=f"{rel}::{name}", name=name, qualname=name, kind=kind, path=rel, start=start_line,
            end=start_line + block.rstrip().count("\n"), signature=head[:160],
            sig_hash=sha(head.replace(name, "§", 1)), body_hash=sha(body_material), source=block.rstrip(),
            calls=sorted({c for c in JS_CALL_RE.findall(block) if c != name} - {"if", "for", "while", "switch", "return", "function", "catch"}),
        ))
    info.imports = JS_IMPORT_RE.findall(text)
    return info


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def stat_file(root: Path, rel: str) -> tuple[int, int] | None:
    """(size, mtime_ns), or None if the file is gone or too big to track."""
    try:
        st = (root / rel).stat()
    except OSError:
        return None
    if st.st_size > MAX_BYTES:
        return None
    return st.st_size, st.st_mtime_ns


def scan_file(root: Path, rel: str) -> FileInfo | None:
    text = read_text(root, rel)
    if text is None:
        return None
    return scan_text(rel, text)


def scan_text(rel: str, text: str) -> FileInfo:
    lang = CODE_EXT.get(Path(rel).suffix.lower())
    if lang == "python":
        return scan_python(rel, text)
    if lang in ("javascript", "typescript"):
        return scan_js(rel, text, lang)
    return FileInfo(rel, "text", sha(text), text.count("\n") + 1, text)


def scan_repo(root: Path, only: list[str] | None = None) -> dict[str, FileInfo]:
    rels = only if only is not None else list_files(root)
    out: dict[str, FileInfo] = {}
    for rel in rels:
        fi = scan_file(root, rel)
        if fi is not None:
            out[rel] = fi
    return out


def module_index(paths: list[str]) -> dict[str, str]:
    """Map dotted python module names (and suffixes of them) to repo paths."""
    index: dict[str, str] = {}
    for p in paths:
        if not p.endswith(".py"):
            continue
        mod = p[:-3].replace("/", ".")
        if mod.endswith(".__init__"):
            mod = mod[: -len(".__init__")]
        parts = mod.split(".")
        for i in range(len(parts)):
            index.setdefault(".".join(parts[i:]), p)
    return index


def resolve_imports(fi: FileInfo, mods: dict[str, str], all_paths: set[str]) -> list[str]:
    targets: set[str] = set()
    if fi.lang == "python":
        pkg = fi.path.rsplit("/", 1)[0].replace("/", ".") if "/" in fi.path else ""
        for imp in fi.imports:
            name = imp
            if imp.startswith("."):
                level = len(imp) - len(imp.lstrip("."))
                base = pkg.split(".") if pkg else []
                base = base[: len(base) - (level - 1)] if level > 1 else base
                name = ".".join([*base, imp.lstrip(".")]).strip(".")
            for cand in (name, name.rsplit(".", 1)[0] if "." in name else None):
                if cand and cand in mods and mods[cand] != fi.path:
                    targets.add(mods[cand])
                    break
    else:
        base_dir = os.path.dirname(fi.path)
        for spec in fi.imports:
            if not spec.startswith("."):
                continue
            stem = os.path.normpath(os.path.join(base_dir, spec)).replace("\\", "/")
            for ext in ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", "/index.ts", "/index.js"):
                if stem + ext in all_paths:
                    targets.add(stem + ext)
                    break
    return sorted(targets)
