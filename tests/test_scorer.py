"""Tests for `domain/scorer.py` and the eval path that drives it.

Synthetic-pair tests below are hand-computed by counting the listed pairs on paper, not derived
from the scorer itself -- a test computing its expected value the same way the code under test
does cannot detect the code changing (`rainmaker/runs/TRAPS.md`).

The real-`verify()` test asserts against the actual observed numbers from running `verify()`
over the full `data/labelled_claims.jsonl` set, measured once by hand and pinned as literals
here -- the same discipline `tests/test_labelled_claims.py` uses for its frozen counts. The
observed result is TP=2, FP=3, FN=5 (findings=5, labelled_contradicted=7).

This corrects an earlier pinning of TP=0/FP=2/FN=7 that was traced to a real bug, not a
fundamental limit of single-file AST checks: `_find_exception_name`'s docstring-ownership check
used exact-line equality against a docstring's *first* line, so a claim's `line` pointing anywhere
else inside a multi-line docstring (as most hand-labelled `raises_propagates` claims do) missed
the docstring entirely and short-circuited to `unverifiable` before the repo-wide handler search
ever ran. Fixed to a span check (`_docstring_spans_line`); lc-001 and lc-002 now correctly resolve
`contradicted`. lc-007 remains `unverifiable` for a different, real reason: its docstring
describes what a function *catches*, not what it raises, and `_find_exception_name`'s
function-docstring branch only looks for the function's own `raise` statements. The fix also
surfaces a new false positive, lc-009 (labelled `ok`): its exception-class docstring says a
handler exists elsewhere in the codebase (by design, not by omission), but `verify_raises_
propagates` cannot distinguish "no handler exists" claims from "a handler exists over there"
claims -- both are class docstrings near an exception class with a handler found somewhere in the
repo, so both resolve `contradicted`. That is a real, separate limitation of the verifier's
claim-polarity blindness, out of scope for the line-matching fix that produced this measurement.
"""

from pathlib import Path

from claimcheck.domain.scorer import PredictionPair, score
from claimcheck.domain.verifiers import verify
from claimcheck.services.eval import empty_checker, null_checker, run_eval

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "labelled_claims.jsonl"

# Frozen by hand-count against data/labelled_claims.jsonl, matching test_labelled_claims.py.
FROZEN_CLAIM_COUNT = 44
FROZEN_CONTRADICTED_COUNT = 7

# Hand-counted on paper against the 5 pairs listed in the test below.
_HAND_TRUE_POSITIVES = 2
_HAND_FALSE_POSITIVES = 1
_HAND_FALSE_NEGATIVES = 1
_HAND_FINDINGS = 3
_HAND_LABELLED_CONTRADICTED = 3

# Observed once from a real `verify()` run over the full labelled set -- see module docstring.
_OBSERVED_VERIFY_TRUE_POSITIVES = 2
_OBSERVED_VERIFY_FALSE_POSITIVES = 3
_OBSERVED_VERIFY_FALSE_NEGATIVES = 5
_OBSERVED_VERIFY_FINDINGS = 5


def test_score_hand_computed_mixed_pairs() -> None:
    """5 pairs, hand-counted: TP=2, FP=1, FN=1, one `ok`/`ok` pair contributes to neither."""
    pairs = [
        PredictionPair(predicted="contradicted", labelled="contradicted"),  # TP
        PredictionPair(predicted="contradicted", labelled="ok"),  # FP
        PredictionPair(predicted="contradicted", labelled="contradicted"),  # TP
        PredictionPair(predicted="ok", labelled="contradicted"),  # FN
        PredictionPair(predicted="unverifiable", labelled="ok"),  # neither
    ]
    result = score(pairs)
    assert result.true_positives == _HAND_TRUE_POSITIVES
    assert result.false_positives == _HAND_FALSE_POSITIVES
    assert result.false_negatives == _HAND_FALSE_NEGATIVES
    assert result.findings == _HAND_FINDINGS
    assert result.labelled_contradicted == _HAND_LABELLED_CONTRADICTED
    assert result.precision == _HAND_TRUE_POSITIVES / _HAND_FINDINGS
    assert result.recall == _HAND_TRUE_POSITIVES / _HAND_LABELLED_CONTRADICTED


def test_score_unparsed_counts_as_false_negative_when_labelled_contradicted() -> None:
    """`unparsed` is never excluded from the recall denominator -- explicit in the brief."""
    pairs = [PredictionPair(predicted="unparsed", labelled="contradicted")]
    result = score(pairs)
    assert result.true_positives == 0
    assert result.false_negatives == 1
    assert result.findings == 0
    assert result.labelled_contradicted == 1
    assert result.precision is None
    assert result.recall == 0.0


def test_score_precision_undefined_with_zero_findings() -> None:
    """Zero findings is 0-over-0 -- `None`, not silently `0.0` and not a crash."""
    pairs = [
        PredictionPair(predicted="ok", labelled="ok"),
        PredictionPair(predicted="unverifiable", labelled="ok"),
    ]
    result = score(pairs)
    assert result.findings == 0
    assert result.precision is None


def test_score_recall_undefined_with_zero_labelled_contradicted() -> None:
    """Zero labelled-contradicted claims is 0-over-0 for recall -- `None`, not `0.0`."""
    pairs = [PredictionPair(predicted="ok", labelled="ok")]
    result = score(pairs)
    assert result.labelled_contradicted == 0
    assert result.recall is None


def test_score_empty_pairs_reports_no_findings_and_no_labelled_contradicted() -> None:
    result = score([])
    assert result.findings == 0
    assert result.labelled_contradicted == 0
    assert result.precision is None
    assert result.recall is None


# --- real verify() against data/labelled_claims.jsonl -----------------------


def test_real_verify_against_labelled_claims_matches_observed_measurement() -> None:
    """Pinned to the actual observed run over the full labelled set -- not a round number.

    See module docstring for why `verify()` scores this low: single-file AST heuristics vs.
    multi-file evidence chains in several hand-labelled `contradicted` claims.
    """
    result = score(run_eval(verify, DATA_PATH))
    assert result.true_positives == _OBSERVED_VERIFY_TRUE_POSITIVES
    assert result.false_positives == _OBSERVED_VERIFY_FALSE_POSITIVES
    assert result.false_negatives == _OBSERVED_VERIFY_FALSE_NEGATIVES
    assert result.findings == _OBSERVED_VERIFY_FINDINGS
    assert result.labelled_contradicted == FROZEN_CONTRADICTED_COUNT
    assert result.precision == _OBSERVED_VERIFY_TRUE_POSITIVES / _OBSERVED_VERIFY_FINDINGS
    assert result.recall == _OBSERVED_VERIFY_TRUE_POSITIVES / FROZEN_CONTRADICTED_COUNT


# --- falsification checkers scored through the same path --------------------


def test_null_checker_scores_precision_at_chance_and_full_recall() -> None:
    """Flags every claim: precision falls to the labelled contradiction rate, recall is 1.0."""
    result = score(run_eval(null_checker, DATA_PATH))
    assert result.findings == FROZEN_CLAIM_COUNT
    assert result.true_positives == FROZEN_CONTRADICTED_COUNT
    assert result.recall == 1.0
    assert result.precision == FROZEN_CONTRADICTED_COUNT / FROZEN_CLAIM_COUNT


def test_empty_checker_scores_zero_recall_and_undefined_precision() -> None:
    """Flags nothing: recall is 0.0 and precision is undefined (0 findings), not a crash."""
    result = score(run_eval(empty_checker, DATA_PATH))
    assert result.findings == 0
    assert result.true_positives == 0
    assert result.recall == 0.0
    assert result.precision is None
