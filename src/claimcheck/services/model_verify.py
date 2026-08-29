"""Model-backed verifier for claims the four deterministic verifiers cannot settle.

Orchestration, not domain logic: builds a short prompt from a claim and a slice of the source
file around `claim.line`, asks a `ModelClient` to judge whether the code contradicts the claim,
and parses the reply into a `Verdict`. Depends only on `services.ports.ModelClient`, never on
`claimcheck.adapters`, so `verify_with_model` runs identically against
`adapters.stub_client.StubClient` in tests and `adapters.anthropic_client.AnthropicClient` for
the one live measurement this session makes.

No exception raised by `client.complete` is caught here -- a budget cap or a network failure is
the caller's decision (stop the whole run, skip one claim, ...), not this function's; see
`scripts/run_model_eval.py` for that decision.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from claimcheck.domain.models import Claim
from claimcheck.domain.verifiers import Verdict
from claimcheck.services.ports import ModelClient

_CONTEXT_LINES_BEFORE: Final = 5
_CONTEXT_LINES_AFTER: Final = 15

# The one prompt this verifier sends, kept short deliberately: the measurement this slice makes
# is budget-constrained, not a chatty multi-turn analysis. Content checked against the 24 real
# `shape == "other"` rows in `data/labelled_claims.jsonl` before shipping -- see the engineer's
# slice report for the count and what it found.
SYSTEM_PROMPT: Final = (
    "You check whether a claim made in a code comment, docstring or README actually holds "
    "against the code shown below it. Read the claim and the source excerpt, then decide "
    "one of:\n"
    "- ok: the code is consistent with the claim.\n"
    "- contradicted: the code demonstrably does something different from what the claim says.\n"
    "- unverifiable: the excerpt does not contain enough to check the claim either way -- for "
    "example it is an opinion, a prediction, or about something outside this file.\n"
    "Reply with exactly two lines: the verdict word alone on the first line (ok, contradicted, "
    "or unverifiable), then a one-sentence justification on the second line. Nothing else."
)

_USER_TEMPLATE: Final = (
    "Claim ({file}:{line}):\n{claim_text}\n\n"
    "Source excerpt ({file}, lines {start}-{end}):\n{excerpt}"
)

_VALID_MODEL_REASONS: Final = frozenset({"ok", "contradicted", "unverifiable"})
_NO_JUSTIFICATION: Final = "(model gave no justification line)"


def _read_excerpt(repo_root: Path, file: str, line: int) -> tuple[str, int, int]:
    """Return (excerpt, start_line, end_line) around `line`, or ("", 0, 0) if unreadable."""
    path = repo_root / file
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "", 0, 0
    if not lines:
        return "", 0, 0
    start = max(1, line - _CONTEXT_LINES_BEFORE)
    end = min(len(lines), line + _CONTEXT_LINES_AFTER)
    excerpt = "\n".join(lines[start - 1 : end])
    return excerpt, start, end


def _build_user_prompt(claim: Claim, repo_root: Path) -> str:
    excerpt, start, end = _read_excerpt(repo_root, claim.file, claim.line)
    if not excerpt:
        excerpt = "(source file could not be read)"
    return _USER_TEMPLATE.format(
        file=claim.file,
        line=claim.line,
        claim_text=claim.claim_text,
        start=start,
        end=end,
        excerpt=excerpt,
    )


def _parse_verdict(response_text: str) -> Verdict:
    lines = [line.strip() for line in response_text.strip().splitlines() if line.strip()]
    if not lines:
        return Verdict("unverifiable", "model returned an empty response")
    reason = lines[0].lower()
    if reason not in _VALID_MODEL_REASONS:
        return Verdict(
            "unverifiable",
            f"model response did not start with a recognised verdict: {lines[0]!r}",
        )
    justification = lines[1] if len(lines) > 1 else _NO_JUSTIFICATION
    return Verdict(reason, justification)


def verify_with_model(claim: Claim, repo_root: Path, client: ModelClient) -> Verdict:
    """Ask `client` to judge `claim` against the source it names, and parse the reply.

    Never `unparsed`: that reason code is an extraction-time signal, not a model judgment --
    a model response this function cannot parse into `ok`/`contradicted`/`unverifiable` becomes
    an `unverifiable` verdict instead, with the raw first line quoted as evidence.
    """
    user_prompt = _build_user_prompt(claim, repo_root)
    response_text = client.complete(SYSTEM_PROMPT, user_prompt)
    return _parse_verdict(response_text)
