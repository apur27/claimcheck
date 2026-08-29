"""One-off live measurement: model-backed verifier vs. deterministic-only baseline.

Runs `verify_with_model` against every labelled claim where the deterministic `verify()`
currently returns `unverifiable` -- not only `shape == "other"`: some `raises_propagates`,
`defaults_to` and `returns_type` claims fall through to `unverifiable` too, when the AST context
they need isn't found near the claimed line. Uses the real `AnthropicClient` -- the one live API
call this session's $2 budget is for. Scores three ways through the same `domain/scorer.score`
path the deterministic-only baseline already used: deterministic-only, model-only (over the
subset it actually judged) and combined (deterministic verdict kept where it isn't
`unverifiable`, model verdict used where it is). Prints real dollar spend read from
`AnthropicClient.total_cost_usd` -- built from actual per-call token usage, never estimated.

PROPAGATES: nothing declared here. `BudgetExceededError` from `adapters.anthropic_client` is
caught explicitly below to stop the loop and report an honest count of claims skipped for
budget rather than let the run crash partway through.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from claimcheck.adapters.anthropic_client import AnthropicClient, BudgetExceededError
from claimcheck.domain.models import Claim
from claimcheck.domain.scorer import PredictionPair, ScoreResult, score
from claimcheck.domain.verifiers import verify
from claimcheck.services.eval import (
    DATA_PATH,
    REPO_ROOTS,
    UnknownSourceRepoError,
    _infer_source,
    _load_labelled_rows,
)
from claimcheck.services.model_verify import verify_with_model


@dataclass(frozen=True)
class _Resolved:
    """One labelled row, already turned into a `Claim` plus the repo root it lives under."""

    row_id: str
    claim: Claim
    repo_root: Path
    labelled_reason: str


def _resolve_rows() -> list[_Resolved]:
    resolved: list[_Resolved] = []
    for row in _load_labelled_rows(DATA_PATH):
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
        resolved.append(_Resolved(row.id, claim, repo_root, row.reason))
    return resolved


def _format_score(label: str, result: ScoreResult) -> str:
    precision = f"{result.precision:.4f}" if result.precision is not None else "undefined"
    recall = f"{result.recall:.4f}" if result.recall is not None else "undefined"
    return (
        f"{label}: TP={result.true_positives} FP={result.false_positives} "
        f"FN={result.false_negatives} precision={precision} recall={recall}"
    )


def main() -> int:
    rows = _resolve_rows()
    deterministic_pairs = [
        PredictionPair(predicted=verify(r.claim, r.repo_root).reason, labelled=r.labelled_reason)
        for r in rows
    ]
    deterministic_only = score(deterministic_pairs)
    print(f"run_model_eval: {len(rows)} labelled claims")
    print(_format_score("deterministic-only", deterministic_only))

    unresolved = [
        (index, r)
        for index, (r, pair) in enumerate(zip(rows, deterministic_pairs, strict=True))
        if pair.predicted == "unverifiable"
    ]
    print(f"run_model_eval: {len(unresolved)} claim(s) deterministic verify() could not settle")

    client = AnthropicClient()
    combined_pairs = list(deterministic_pairs)
    model_pairs: list[PredictionPair] = []
    judged_ids: list[str] = []
    skipped_ids: list[str] = []
    budget_exhausted = False

    for index, r in unresolved:
        if budget_exhausted:
            skipped_ids.append(r.row_id)
            continue
        try:
            model_verdict = verify_with_model(r.claim, r.repo_root, client)
        except BudgetExceededError as exc:
            print(f"run_model_eval: stopping -- {exc}")
            budget_exhausted = True
            skipped_ids.append(r.row_id)
            continue
        model_pair = PredictionPair(predicted=model_verdict.reason, labelled=r.labelled_reason)
        model_pairs.append(model_pair)
        combined_pairs[index] = model_pair
        judged_ids.append(r.row_id)

    model_only = score(model_pairs)
    combined = score(combined_pairs)

    print(f"run_model_eval: judged {len(judged_ids)} claim(s): {judged_ids}")
    if skipped_ids:
        print(f"run_model_eval: skipped {len(skipped_ids)} claim(s) for budget: {skipped_ids}")
    print(_format_score("model-only (judged subset)", model_only))
    print(_format_score("combined (deterministic + model)", combined))
    print(
        f"run_model_eval: real spend ${client.total_cost_usd:.4f} across "
        f"{client.calls_made} call(s), cap ${client.budget_usd:.2f}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
