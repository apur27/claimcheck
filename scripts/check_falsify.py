"""Falsification check for the scoring path (`make check-falsify`).

Runs the same scorer through three checkers -- the real deterministic `verify()`, a null
checker that flags every claim, and an empty checker that flags none -- through the identical
scoring path (`services/eval.run_eval` -> `domain/scorer.score`). If `null_checker` and
`empty_checker` do not show the mathematically expected pattern, the scoring path itself is
broken and no number it reports about the real checker can be trusted.

PROPAGATES: none by design -- every condition this script can hit is turned into a printed
reason and `sys.exit(1)`; nothing here is imported by the package under test.
"""

from __future__ import annotations

import math
import sys

from claimcheck.domain.scorer import ScoreResult, score
from claimcheck.domain.verifiers import verify
from claimcheck.services.eval import empty_checker, null_checker, run_eval

# Frozen by hand-count against data/labelled_claims.jsonl (see tests/test_labelled_claims.py).
_FROZEN_TOTAL_CLAIMS = 44
_FROZEN_CONTRADICTED_COUNT = 7
_EXPECTED_NULL_RECALL = 1.0
_EXPECTED_EMPTY_RECALL = 0.0
_EXPECTED_NULL_PRECISION = _FROZEN_CONTRADICTED_COUNT / _FROZEN_TOTAL_CLAIMS
_PRECISION_TOLERANCE = 1e-9


def _format(label: str, result: ScoreResult) -> str:
    precision_text = (
        f"{result.precision:.4f}" if result.precision is not None else "undefined (0 findings)"
    )
    recall_text = (
        f"{result.recall:.4f}"
        if result.recall is not None
        else "undefined (0 labelled-contradicted)"
    )
    return (
        f"{label}: precision={precision_text} ({result.true_positives}/{result.findings}), "
        f"recall={recall_text} ({result.true_positives}/{result.labelled_contradicted})"
    )


def main() -> int:
    """Print all three checkers' scores and fail loudly if the falsification pattern breaks."""
    real_result = score(run_eval(verify))
    print(_format("verify()      ", real_result))

    null_result = score(run_eval(null_checker))
    print(_format("null_checker  ", null_result))

    empty_result = score(run_eval(empty_checker))
    print(_format("empty_checker ", empty_result))

    failures: list[str] = []

    if null_result.recall != _EXPECTED_NULL_RECALL:
        failures.append(
            f"null_checker recall was {null_result.recall}, expected {_EXPECTED_NULL_RECALL} "
            "(it flags everything, so it can never miss a labelled-contradicted claim)"
        )
    if null_result.precision is None or not math.isclose(
        null_result.precision, _EXPECTED_NULL_PRECISION, abs_tol=_PRECISION_TOLERANCE
    ):
        failures.append(
            f"null_checker precision was {null_result.precision}, expected "
            f"{_EXPECTED_NULL_PRECISION:.4f} (the labelled contradiction rate, "
            f"{_FROZEN_CONTRADICTED_COUNT}/{_FROZEN_TOTAL_CLAIMS})"
        )

    if empty_result.recall != _EXPECTED_EMPTY_RECALL:
        failures.append(
            f"empty_checker recall was {empty_result.recall}, expected {_EXPECTED_EMPTY_RECALL} "
            "(it never flags anything, so it can never catch a labelled-contradicted claim)"
        )
    if empty_result.findings != 0 or empty_result.precision is not None:
        failures.append(
            f"empty_checker found {empty_result.findings} findings with precision "
            f"{empty_result.precision}, expected 0 findings and undefined precision"
        )

    if failures:
        print("check-falsify: FAILED")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("check-falsify: passed -- scoring path proven falsifiable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
