"""Command-line wiring for claimcheck.

Hosts `claimcheck src/`: parses input, calls `services.extract` and
`domain.verifiers`, formats output -- no business logic lives here. Never
imports `claimcheck.adapters`: this entry point works with no network and no
`ANTHROPIC_API_KEY`, since only the four deterministic verifier shapes are
checked this session.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from claimcheck.domain.models import VALID_SHAPES, Claim
from claimcheck.domain.verifiers import verify
from claimcheck.services.extract import extract_claims, extract_claims_from_paths

_DETERMINISTIC_SHAPES = VALID_SHAPES - {"other"}
_DIFF_SOURCE_SUFFIXES = (".py", ".md")
_GIT_NOT_FOUND_DETAIL = "git is not installed or not on PATH"


class GitUnavailableError(Exception):
    """`--diff` could not consult git: no repo here, or `git` is not installed.

    Always caught in `_run_diff`, which prints a one-line message and
    returns a non-zero exit code -- never raised out of `main()`.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(f"--diff needs git: {detail}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claimcheck",
        description="Find claims in docstrings/comments/markdown that the code contradicts.",
    )
    parser.add_argument("path", help="repo or directory to scan")
    parser.add_argument(
        "--diff",
        action="store_true",
        help="scan only claims in files with staged changes (git diff --cached)",
    )
    return parser


def main() -> int:
    """Entry point registered as the `claimcheck` console script.

    Extracts claims under `args.path` (or, with `--diff`, only claims in
    files touched by the staged diff), verifies the deterministic shapes
    and prints every `contradicted` finding. Returns 0 for a completed scan
    (finding contradictions is the tool doing its job, not a failure) and
    non-zero only when the scan itself could not run, e.g. a missing path.
    """
    args = _build_parser().parse_args()
    repo_root = Path(args.path)
    if not repo_root.is_dir():
        print(f"claimcheck: {repo_root} is not a directory", file=sys.stderr)
        return 1
    if args.diff:
        return _run_diff(repo_root)

    claims = extract_claims(repo_root)
    return _report(claims, repo_root)


def _run_diff(path: Path) -> int:
    """Verify only claims in files touched by the currently staged diff."""
    try:
        repo_root = _git_toplevel(path)
        staged = _staged_source_files(repo_root)
    except GitUnavailableError as exc:
        print(f"claimcheck: {exc}", file=sys.stderr)
        return 1
    if not staged:
        print("claimcheck: no staged changes")
        return 0
    claims = extract_claims_from_paths(repo_root, staged)
    return _report(claims, repo_root)


def _git_toplevel(path: Path) -> Path:
    """Return the top-level directory of the git repo containing `path`.

    `git diff --cached --name-only` reports paths relative to the repo's
    top level, not the current working directory, so callers need this to
    resolve those paths back to real files.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitUnavailableError(_GIT_NOT_FOUND_DETAIL) from exc
    if result.returncode != 0:
        message = result.stderr.strip() or f"{path} is not a git repository"
        raise GitUnavailableError(message)
    return Path(result.stdout.strip())


def _staged_source_files(repo_root: Path) -> list[Path]:
    """Return `.py`/`.md` files with staged changes, as absolute paths."""
    result = subprocess.run(
        ["git", "-C", str(repo_root), "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or "git diff --cached failed"
        raise GitUnavailableError(message)
    files = []
    for line in result.stdout.splitlines():
        rel_path = line.strip()
        if not rel_path:
            continue
        candidate = repo_root / rel_path
        if candidate.suffix in _DIFF_SOURCE_SUFFIXES and candidate.is_file():
            files.append(candidate)
    return files


def _report(claims: list[Claim], repo_root: Path) -> int:
    """Verify `claims` against `repo_root` and print every contradicted finding."""
    contradicted = 0
    skipped_other = 0
    for claim in claims:
        if claim.shape not in _DETERMINISTIC_SHAPES:
            skipped_other += 1
            continue
        verdict = verify(claim, repo_root)
        if verdict.reason == "contradicted":
            contradicted += 1
            print(f"{claim.file}:{claim.line}: {claim.claim_text}\n  -> {verdict.evidence}\n")

    checked = len(claims) - skipped_other
    print(
        f"claimcheck: {len(claims)} claim(s) found, {checked} checked, "
        f"{skipped_other} skipped (shape 'other', no deterministic verifier), "
        f"{contradicted} contradicted"
    )
    return 0
