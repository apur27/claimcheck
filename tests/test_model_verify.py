"""Tests for `services/model_verify.py`: prompt construction and response parsing.

Runs entirely against `adapters.stub_client.StubClient` -- never the real network, and never
`claimcheck.adapters.anthropic_client` -- proving `verify_with_model` needs only the
`ModelClient` protocol, not a concrete backend.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claimcheck.adapters.stub_client import StubClient
from claimcheck.domain.models import Claim
from claimcheck.services.model_verify import verify_with_model

FIXTURE_FILE = "pkg/module_a.py"
_NO_SUCH_REPO = Path("/no-such-claimcheck-fixture-repo")


def _claim(claim_text: str = "the code always returns a string", line: int = 2) -> Claim:
    return Claim(
        file=FIXTURE_FILE, line=line, claim_text=claim_text, shape="other", source="docstring"
    )


def _write_fixture_repo(tmp_path: Path) -> Path:
    """Write a tiny real Python file under `tmp_path` and return the repo root."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "module_a.py").write_text(
        "\n".join(f"# line {i}" for i in range(1, 5))
        + "\ndef annotated_function() -> str:\n    return 'x'\n"
    )
    return tmp_path


def test_ok_response_parses_to_ok_verdict(tmp_path: Path) -> None:
    """A two-line 'ok\\n<reason>' reply becomes an `ok` Verdict with that reason as evidence."""
    repo_root = _write_fixture_repo(tmp_path)
    client = StubClient(response="ok\nthe docstring matches what the function does")
    verdict = verify_with_model(_claim(), repo_root, client)
    assert verdict.reason == "ok"
    assert verdict.evidence == "the docstring matches what the function does"


def test_contradicted_response_parses_to_contradicted_verdict(tmp_path: Path) -> None:
    """A 'contradicted\\n<reason>' reply becomes a `contradicted` Verdict."""
    repo_root = _write_fixture_repo(tmp_path)
    client = StubClient(response="contradicted\nthe function actually returns an int")
    verdict = verify_with_model(_claim(), repo_root, client)
    assert verdict.reason == "contradicted"
    assert verdict.evidence == "the function actually returns an int"


def test_unrecognised_first_line_becomes_unverifiable_not_unparsed() -> None:
    """A malformed reply is `unverifiable`, never the extraction-time code `unparsed`."""
    client = StubClient(response="maybe??\nthe model rambled")
    verdict = verify_with_model(_claim(), _NO_SUCH_REPO, client)
    assert verdict.reason == "unverifiable"
    assert verdict.reason != "unparsed"


def test_empty_response_becomes_unverifiable() -> None:
    """An empty reply is treated as `unverifiable`, not a crash."""
    client = StubClient(response="")
    verdict = verify_with_model(_claim(), _NO_SUCH_REPO, client)
    assert verdict.reason == "unverifiable"


def test_client_exception_propagates_unmodified() -> None:
    """`verify_with_model` catches nothing: a client failure is the caller's decision.

    The real exception's own text survives untouched -- never replaced by a canned message.
    """
    client = StubClient(error=RuntimeError("distinctive-client-failure-quokka-7"))
    with pytest.raises(RuntimeError, match="distinctive-client-failure-quokka-7"):
        verify_with_model(_claim(), _NO_SUCH_REPO, client)


def test_prompt_includes_claim_text_and_source_excerpt(tmp_path: Path) -> None:
    """The user turn actually sent carries the claim text and the source file's content."""
    repo_root = _write_fixture_repo(tmp_path)
    client = StubClient(response="ok\nfine")
    verify_with_model(_claim(claim_text="a very specific claim marker xyzzy"), repo_root, client)
    assert len(client.calls) == 1
    _system, user = client.calls[0]
    assert "a very specific claim marker xyzzy" in user
    assert "def annotated_function" in user


def test_unreadable_source_file_still_produces_a_prompt() -> None:
    """A missing/unreadable source file degrades to a placeholder excerpt, not a crash."""
    client = StubClient(response="unverifiable\ncould not read the file")
    verdict = verify_with_model(_claim(), _NO_SUCH_REPO, client)
    assert verdict.reason == "unverifiable"
    _system, user = client.calls[0]
    assert "could not be read" in user
