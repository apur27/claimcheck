"""Pure claim domain model.

No I/O, no vendor SDK, no clock -- testable with nothing but Python. A
`Claim` is an assertion extracted from prose (a docstring, a comment or a
markdown paragraph), not yet checked against the code it describes.

Field names and the `shape` vocabulary match `data/labelled_claims.jsonl`
(slice 1's frozen ground truth) so slice 3's verifiers and slice 4's scorer
can compare extracted claims against the labelled set with no translation
layer.
"""

from __future__ import annotations

from dataclasses import dataclass

VALID_SHAPES = frozenset(
    {"raises_propagates", "defaults_to", "returns_type", "markdown_reference", "other"}
)
VALID_SOURCES = frozenset({"docstring", "comment", "markdown"})


@dataclass(frozen=True)
class Claim:
    """A single claim extracted from prose, not yet checked against the code.

    Attributes:
        file: path relative to the repo root being scanned.
        line: 1-indexed line number the claim's text starts on.
        claim_text: the prose making the claim.
        shape: one of `VALID_SHAPES` -- which deterministic verifier, if
            any, can check this claim.
        source: one of `VALID_SOURCES` -- where the claim was found.
    """

    file: str
    line: int
    claim_text: str
    shape: str
    source: str
