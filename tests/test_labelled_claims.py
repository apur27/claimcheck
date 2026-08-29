"""Tests for the hand-built labelled claims set.

This is the ground truth the checker will be scored against, built and committed before any
verifier exists. The counts below are frozen: hand-counted once against `data/labelled_claims.jsonl`
and asserted as literal integers, never recomputed from the file and compared to itself -- a test
that redefines the constant it defends cannot detect it changing (`rainmaker/runs/TRAPS.md`).

Real numbers as counted by hand on 2026-08-29: 44 claims total, 7 labelled `contradicted`. The
brief this set was built against asked for at least 10 genuinely contradicted claims; exhaustive,
documented verification against the three source corpora (tomoro-task, llmRun, rainmaker) found
only 7 that are *currently* true of the code on disk -- three of the four named historical
instances had already been fixed by the time this set was built, and the two solution repos in
particular are unusually well-reviewed. The floor asserted here is the real, hand-verified count,
not the requested one; see the engineer's slice report for the full accounting.
"""

import json
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "labelled_claims.jsonl"

REQUIRED_FIELDS = frozenset(
    {
        "id",
        "source_repo",
        "file",
        "line",
        "claim_kind",
        "claim_text",
        "reason",
        "evidence",
        "shape",
    }
)
ALLOWED_REASONS = frozenset({"ok", "contradicted", "unverifiable", "unparsed"})
ALLOWED_SOURCE_REPOS = frozenset({"tomoro-task", "llmRun", "rainmaker"})

# Frozen by hand-count against the committed file. Never derived from len(claims) below.
FROZEN_CLAIM_COUNT = 44
FROZEN_CONTRADICTED_COUNT = 7


def _load_claims() -> list[dict[str, object]]:
    lines = DATA_PATH.read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_every_line_parses_as_json_with_required_fields() -> None:
    """Every row is valid JSONL and carries every required field with no nulls."""
    claims = _load_claims()
    assert claims, "labelled_claims.jsonl must not be empty"
    for claim in claims:
        missing = REQUIRED_FIELDS - claim.keys()
        assert not missing, f"{claim.get('id', '?')} is missing fields: {missing}"
        for field_name, value in claim.items():
            assert value is not None, f"{claim.get('id', '?')}.{field_name} is null"


def test_every_reason_is_in_the_allowed_enum() -> None:
    """`reason` is always one of ok/contradicted/unverifiable/unparsed -- no ad-hoc values."""
    claims = _load_claims()
    for claim in claims:
        assert claim["reason"] in ALLOWED_REASONS, (
            f"{claim['id']} has reason {claim['reason']!r}, not in {sorted(ALLOWED_REASONS)}"
        )


def test_frozen_claim_count() -> None:
    """The labelled set holds exactly the hand-counted number of claims, pinned as a literal."""
    claims = _load_claims()
    assert len(claims) == FROZEN_CLAIM_COUNT


def test_frozen_contradicted_count() -> None:
    """The `contradicted` label count is pinned as a literal, hand-counted against the real file.

    Not `>= FROZEN_CONTRADICTED_COUNT` and not recomputed from the data being asserted against --
    an equality check against a number counted by hand is what actually pins it (see module
    docstring: this is the exact failure mode `runs/TRAPS.md` names).
    """
    claims = _load_claims()
    contradicted = [c for c in claims if c["reason"] == "contradicted"]
    assert len(contradicted) == FROZEN_CONTRADICTED_COUNT


def test_every_source_repo_is_one_of_the_three_corpora() -> None:
    """Every claim traces to tomoro-task, llmRun or rainmaker -- no stray fourth source."""
    claims = _load_claims()
    for claim in claims:
        assert claim["source_repo"] in ALLOWED_SOURCE_REPOS, (
            f"{claim['id']} has source_repo {claim['source_repo']!r}, "
            f"not in {sorted(ALLOWED_SOURCE_REPOS)}"
        )


def test_no_duplicate_file_and_line_claims() -> None:
    """No two claims point at the same (file, line) -- every claim is a distinct instance."""
    claims = _load_claims()
    pairs = [(c["file"], c["line"]) for c in claims]
    duplicates = {pair for pair in pairs if pairs.count(pair) > 1}
    assert not duplicates, f"duplicate (file, line) claims: {duplicates}"


def test_every_claim_id_is_unique() -> None:
    """Ids are the join key for future scoring output -- collisions would corrupt it."""
    claims = _load_claims()
    ids = [c["id"] for c in claims]
    assert len(ids) == len(set(ids))
