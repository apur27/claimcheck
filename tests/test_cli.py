"""Tests for the claimcheck CLI entry point.

`main()` is the console-script entry point registered in `pyproject.toml`.
It parses a positional path argument, extracts claims via `services.extract`,
verifies the four deterministic shapes via `domain.verifiers`, and prints
`contradicted` findings -- the brief's own `WORKING` demonstration, made
executable below against `tests/fixtures/sample_repo/`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from claimcheck.cli import main

FIXTURE_REPO = Path(__file__).resolve().parent / "fixtures" / "sample_repo"


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    return repo


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


def test_diff_reports_staged_claim(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--diff` verifies a claim in a staged file, same shape as the full-tree scan."""
    repo = _init_repo(tmp_path)
    (repo / "module_a.py").write_text(
        "class CustomError(Exception):\n"
        '    """PROPAGATES: no handler exists in this module for CustomError."""\n'
    )
    (repo / "module_c.py").write_text(
        "from module_a import CustomError\n\n"
        "def handle() -> str:\n"
        "    try:\n"
        "        return 'x'\n"
        "    except CustomError:\n"
        "        return 'handled'\n"
    )
    _git("add", "module_a.py", "module_c.py", cwd=repo)
    monkeypatch.setattr(sys, "argv", ["claimcheck", str(repo), "--diff"])
    exit_code = main()
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "module_a.py" in output
    assert "CustomError" in output


def test_diff_excludes_unstaged_file_claims(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A modified-but-unstaged file's claim is not reported -- only the staged file is."""
    repo = _init_repo(tmp_path)
    (repo / "staged.py").write_text("STAGED_LIMIT = 5\n")
    (repo / "unstaged.py").write_text("UNSTAGED_LIMIT = 5\n")
    _git("add", "staged.py", "unstaged.py", cwd=repo)
    _git("commit", "-m", "init", cwd=repo)

    (repo / "staged.py").write_text("STAGED_LIMIT = 5  # Defaults to 42 if not overridden.\n")
    _git("add", "staged.py", cwd=repo)
    (repo / "unstaged.py").write_text("UNSTAGED_LIMIT = 5  # Defaults to 99 if not overridden.\n")

    monkeypatch.setattr(sys, "argv", ["claimcheck", str(repo), "--diff"])
    exit_code = main()
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "staged.py" in output
    assert "42" in output
    assert "unstaged.py" not in output
    assert "99" not in output


def test_diff_no_staged_changes_exits_zero(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No staged changes is not an error -- a clear message and exit 0."""
    repo = _init_repo(tmp_path)
    (repo / "a.py").write_text('"""Nothing staged."""\n')
    monkeypatch.setattr(sys, "argv", ["claimcheck", str(repo), "--diff"])
    exit_code = main()
    assert exit_code == 0
    assert "no staged changes" in capsys.readouterr().out.lower()


def test_diff_outside_git_repo_fails_cleanly(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--diff` outside a git repo fails with a clear message, not a raw traceback."""
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    monkeypatch.setattr(sys, "argv", ["claimcheck", str(outside), "--diff"])
    exit_code = main()
    assert exit_code != 0
    assert "git" in capsys.readouterr().err.lower()
