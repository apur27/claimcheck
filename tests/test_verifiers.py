"""Tests for the four deterministic verifiers.

Primary source of truth is `tests/fixtures/sample_repo/` -- the same fixture
`extract_claims` is tested against, extended in this slice with a few more
seeded cases so every shape has at least one `ok` and one `contradicted`
case. `verify` (and each of the four verifiers) is pure `ast` -- no
`import`/`exec` of the scanned code, matching `services/extract.py`.

The `pkg/module_a.py` docstring is the brief's own `WORKING` example: a
`PROPAGATES: no handler exists` claim about `CustomError`, while
`pkg/module_c.py` -- three files away -- actually handles it. That case
must resolve `contradicted`; this is checked directly below both before the
verifier logic exists (RED) and after (GREEN).
"""

from pathlib import Path

import pytest

from claimcheck.domain.models import Claim
from claimcheck.domain.verifiers import (
    Verdict,
    verify,
    verify_defaults_to,
    verify_markdown_reference,
    verify_raises_propagates,
    verify_returns_type,
)

FIXTURE_REPO = Path(__file__).resolve().parent / "fixtures" / "sample_repo"
RAINMAKER_REPO = Path("/home/abhi/git/rainmaker")
TOMORO_TASK_REPO = Path("/home/abhi/git/tomoro-task")

# The RainMaker cross-check tests below read a sibling repo that only exists on
# a RainMaker orchestrator's own machine -- not vendored or fixtured here, so a
# clean checkout (CI, another contributor) has no such path. Skip rather than
# fail: these are a bonus sanity check against real hand-labelled data, not
# part of this package's own coverage (that lives in the fixture-repo tests
# above and in tests/test_scorer.py's run against data/labelled_claims.jsonl).
requires_rainmaker_repo = pytest.mark.skipif(
    not RAINMAKER_REPO.is_dir(),
    reason="cross-check reads a sibling rainmaker checkout not vendored in this repo",
)

# Same rationale as requires_rainmaker_repo, but for the sibling repo lc-001's
# docstring-span regression case (and its labelled raises_propagates siblings)
# were found against -- only present on a machine that has cloned it.
requires_tomoro_task_repo = pytest.mark.skipif(
    not TOMORO_TASK_REPO.is_dir(),
    reason="cross-check reads a sibling tomoro-task checkout not vendored in this repo",
)


def _claim(**overrides: object) -> Claim:
    defaults: dict[str, object] = {
        "file": "pkg/module_a.py",
        "line": 2,
        "claim_text": "",
        "shape": "raises_propagates",
        "source": "docstring",
    }
    defaults.update(overrides)
    return Claim(**defaults)  # type: ignore[arg-type]


# --- verify_raises_propagates ----------------------------------------------


def test_raises_propagates_contradicted_when_handler_exists_three_files_away() -> None:
    """The brief's own `WORKING` example: `CustomError` is handled in `module_c.py`."""
    claim = _claim(
        file="pkg/module_a.py",
        line=2,
        claim_text="PROPAGATES: no handler exists in this module for CustomError.",
        shape="raises_propagates",
        source="docstring",
    )
    verdict = verify_raises_propagates(claim, FIXTURE_REPO)
    assert verdict.reason == "contradicted"
    assert "module_c.py" in verdict.evidence


def test_raises_propagates_ok_when_no_handler_exists_anywhere() -> None:
    """`LonelyError` is genuinely never caught anywhere in the fixture repo."""
    claim = _claim(
        file="pkg/module_d.py",
        line=2,
        claim_text="PROPAGATES: no handler exists anywhere for LonelyError.",
        shape="raises_propagates",
        source="docstring",
    )
    verdict = verify_raises_propagates(claim, FIXTURE_REPO)
    assert verdict.reason == "ok"


@requires_tomoro_task_repo
def test_raises_propagates_contradicted_when_claim_line_is_inside_docstring_span() -> None:
    """lc-001: regression for a docstring-ownership check that used exact-line equality.

    `FixtureMissError`'s docstring opens at line 19; the labelled claim's `line: 21` points at
    the specific sentence inside that multi-line docstring ("PROPAGATES: ..."), not its first
    line. The ownership check must treat any line within the docstring's span as belonging to
    it, or this claim is wrongly reported `unverifiable` and the real, repo-wide handler search
    (which does find `src/main.py:99`) never runs.
    """
    claim = _claim(
        file="src/adapters/fixture_client.py",
        line=21,
        claim_text="PROPAGATES: no handler exists in this slice.",
        shape="raises_propagates",
        source="docstring",
    )
    verdict = verify_raises_propagates(claim, TOMORO_TASK_REPO)
    assert verdict.reason == "contradicted"
    assert "src/main.py:99" in verdict.evidence


def test_raises_propagates_unverifiable_for_module_level_docstring() -> None:
    """A module docstring names no single exception class to check."""
    claim = _claim(
        file="pkg/unimportable.py",
        line=1,
        claim_text="PROPAGATES: this module raises at import time; nothing here recovers it.",
        shape="raises_propagates",
        source="docstring",
    )
    verdict = verify_raises_propagates(claim, FIXTURE_REPO)
    assert verdict.reason == "unverifiable"


# --- verify_defaults_to ------------------------------------------------


def test_defaults_to_ok_when_actual_default_matches() -> None:
    claim = _claim(
        file="pkg/module_defaults.py",
        line=1,
        claim_text="Defaults to 30 seconds if not overridden.",
        shape="defaults_to",
        source="comment",
    )
    verdict = verify_defaults_to(claim, FIXTURE_REPO)
    assert verdict.reason == "ok"


def test_defaults_to_contradicted_when_actual_default_differs() -> None:
    claim = _claim(
        file="pkg/module_defaults.py",
        line=8,
        claim_text="default is 3 if not overridden.",
        shape="defaults_to",
        source="comment",
    )
    verdict = verify_defaults_to(claim, FIXTURE_REPO)
    assert verdict.reason == "contradicted"
    assert "5" in verdict.evidence


def test_defaults_to_unparsed_when_no_literal_in_claim_text() -> None:
    claim = _claim(
        file="pkg/module_defaults.py",
        line=1,
        claim_text="The timeout has a sensible default.",
        shape="defaults_to",
        source="comment",
    )
    verdict = verify_defaults_to(claim, FIXTURE_REPO)
    assert verdict.reason == "unparsed"


# --- verify_returns_type ------------------------------------------------


def test_returns_type_ok_when_annotation_agrees() -> None:
    claim = _claim(
        file="pkg/module_b.py",
        line=6,
        claim_text="Returns an int product of a and b.",
        shape="returns_type",
        source="docstring",
    )
    verdict = verify_returns_type(claim, FIXTURE_REPO)
    assert verdict.reason == "ok"


def test_returns_type_contradicted_when_annotation_disagrees() -> None:
    claim = _claim(
        file="pkg/module_b.py",
        line=11,
        claim_text="Returns a bool indicating whether the division succeeded.",
        shape="returns_type",
        source="docstring",
    )
    verdict = verify_returns_type(claim, FIXTURE_REPO)
    assert verdict.reason == "contradicted"
    assert "float" in verdict.evidence


def test_returns_type_unverifiable_when_no_annotation(tmp_path: Path) -> None:
    (tmp_path / "untyped.py").write_text(
        "def total():\n    return 3  # returns the running total\n", encoding="utf-8"
    )
    claim = _claim(
        file="untyped.py",
        line=2,
        claim_text="returns the running total",
        shape="returns_type",
        source="comment",
    )
    verdict = verify_returns_type(claim, tmp_path)
    assert verdict.reason == "unverifiable"


# --- verify_markdown_reference --------------------------------------------


def test_markdown_reference_ok_when_file_exists() -> None:
    claim = _claim(
        file="README.md",
        line=5,
        claim_text="See `pkg/module_a.py` for the CustomError example.",
        shape="markdown_reference",
        source="markdown",
    )
    verdict = verify_markdown_reference(claim, FIXTURE_REPO)
    assert verdict.reason == "ok"


def test_markdown_reference_contradicted_when_file_does_not_exist() -> None:
    claim = _claim(
        file="README.md",
        line=6,
        claim_text="See `docs/CHANGELOG.md` for release notes.",
        shape="markdown_reference",
        source="markdown",
    )
    verdict = verify_markdown_reference(claim, FIXTURE_REPO)
    assert verdict.reason == "contradicted"


def test_markdown_reference_ok_for_a_declared_command() -> None:
    claim = _claim(
        file="README.md",
        line=1,
        claim_text="Run `claimcheck` from the project root.",
        shape="markdown_reference",
        source="markdown",
    )
    verdict = verify_markdown_reference(claim, Path(__file__).resolve().parent.parent)
    assert verdict.reason == "ok"


def test_markdown_reference_unverifiable_when_nothing_named() -> None:
    claim = _claim(
        file="README.md",
        line=1,
        claim_text="This line names no command and no file.",
        shape="markdown_reference",
        source="markdown",
    )
    verdict = verify_markdown_reference(claim, FIXTURE_REPO)
    assert verdict.reason == "unverifiable"


# --- verify() dispatcher -----------------------------------------------


def test_verify_dispatches_on_shape() -> None:
    claim = _claim(
        file="pkg/module_a.py",
        line=2,
        claim_text="PROPAGATES: no handler exists in this module for CustomError.",
        shape="raises_propagates",
        source="docstring",
    )
    assert verify(claim, FIXTURE_REPO) == verify_raises_propagates(claim, FIXTURE_REPO)


def test_verify_returns_unverifiable_for_other_shape() -> None:
    claim = _claim(shape="other", claim_text="a claim no deterministic verifier settles")
    verdict = verify(claim, FIXTURE_REPO)
    assert verdict == Verdict("unverifiable", verdict.evidence)


# --- cross-check against data/labelled_claims.jsonl, real rainmaker files --


@requires_rainmaker_repo
def test_cross_check_rainmaker_harness_check_target_lines_agrees_with_label() -> None:
    """lc-030: `CLAUDE_MD_TARGET_LINES = 200` -- labelled `ok`, defaults_to, rainmaker."""
    claim = _claim(
        file=".claude/templates/harness-check.py",
        line=43,
        claim_text='CLAUDE_MD_TARGET_LINES = 200  # "target under 200 lines"; guidance, '
        "not enforced by the harness",
        shape="defaults_to",
        source="comment",
    )
    verdict = verify_defaults_to(claim, RAINMAKER_REPO)
    assert verdict.reason == "ok"


@requires_rainmaker_repo
def test_cross_check_rainmaker_timesheet_min_sample_agrees_with_label() -> None:
    """lc-032: `MIN_SAMPLE = 8` -- labelled `ok`, defaults_to, rainmaker."""
    claim = _claim(
        file="bin/timesheet",
        line=9,
        claim_text="the 8-sample minimum passed on duplicated data",
        shape="defaults_to",
        source="comment",
    )
    verdict = verify_defaults_to(claim, RAINMAKER_REPO)
    assert verdict.reason == "ok"


@requires_rainmaker_repo
def test_cross_check_rainmaker_codex_cli_doc_size_agrees_with_label() -> None:
    """lc-043: a claim about Codex CLI's own external docs -- labelled `unverifiable`.

    `bin/timesheet` is a shell-style script with no `.py` suffix but valid Python
    source; `.claude/standards/harness/codex-cli.md` is markdown, so no AST target
    exists to compare the extracted `32` against -- `unverifiable`, agreeing with
    the hand-assigned label for a different reason than the label's own (a
    third-party doc fact, not a repo constant), which is itself a real disagreement
    worth recording: the verifier cannot tell *why* it is unverifiable, only that
    it is.
    """
    claim = _claim(
        file=".claude/standards/harness/codex-cli.md",
        line=28,
        claim_text='**Size**: `project_doc_max_bytes`, "32 KiB by default" per the agents-md page.',
        shape="defaults_to",
        source="markdown",
    )
    verdict = verify_defaults_to(claim, RAINMAKER_REPO)
    assert verdict.reason == "unverifiable"
