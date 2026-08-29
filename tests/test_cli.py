"""Tests for the claimcheck CLI entry point.

`main()` is the console-script entry point registered in `pyproject.toml`.
It parses a positional path argument, extracts claims via `services.extract`,
verifies the four deterministic shapes via `domain.verifiers`, and prints
`contradicted` findings -- the brief's own `WORKING` demonstration, made
executable below against `tests/fixtures/sample_repo/`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from claimcheck.cli import main

FIXTURE_REPO = Path(__file__).resolve().parent / "fixtures" / "sample_repo"


def test_help_exits_zero(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`claimcheck --help` prints usage and exits 0, with no ambient API key needed."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["claimcheck", "--help"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_working_demonstration_names_claim_and_handler(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The brief's own WORKING line: a PROPAGATES claim contradicted by a handler three
    files away is named, with the file, line and the evidence naming the handler."""
    monkeypatch.setattr(sys, "argv", ["claimcheck", str(FIXTURE_REPO)])
    exit_code = main()
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "pkg/module_a.py:2" in output
    assert "CustomError" in output
    assert "module_c.py" in output


def test_runs_with_no_api_key(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scanning a repo needs no `ANTHROPIC_API_KEY` and no network."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["claimcheck", str(FIXTURE_REPO)])
    exit_code = main()
    assert exit_code == 0
    assert capsys.readouterr().out != ""


def test_missing_path_exits_nonzero(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Pointing the CLI at a path that doesn't exist is a real failure, not a silent no-op."""
    missing = tmp_path / "does-not-exist"
    monkeypatch.setattr(sys, "argv", ["claimcheck", str(missing)])
    exit_code = main()
    assert exit_code != 0
    assert "does-not-exist" in capsys.readouterr().err
