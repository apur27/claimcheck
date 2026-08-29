"""Tests for AST-based claim extraction.

`extract_claims` must never `import`/`exec`/`importlib` the scanned code --
the fixture repo contains a module that crashes at import time
(`pkg/unimportable.py`) specifically to prove that behaviourally: if
extraction ever imported it, these tests would raise `RuntimeError` instead
of collecting a claim.
"""

from pathlib import Path

from claimcheck.domain.models import Claim
from claimcheck.services.extract import extract_claims

FIXTURE_REPO = Path(__file__).resolve().parent / "fixtures" / "sample_repo"
CLAIMCHECK_SRC = Path(__file__).resolve().parent.parent / "src" / "claimcheck"


def test_extract_claims_over_fixture_repo_matches_expected_exactly() -> None:
    """The fixture repo yields exactly the four seeded claims, nothing else."""
    claims = extract_claims(FIXTURE_REPO)

    expected = [
        Claim(
            file="pkg/module_a.py",
            line=2,
            claim_text="PROPAGATES: no handler exists in this module for CustomError.",
            shape="raises_propagates",
            source="docstring",
        ),
        Claim(
            file="pkg/module_defaults.py",
            line=1,
            claim_text="Defaults to 30 seconds if not overridden.",
            shape="defaults_to",
            source="comment",
        ),
        Claim(
            file="pkg/unimportable.py",
            line=1,
            claim_text="PROPAGATES: this module raises at import time; nothing here recovers it.",
            shape="raises_propagates",
            source="docstring",
        ),
        Claim(
            file="README.md",
            line=3,
            claim_text="Run `claimcheck src/` to scan this repo for stale claims.",
            shape="markdown_reference",
            source="markdown",
        ),
    ]

    assert sorted(claims, key=lambda c: (c.file, c.line)) == sorted(
        expected, key=lambda c: (c.file, c.line)
    )


def test_extract_claims_never_imports_the_scanned_code() -> None:
    """`pkg/unimportable.py` raises at import time; extraction must not trip it.

    If extraction ever called `import`/`importlib`/`exec` on the scanned
    file, this test would fail with the module's `RuntimeError` instead of
    collecting its docstring claim.
    """
    claims = extract_claims(FIXTURE_REPO)

    unimportable_claims = [c for c in claims if c.file == "pkg/unimportable.py"]
    assert len(unimportable_claims) == 1
    assert unimportable_claims[0].shape == "raises_propagates"
    assert unimportable_claims[0].source == "docstring"


def test_extract_claims_over_own_src_does_not_raise() -> None:
    """Self-hosting smoke test: scanning claimcheck's own src/ must not raise."""
    claims = extract_claims(CLAIMCHECK_SRC)
    assert isinstance(claims, list)


def test_extract_claims_comment_claim_has_correct_line_number() -> None:
    """The tokenize pass recovers the comment's own line, not the statement's."""
    claims = extract_claims(FIXTURE_REPO)
    comment_claims = [c for c in claims if c.source == "comment"]
    assert len(comment_claims) == 1
    assert comment_claims[0].line == 1
    assert comment_claims[0].file == "pkg/module_defaults.py"


def test_extract_claims_skips_files_with_syntax_errors(tmp_path: Path) -> None:
    """A `.py` file that does not parse is skipped, not raised on."""
    (tmp_path / "broken.py").write_text("def f(:\n    pass\n", encoding="utf-8")
    assert extract_claims(tmp_path) == []


def test_extract_claims_skips_excluded_directories(tmp_path: Path) -> None:
    """Files under `.venv`/`__pycache__`/`.git` are never scanned."""
    venv_dir = tmp_path / ".venv" / "lib"
    venv_dir.mkdir(parents=True)
    (venv_dir / "vendored.py").write_text(
        '"""PROPAGATES: this should never be seen by the extractor."""\n', encoding="utf-8"
    )
    assert extract_claims(tmp_path) == []


def test_extract_claims_skips_empty_python_file(tmp_path: Path) -> None:
    """An empty module (`ast.parse("")`) has an empty body and yields no claims."""
    (tmp_path / "empty.py").write_text("", encoding="utf-8")
    assert extract_claims(tmp_path) == []


def test_extract_claims_skips_module_with_non_string_leading_statement(tmp_path: Path) -> None:
    """A module whose first statement is a literal, not a string, has no docstring line."""
    (tmp_path / "leading_literal.py").write_text(
        "42\n\n\ndef f() -> None:\n    pass\n", encoding="utf-8"
    )
    assert extract_claims(tmp_path) == []


def test_extract_claims_classifies_returns_type_docstring(tmp_path: Path) -> None:
    """A docstring saying "returns" on a function with a return annotation is `returns_type`."""
    (tmp_path / "typed.py").write_text(
        'def count() -> int:\n    """Returns the number of widgets seen so far."""\n    return 3\n',
        encoding="utf-8",
    )
    claims = extract_claims(tmp_path)
    assert len(claims) == 1
    assert claims[0].shape == "returns_type"
    assert claims[0].source == "docstring"


def test_extract_claims_skips_comments_with_no_recognized_shape(tmp_path: Path) -> None:
    """A comment that matches none of the claim patterns is not extracted."""
    (tmp_path / "noted.py").write_text("x = 1  # just a note, not a claim\n", encoding="utf-8")
    assert extract_claims(tmp_path) == []


def test_extract_claims_classifies_returns_type_comment(tmp_path: Path) -> None:
    """A comment saying "returns" is classified `returns_type`, same as docstrings."""
    (tmp_path / "commented.py").write_text(
        "def total() -> int:\n    return 3  # returns the running total\n", encoding="utf-8"
    )
    claims = extract_claims(tmp_path)
    assert len(claims) == 1
    assert claims[0].shape == "returns_type"
    assert claims[0].source == "comment"


def test_extract_claims_classifies_defaults_to_docstring(tmp_path: Path) -> None:
    """A docstring saying "defaults to" is classified `defaults_to`, same as comments."""
    (tmp_path / "with_default.py").write_text(
        'def configure() -> None:\n    """The retry count defaults to 3 if not overridden."""\n',
        encoding="utf-8",
    )
    claims = extract_claims(tmp_path)
    assert len(claims) == 1
    assert claims[0].shape == "defaults_to"
    assert claims[0].source == "docstring"


def test_extract_claims_classifies_raises_propagates_comment(tmp_path: Path) -> None:
    """A comment naming PROPAGATES: is classified `raises_propagates`, same as docstrings."""
    (tmp_path / "propagating.py").write_text(
        "x = 1  # PROPAGATES: no handler exists for this in the module.\n", encoding="utf-8"
    )
    claims = extract_claims(tmp_path)
    assert len(claims) == 1
    assert claims[0].shape == "raises_propagates"
    assert claims[0].source == "comment"
