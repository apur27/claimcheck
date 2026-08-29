"""Deterministic verifiers.

Four pure functions, one per `Claim.shape` in `VALID_SHAPES` minus `"other"`,
each settling whether the code contradicts a claim using only `ast` --
never `import`/`importlib`/`exec` of the scanned code, the same hard
constraint `services/extract.py` holds. Reading source text with
`Path.read_text` is fine; running it is not.

Each verifier returns a `Verdict` carrying one of the frozen `REASON_CODES`
plus a one-line `evidence` string, mirroring the `evidence` field already
used in `data/labelled_claims.jsonl`. `verify()` dispatches a `Claim` to the
matching verifier by `shape`; `shape == "other"` has no deterministic
verifier and always resolves `unverifiable` -- that shape is the
model-backed verifier's job, out of scope here.
"""

from __future__ import annotations

import ast
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from claimcheck.domain.models import Claim

REASON_CODES = frozenset({"ok", "contradicted", "unverifiable", "unparsed"})

_EXCLUDED_DIR_NAMES = frozenset({".venv", "__pycache__", ".git"})
_EXCEPTION_BASE_SUFFIXES = ("Error", "Exception")
_SINGLE_TARGET_COUNT = 1
_SCRIPT_DIR_NAMES = ("bin", "scripts")

_DEFAULT_PHRASE_PATTERN = re.compile(
    r"defaults?\s+(?:to|is)\s+[\"'`]?(-?\d+(?:\.\d+)?|[^\s\"'`,.;]+)", re.IGNORECASE
)
_NUMBER_PATTERN = re.compile(r"-?\d+(?:\.\d+)?")
_RETURNS_TYPE_PATTERN = re.compile(
    r"\breturns?\s+(?:an?\s+|the\s+)?([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE
)
_INLINE_CODE_PATTERN = re.compile(r"`([^`]+)`")
_PATH_LIKE_PATTERN = re.compile(r"\b[\w][\w./-]*\.[A-Za-z]{1,6}\b")
_PATH_EXTENSION_PATTERN = re.compile(r"\.[A-Za-z0-9]{1,6}$")

_FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef
_DocstringNode = ast.Module | ast.ClassDef | _FunctionNode


@dataclass(frozen=True)
class Verdict:
    """A verifier's settlement of one `Claim` against the code it describes.

    Attributes:
        reason: one of `REASON_CODES`.
        evidence: one line explaining the verdict -- mirrors the `evidence`
            field already used in `data/labelled_claims.jsonl`.
    """

    reason: str
    evidence: str


# --- shared AST helpers -----------------------------------------------


def _parse_python_file(path: Path) -> ast.Module | None:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return ast.parse(source, filename=str(path))
    except SyntaxError:
        return None


def _base_name(expr: ast.expr) -> str | None:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        return expr.attr
    return None


def _iter_py_files(repo_root: Path) -> list[Path]:
    files = []
    for path in sorted(repo_root.rglob("*.py")):
        dir_parts = set(path.relative_to(repo_root).parts[:-1])
        if _EXCLUDED_DIR_NAMES & dir_parts:
            continue
        files.append(path)
    return files


def _docstring_span(node: _DocstringNode) -> tuple[int, int] | None:
    if not node.body:
        return None
    first = node.body[0]
    if not isinstance(first, ast.Expr) or not isinstance(first.value, ast.Constant):
        return None
    if not isinstance(first.value.value, str):
        return None
    return first.lineno, first.end_lineno or first.lineno


def _docstring_spans_line(node: _DocstringNode, line: int) -> bool:
    span = _docstring_span(node)
    return span is not None and span[0] <= line <= span[1]


def _function_spans_line(node: _FunctionNode, line: int) -> bool:
    end = node.end_lineno or node.lineno
    return node.lineno <= line <= end


# --- verify_raises_propagates -------------------------------------------


def _is_exception_class(node: ast.ClassDef) -> bool:
    return any(
        (name := _base_name(base)) is not None and name.endswith(_EXCEPTION_BASE_SUFFIXES)
        for base in node.bases
    )


def _raised_exception_name(exc: ast.expr) -> str | None:
    if isinstance(exc, ast.Call):
        return _base_name(exc.func)
    return _base_name(exc)


def _first_raised_name(func: _FunctionNode) -> str | None:
    for sub in ast.walk(func):
        if isinstance(sub, ast.Raise) and sub.exc is not None:
            name = _raised_exception_name(sub.exc)
            if name is not None:
                return name
    return None


def _find_exception_name(tree: ast.Module, line: int) -> str | None:
    """Identify the exception class a docstring at `line` is discussing.

    A class docstring on an exception class names that class. A function
    docstring names whatever it `raise`s first. A module docstring names no
    single class -- `None`, which the caller reports as `unverifiable`.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | _FunctionNode):
            continue
        if not _docstring_spans_line(node, line):
            continue
        if isinstance(node, ast.ClassDef):
            return node.name if _is_exception_class(node) else None
        if isinstance(node, _FunctionNode):
            return _first_raised_name(node)
        return None
    return None


def _find_handler_locations(repo_root: Path, exception_name: str) -> list[str]:
    locations: list[str] = []
    for path in _iter_py_files(repo_root):
        tree = _parse_python_file(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler) or node.type is None:
                continue
            types = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
            for exc_type in types:
                if _base_name(exc_type) == exception_name:
                    rel = path.relative_to(repo_root).as_posix()
                    locations.append(f"{rel}:{node.lineno}")
    return locations


def verify_raises_propagates(claim: Claim, repo_root: Path) -> Verdict:
    """Settle a "PROPAGATES"/"raises X uncaught" claim against the whole repo.

    A handler for the named exception found anywhere means the claim's "no
    handler exists" is stale -- `contradicted`. No handler anywhere means the
    claim holds -- `ok`.
    """
    tree = _parse_python_file(repo_root / claim.file)
    if tree is None:
        return Verdict("unverifiable", f"could not parse {claim.file} as Python source")
    exception_name = _find_exception_name(tree, claim.line)
    if exception_name is None:
        return Verdict(
            "unverifiable", f"no exception class identifiable near {claim.file}:{claim.line}"
        )
    locations = _find_handler_locations(repo_root, exception_name)
    if locations:
        return Verdict(
            "contradicted",
            f"{claim.file}:{claim.line} claims no handler for {exception_name}, "
            f"but {locations[0]} handles it",
        )
    return Verdict("ok", f"no handler for {exception_name} found anywhere under {repo_root}")


# --- verify_defaults_to --------------------------------------------------


def _extract_default_literal(text: str) -> str | None:
    phrase_match = _DEFAULT_PHRASE_PATTERN.search(text)
    if phrase_match:
        return phrase_match.group(1).strip("\"'`")
    number_match = _NUMBER_PATTERN.search(text)
    if number_match:
        return number_match.group(0)
    return None


def _default_candidates(tree: ast.Module) -> list[tuple[int, ast.Constant]]:
    """Every module-level constant assignment and function-default literal.

    Each candidate carries the source line its literal actually sits on, so
    the caller can pick whichever candidate is nearest the claim.
    """
    candidates: list[tuple[int, ast.Constant]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == _SINGLE_TARGET_COUNT
            and isinstance(node.value, ast.Constant)
        ):
            candidates.append((node.value.lineno, node.value))
        elif isinstance(node, _FunctionNode):
            defaults = (*node.args.defaults, *node.args.kw_defaults)
            candidates.extend(
                (default.lineno, default)
                for default in defaults
                if isinstance(default, ast.Constant)
            )
    return candidates


def _nearest_default(tree: ast.Module, line: int) -> ast.Constant | None:
    candidates = _default_candidates(tree)
    if not candidates:
        return None
    return min(candidates, key=lambda pair: abs(pair[0] - line))[1]


def _literal_matches(actual: ast.Constant, claimed: str) -> bool:
    value = actual.value
    if isinstance(value, bool):
        return str(value).lower() == claimed.lower()
    if isinstance(value, int | float):
        try:
            return float(value) == float(claimed)
        except ValueError:
            return False
    if isinstance(value, str):
        return value.strip().lower() == claimed.strip().lower()
    return False


def verify_defaults_to(claim: Claim, repo_root: Path) -> Verdict:
    """Settle a "defaults to N"/"default is N" claim against the actual AST default."""
    literal = _extract_default_literal(claim.claim_text)
    if literal is None:
        return Verdict("unparsed", f"no default value found in claim text: {claim.claim_text!r}")
    tree = _parse_python_file(repo_root / claim.file)
    if tree is None:
        return Verdict("unverifiable", f"could not parse {claim.file} as Python source")
    target = _nearest_default(tree, claim.line)
    if target is None:
        return Verdict(
            "unverifiable",
            f"no default value found in {claim.file} to compare against {literal!r}",
        )
    if _literal_matches(target, literal):
        return Verdict(
            "ok", f"{claim.file}:{claim.line} actual default matches claimed {literal!r}"
        )
    actual_repr = ast.unparse(target)
    return Verdict(
        "contradicted",
        f"{claim.file}:{claim.line} actual default is {actual_repr}, claim says {literal!r}",
    )


# --- verify_returns_type --------------------------------------------------


def _nearest_enclosing_function(tree: ast.Module, line: int) -> _FunctionNode | None:
    best: _FunctionNode | None = None
    best_span: int | None = None
    for node in ast.walk(tree):
        if not isinstance(node, _FunctionNode) or not _function_spans_line(node, line):
            continue
        span = (node.end_lineno or node.lineno) - node.lineno
        if best_span is None or span < best_span:
            best, best_span = node, span
    return best


def _extract_claimed_type(text: str) -> str | None:
    match = _RETURNS_TYPE_PATTERN.search(text)
    return match.group(1) if match else None


def verify_returns_type(claim: Claim, repo_root: Path) -> Verdict:
    """Settle a "returns X" claim against the nearest function's return annotation."""
    tree = _parse_python_file(repo_root / claim.file)
    if tree is None:
        return Verdict("unverifiable", f"could not parse {claim.file} as Python source")
    func = _nearest_enclosing_function(tree, claim.line)
    if func is None or func.returns is None:
        return Verdict("unverifiable", f"no return annotation found near {claim.file}:{claim.line}")
    claimed_type = _extract_claimed_type(claim.claim_text)
    if claimed_type is None:
        return Verdict("unverifiable", f"no claimed type found in: {claim.claim_text!r}")
    annotation_text = ast.unparse(func.returns)
    if claimed_type.lower() in annotation_text.lower():
        return Verdict(
            "ok", f"{claim.file}:{claim.line} annotation {annotation_text} agrees with claim"
        )
    return Verdict(
        "contradicted",
        f"{claim.file}:{claim.line} annotation is {annotation_text}, claim says {claimed_type!r}",
    )


# --- verify_markdown_reference ---------------------------------------------


def _extract_markdown_target(text: str) -> str | None:
    inline_match = _INLINE_CODE_PATTERN.search(text)
    if inline_match:
        return inline_match.group(1).strip()
    path_match = _PATH_LIKE_PATTERN.search(text)
    if path_match:
        return path_match.group(0)
    return None


def _looks_like_path(token: str) -> bool:
    return "/" in token or _PATH_EXTENSION_PATTERN.search(token) is not None


def _is_declared_script(repo_root: Path, token: str) -> bool:
    pyproject = repo_root / "pyproject.toml"
    if pyproject.exists():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError:
            data = {}
        scripts = data.get("project", {}).get("scripts", {})
        if token in scripts:
            return True
    for directory in _SCRIPT_DIR_NAMES:
        if (repo_root / directory / token).exists():
            return True
        if (repo_root / directory / f"{token}.py").exists():
            return True
    return False


def verify_markdown_reference(claim: Claim, repo_root: Path) -> Verdict:
    """Settle a claim naming a command or a file by checking it exists in `repo_root`."""
    content = _extract_markdown_target(claim.claim_text)
    if content is None:
        return Verdict(
            "unverifiable", f"no command or file reference found in: {claim.claim_text!r}"
        )
    tokens = content.split()
    token = tokens[0] if tokens else content
    if _looks_like_path(token):
        target = repo_root / token.lstrip("/")
        if target.exists():
            return Verdict("ok", f"{token} exists at {target}")
        return Verdict("contradicted", f"{token} does not exist under {repo_root}")
    if _is_declared_script(repo_root, token):
        return Verdict("ok", f"{token!r} is a declared script/command under {repo_root}")
    return Verdict(
        "contradicted",
        f"{token!r} is not a [project.scripts] entry or executable under {repo_root}",
    )


# --- dispatcher ------------------------------------------------------------

_VERIFIERS: dict[str, Callable[[Claim, Path], Verdict]] = {
    "raises_propagates": verify_raises_propagates,
    "defaults_to": verify_defaults_to,
    "returns_type": verify_returns_type,
    "markdown_reference": verify_markdown_reference,
}


def verify(claim: Claim, repo_root: Path) -> Verdict:
    """Dispatch `claim` to the deterministic verifier matching its `shape`.

    `shape == "other"` has no deterministic verifier -- that is the
    model-backed verifier's job, out of scope here -- and always resolves
    `unverifiable`.
    """
    verifier = _VERIFIERS.get(claim.shape)
    if verifier is None:
        return Verdict("unverifiable", f"shape {claim.shape!r} has no deterministic verifier")
    return verifier(claim, repo_root)
