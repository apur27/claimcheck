"""Eval runner: scores a checker against `data/labelled_claims.jsonl`.

Loads the labelled set, reconstructs a `Claim` per row from the row's own `file`/`line`/
`claim_text`/`shape` -- never re-runs extraction against the source repos, since the labelled
row already carries what extraction would have produced -- resolves `source_repo` to a real
repo root on disk, and calls a caller-supplied checker (`Callable[[Claim, Path], Verdict]`) on
each. Also hosts two trivial checkers, `null_checker` and `empty_checker`, used only to prove
the scoring path in `domain/scorer.py` is falsifiable (`make check-falsify`).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from claimcheck.domain.models import Claim
from claimcheck.domain.scorer import PredictionPair
from claimcheck.domain.verifiers import Verdict

CheckerFn = Callable[[Claim, Path], Verdict]

DATA_PATH = Path(__file__).resolve().parents[3] / "data" / "labelled_claims.jsonl"

# Read-only checkouts of the three corpora `data/labelled_claims.jsonl` rows point into --
# never copied into this repo, per the brief.
REPO_ROOTS: dict[str, Path] = {
    "tomoro-task": Path("/home/abhi/git/tomoro-task"),
    "llmRun": Path("/home/abhi/git/llmRun"),
    "rainmaker": Path("/home/abhi/git/rainmaker"),
}

_MARKDOWN_SHAPE = "markdown_reference"


class UnknownSourceRepoError(ValueError):
    """A labelled row names a `source_repo` outside `REPO_ROOTS`.

    PROPAGATES: no handler exists. A `source_repo` missing from the mapping means the labelled
    set and this runner have drifted apart -- a data/config defect to fix at the source, not a
    runtime condition an eval run can recover from -- so this terminates the run.
    """

    def __init__(self, row_id: str, source_repo: str) -> None:
        super().__init__(f"{row_id}: unknown source_repo {source_repo!r}")


@dataclass(frozen=True)
class LabelledRow:
    """One row of `data/labelled_claims.jsonl`, narrowed to the fields this runner needs."""

    id: str
    source_repo: str
    file: str
    line: int
    claim_text: str
    shape: str
    reason: str


def _load_labelled_rows(data_path: Path) -> list[LabelledRow]:
    rows: list[LabelledRow] = []
    for line in data_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        rows.append(
            LabelledRow(
                id=str(raw["id"]),
                source_repo=str(raw["source_repo"]),
                file=str(raw["file"]),
                line=int(raw["line"]),
                claim_text=str(raw["claim_text"]),
                shape=str(raw["shape"]),
                reason=str(raw["reason"]),
            )
        )
    return rows


def _infer_source(shape: str) -> str:
    """Best-effort `Claim.source` for a labelled row, which does not record it directly.

    No deterministic verifier reads `Claim.source` (checked directly: none of the four
    functions in `domain/verifiers.py` reference it), so this only needs to be plausible, not
    a byte-exact reconstruction of what extraction originally produced.
    """
    return "markdown" if shape == _MARKDOWN_SHAPE else "docstring"


def run_eval(checker: CheckerFn, data_path: Path = DATA_PATH) -> list[PredictionPair]:
    """Run `checker` over every row of `data_path` and return (predicted, labelled) pairs."""
    pairs: list[PredictionPair] = []
    for row in _load_labelled_rows(data_path):
        repo_root = REPO_ROOTS.get(row.source_repo)
        if repo_root is None:
            raise UnknownSourceRepoError(row.id, row.source_repo)
        claim = Claim(
            file=row.file,
            line=row.line,
            claim_text=row.claim_text,
            shape=row.shape,
            source=_infer_source(row.shape),
        )
        verdict = checker(claim, repo_root)
        pairs.append(PredictionPair(predicted=verdict.reason, labelled=row.reason))
    return pairs


def null_checker(claim: Claim, repo_root: Path) -> Verdict:
    """Always reports `contradicted`, regardless of input.

    Exists only to falsify the scoring path (`make check-falsify`): flagging every claim
    should drive precision down to the labelled contradiction rate and recall up to 1.0, since
    every labelled-contradicted claim is necessarily among the ones it flags.
    """
    del claim, repo_root  # intentionally input-independent
    return Verdict("contradicted", "null_checker: always reports contradicted")


def empty_checker(claim: Claim, repo_root: Path) -> Verdict:
    """Always reports `ok`, regardless of input -- never flags a contradiction.

    Exists only to falsify the scoring path (`make check-falsify`): flagging nothing should
    drive recall to 0.0, since it never catches any of the labelled-contradicted claims.
    """
    del claim, repo_root  # intentionally input-independent
    return Verdict("ok", "empty_checker: never reports contradicted")
