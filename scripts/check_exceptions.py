"""Mechanical check: every exception class this package declares is handled.

Walks every module under ``src/claimcheck`` and finds class definitions whose
base names end in ``Error`` or ``Exception`` (a "declared" exception type).
Each declared type must either:

- be named in an ``except`` clause somewhere in the package, or
- carry a ``PROPAGATES:`` line in its docstring saying what happens when it
  reaches the top level.

Exits non-zero and prints the offending class names if either condition is
unmet for a declared type. Run via ``make exceptions`` or directly as
``python scripts/check_exceptions.py``.

PROPAGATES: none — this script is a standalone entry point, not imported by
the package under test. A failure here means sys.exit(1), never a raised
exception escaping to a caller.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "claimcheck"
ERROR_SUFFIXES = ("Error", "Exception")
PROPAGATES_MARKER = "PROPAGATES:"


def _base_name(base: ast.expr) -> str | None:
    """Return the simple name of a class base expression, if it has one."""
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return None


def _is_exception_class(node: ast.ClassDef) -> bool:
    return any(
        (name := _base_name(base)) is not None and name.endswith(ERROR_SUFFIXES)
        for base in node.bases
    )


def _declares_propagation(node: ast.ClassDef) -> bool:
    docstring = ast.get_docstring(node) or ""
    return PROPAGATES_MARKER in docstring


def _find_declared_exceptions(tree: ast.Module) -> list[ast.ClassDef]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and _is_exception_class(node)
    ]


def _find_handled_names(tree: ast.Module) -> set[str]:
    handled: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or node.type is None:
            continue
        types = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
        for exc_type in types:
            name = _base_name(exc_type)
            if name is not None:
                handled.add(name)
    return handled


def main() -> int:
    """Check every declared exception type is handled or marked propagating."""
    declared: list[tuple[str, ast.ClassDef]] = []
    handled: set[str] = set()

    for path in sorted(SRC_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        declared.extend((path.name, node) for node in _find_declared_exceptions(tree))
        handled |= _find_handled_names(tree)

    unhandled = [
        f"{filename}: {node.name}"
        for filename, node in declared
        if node.name not in handled and not _declares_propagation(node)
    ]

    if unhandled:
        print("Exception types declared but neither caught nor marked PROPAGATES:")
        for entry in unhandled:
            print(f"  - {entry}")
        return 1

    print(f"exceptions: clean ({len(declared)} declared exception type(s) checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
