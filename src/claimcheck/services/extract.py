"""AST-based claim extraction.

Walks a repo tree and pulls `Claim` objects out of Python docstrings,
Python comments and markdown prose -- without ever importing or executing
the scanned code. Only `ast.parse` (never `import`/`importlib`/`exec`) and
`tokenize.generate_tokens` touch the scanned source.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from collections.abc import Iterator
from pathlib import Path

from claimcheck.domain.models import Claim

_PY_SUFFIX = ".py"
_MD_SUFFIX = ".md"
_SOURCE_SUFFIXES = (_PY_SUFFIX, _MD_SUFFIX)
_EXCLUDED_DIR_NAMES = frozenset({".venv", "__pycache__", ".git"})

_PROPAGATES_MARKER = "PROPAGATES:"
_RAISES_PATTERN = re.compile(r"\braises\b")
_PROPAGATES_PATTERN = re.compile(r"propagates")
_DEFAULTS_PATTERN = re.compile(r"defaults to|default is")
_RETURNS_PATTERN = re.compile(r"\breturns\b")
_INLINE_CODE_PATTERN = re.compile(r"`[^`]+`")
_PATH_LIKE_PATTERN = re.compile(r"\b[\w][\w./-]*\.[A-Za-z]{1,6}\b")

type _DocstringNode = ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef


def extract_claims(repo_root: Path) -> list[Claim]:
    """Walk `repo_root` and return every claim found in `.py` and `.md` files.

    Never imports, execs or otherwise runs any file under `repo_root` --
    Python files go through `ast.parse` and `tokenize.generate_tokens`
    only.
    """
    claims: list[Claim] = []
    for path in _iter_source_files(repo_root):
        if path.suffix == _PY_SUFFIX:
            claims.extend(_extract_python_claims(path, repo_root))
        else:
            claims.extend(_extract_markdown_claims(path, repo_root))
    return claims


def _iter_source_files(repo_root: Path) -> Iterator[Path]:
    for path in sorted(repo_root.rglob("*")):
        if not path.is_file() or path.suffix not in _SOURCE_SUFFIXES:
            continue
        dir_parts = set(path.relative_to(repo_root).parts[:-1])
        if _EXCLUDED_DIR_NAMES & dir_parts:
            continue
        yield path


def _extract_python_claims(path: Path, repo_root: Path) -> list[Claim]:
    source = path.read_text(encoding="utf-8")
    rel_path = path.relative_to(repo_root).as_posix()
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []
    claims = _extract_docstring_claims(tree, rel_path)
    claims.extend(_extract_comment_claims(source, rel_path))
    return claims


def _extract_docstring_claims(tree: ast.Module, rel_path: str) -> list[Claim]:
    claims: list[Claim] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        text = ast.get_docstring(node)
        line = _docstring_line(node)
        if not text or line is None:
            continue
        has_return_annotation = (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.returns is not None
        )
        shape = _classify_claim_text(text, has_return_annotation=has_return_annotation)
        claims.append(
            Claim(
                file=rel_path,
                line=line,
                claim_text=text.strip(),
                shape=shape,
                source="docstring",
            )
        )
    return claims


def _docstring_line(node: _DocstringNode) -> int | None:
    """Return the line the docstring literal itself starts on, or `None`."""
    if not node.body:
        return None
    first = node.body[0]
    if not isinstance(first, ast.Expr) or not isinstance(first.value, ast.Constant):
        return None
    if not isinstance(first.value.value, str):
        return None
    return first.lineno


def _classify_claim_text(text: str, *, has_return_annotation: bool) -> str:
    lowered = text.lower()
    if (
        _PROPAGATES_MARKER in text
        or _PROPAGATES_PATTERN.search(lowered)
        or _RAISES_PATTERN.search(lowered)
    ):
        return "raises_propagates"
    if _DEFAULTS_PATTERN.search(lowered):
        return "defaults_to"
    if has_return_annotation and _RETURNS_PATTERN.search(lowered):
        return "returns_type"
    return "other"


def _classify_comment_text(text: str) -> str | None:
    lowered = text.lower()
    if (
        _PROPAGATES_MARKER in text
        or _PROPAGATES_PATTERN.search(lowered)
        or _RAISES_PATTERN.search(lowered)
    ):
        return "raises_propagates"
    if _DEFAULTS_PATTERN.search(lowered):
        return "defaults_to"
    if _RETURNS_PATTERN.search(lowered):
        return "returns_type"
    return None


def _extract_comment_claims(source: str, rel_path: str) -> list[Claim]:
    # `source` already parsed cleanly via `ast.parse` in `_extract_python_claims`
    # (a `SyntaxError` there returns early), so CPython's tokenizer -- which
    # agrees with its parser on what is valid -- cannot fail here either.
    claims: list[Claim] = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type != tokenize.COMMENT:
            continue
        text = tok.string.lstrip("#").strip()
        shape = _classify_comment_text(text) if text else None
        if shape is None:
            continue
        claims.append(
            Claim(file=rel_path, line=tok.start[0], claim_text=text, shape=shape, source="comment")
        )
    return claims


def _extract_markdown_claims(path: Path, repo_root: Path) -> list[Claim]:
    rel_path = path.relative_to(repo_root).as_posix()
    claims: list[Claim] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for line_no, raw_line in enumerate(lines, start=1):
        text = raw_line.strip()
        if not text:
            continue
        if _INLINE_CODE_PATTERN.search(text) or _PATH_LIKE_PATTERN.search(text):
            claims.append(
                Claim(
                    file=rel_path,
                    line=line_no,
                    claim_text=text,
                    shape="markdown_reference",
                    source="markdown",
                )
            )
    return claims
