"""Pure precision/recall scorer for checker output against labelled ground truth.

No I/O, no vendor SDK, no clock -- takes only in-memory (predicted, labelled) reason pairs and
returns counts. A **finding** is a claim the checker reported as `contradicted`. Precision and
recall are reported separately, with their own denominators, never blended into one score -- the
frozen METRIC this scorer implements. A claim the checker returns `unparsed` for still counts as
a false negative when labelled `contradicted`; it is never excluded from either denominator.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

CONTRADICTED = "contradicted"


@dataclass(frozen=True)
class PredictionPair:
    """One claim's checker output paired with its labelled ground truth.

    Attributes:
        predicted: the reason code a checker returned (`Verdict.reason`).
        labelled: the hand-assigned ground-truth reason for the same claim.
    """

    predicted: str
    labelled: str


@dataclass(frozen=True)
class ScoreResult:
    """Precision and recall over a set of `PredictionPair`s, each with its own denominator.

    Attributes:
        true_positives: checker said `contradicted`, label says `contradicted`.
        false_positives: checker said `contradicted`, label says something else.
        false_negatives: label says `contradicted`, checker did not say `contradicted`
            (includes `unparsed` -- it is never excluded from this count).
        findings: total claims the checker flagged `contradicted` -- precision's denominator.
        labelled_contradicted: total labelled-`contradicted` claims -- recall's denominator.
        precision: `true_positives / findings`, or `None` when `findings == 0` (0-over-0 is
            undefined, not zero).
        recall: `true_positives / labelled_contradicted`, or `None` when
            `labelled_contradicted == 0`.
    """

    true_positives: int
    false_positives: int
    false_negatives: int
    findings: int
    labelled_contradicted: int
    precision: float | None
    recall: float | None


def score(pairs: Sequence[PredictionPair]) -> ScoreResult:
    """Score `pairs` into a `ScoreResult` with separate precision/recall denominators."""
    true_positives = 0
    false_positives = 0
    false_negatives = 0
    for pair in pairs:
        predicted_contradicted = pair.predicted == CONTRADICTED
        labelled_contradicted = pair.labelled == CONTRADICTED
        if predicted_contradicted and labelled_contradicted:
            true_positives += 1
        elif predicted_contradicted and not labelled_contradicted:
            false_positives += 1
        elif labelled_contradicted and not predicted_contradicted:
            false_negatives += 1

    findings = true_positives + false_positives
    labelled_contradicted_total = true_positives + false_negatives
    precision = true_positives / findings if findings else None
    recall = true_positives / labelled_contradicted_total if labelled_contradicted_total else None

    return ScoreResult(
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        findings=findings,
        labelled_contradicted=labelled_contradicted_total,
        precision=precision,
        recall=recall,
    )
