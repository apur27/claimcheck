"""Command-line wiring for claimcheck.

Hosts `claimcheck src/`: parses input, calls `services.extract` and
`domain.verifiers`, formats output -- no business logic lives here. Never
imports `claimcheck.adapters`: this entry point works with no network and no
`ANTHROPIC_API_KEY`, since only the four deterministic verifier shapes are
checked this session.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from claimcheck.domain.models import VALID_SHAPES
from claimcheck.domain.verifiers import verify
from claimcheck.services.extract import extract_claims

_DETERMINISTIC_SHAPES = VALID_SHAPES - {"other"}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claimcheck",
        description="Find claims in docstrings/comments/markdown that the code contradicts.",
    )
    parser.add_argument("path", help="repo or directory to scan")
    parser.add_argument(
        "--diff",
        action="store_true",
        help="scan only staged changes (not yet implemented, lands session 2)",
    )
    return parser


def main() -> int:
    """Entry point registered as the `claimcheck` console script.

    Extracts claims under `args.path`, verifies the deterministic shapes and
    prints every `contradicted` finding. Returns 0 for a completed scan
    (finding contradictions is the tool doing its job, not a failure) and
    non-zero only when the scan itself could not run, e.g. a missing path.
    """
    args = _build_parser().parse_args()
    repo_root = Path(args.path)
    if not repo_root.is_dir():
        print(f"claimcheck: {repo_root} is not a directory", file=sys.stderr)
        return 1
    if args.diff:
        print("claimcheck: --diff is not yet implemented, lands session 2", file=sys.stderr)
        return 1

    claims = extract_claims(repo_root)
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
