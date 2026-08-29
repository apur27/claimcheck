"""Real Anthropic API adapter implementing the `ModelClient` port.

The ONLY module in this package allowed to import `anthropic` -- enforced by the
`vendor-isolation` contract in `.importlinter`, which forbids `claimcheck.domain` and
`claimcheck.services` from importing it. Reads `ANTHROPIC_API_KEY` from `.env` via
`python-dotenv`, here and nowhere else.

No explicit `temperature`/`top_p`/`top_k`: Claude Sonnet 5 (`MODEL_ID` below) returns HTTP 400
if any of those are set explicitly this session. `thinking={"type": "disabled"}` is used instead
as the closest determinism lever actually available -- this is NOT a claim of literal
"temperature 0", which would itself be a false claim in a tool whose whole purpose is catching
false claims. `timeout=` and `max_retries=0` are passed explicitly to the SDK client constructor:
its own defaults (600s timeout, 2 internal retries) stack underneath any retry policy a caller
writes and are unbounded/uncounted if left implicit.

Two spend guards, both real: every response's actual `usage` block is priced and added to
`total_cost_usd`, printed after every call; and before a call is made, `complete` estimates its
worst-case cost and refuses (`BudgetExceededError`) rather than let the running total cross
`budget_usd`. A response cache, keyed on `(system prompt, model id, user turn)` -- never the
user turn alone, so two prompt variants sharing one cache key silently returning one arm's
answers for the other cannot happen even with a single arm in use today -- also avoids paying
twice for an identical call within one process.
"""

from __future__ import annotations

import hashlib
import os
from typing import Final

import anthropic
from dotenv import load_dotenv

MODEL_ID: Final = "claude-sonnet-5"
CLIENT_TIMEOUT_SECONDS: Final = 30.0
SDK_MAX_RETRIES: Final = 0
DEFAULT_BUDGET_USD: Final = 2.00
_MAX_OUTPUT_TOKENS: Final = 200
_CHARS_PER_TOKEN_ESTIMATE: Final = 4

_USD_PER_MILLION_TOKENS: Final = 1_000_000
# Rate assumption, not measured -- Claude Sonnet-class published per-token price as of this
# session (Aug 2026): $3.00 / MTok input, $15.00 / MTok output, the rate that has held across
# the Sonnet line since 3.5. Not fetched live this slice; source-check against the current
# pricing page before treating this as billing-accurate (same caveat the tomoro-task sibling
# adapter carries for its own rate constants).
INPUT_TOKEN_RATE_USD: Final = 3.00 / _USD_PER_MILLION_TOKENS
OUTPUT_TOKEN_RATE_USD: Final = 15.00 / _USD_PER_MILLION_TOKENS

_API_KEY_ENV_VAR: Final = "ANTHROPIC_API_KEY"


class BudgetExceededError(RuntimeError):
    """Raised by `AnthropicClient.complete` when the next call would cross the dollar cap.

    PROPAGATES: to the caller of `complete` -- `services/model_verify.py`, and above it the
    measurement script driving the one live comparison this slice makes -- which is expected to
    stop issuing calls and report how many claims got a real verdict versus how many were
    skipped for budget. Never caught inside this module: swallowing it here would spend past the
    cap it exists to enforce.
    """

    def __init__(self, spent_usd: float, next_call_estimate_usd: float, budget_usd: float) -> None:
        super().__init__(
            f"budget exhausted: ${spent_usd:.4f} spent, next call estimated at "
            f"${next_call_estimate_usd:.4f}, would exceed the ${budget_usd:.2f} cap"
        )


def _cache_key(system: str, model: str, user: str) -> str:
    """Cache key over (system prompt, model id, user turn) -- never the user turn alone.

    An A/B (or any two prompt variants) sharing a cache key would silently return one arm's
    answers for the other; keying on `system` and `model` too, not just `user`, is what this
    guards even with a single arm in use today. `hashlib.sha256` rather than the builtin
    `hash()`: the builtin is randomized per process (`PYTHONHASHSEED`), which would make this
    key untestable by direct equality across separate test runs.
    """
    digest_input = "\x1f".join((system, model, user)).encode("utf-8")
    return hashlib.sha256(digest_input).hexdigest()


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN_ESTIMATE)


class ModelResponseMissingTextError(ValueError):
    """A model response carried no text content block to parse.

    PROPAGATES: to the caller of `AnthropicClient.complete`. Should never happen with `thinking`
    disabled and no tools offered -- treated as a hard failure rather than a quiet
    `unverifiable` Verdict, since it signals the request shape changed, not routine model
    uncertainty.
    """


def _extract_text(response: anthropic.types.Message) -> str:
    for block in response.content:
        if isinstance(block, anthropic.types.TextBlock):
            return block.text
    raise ModelResponseMissingTextError(response.content)


class AnthropicClient:
    """Real `ModelClient` backed by the Anthropic SDK, with cost tracking and a hard budget cap."""

    def __init__(
        self,
        *,
        budget_usd: float = DEFAULT_BUDGET_USD,
        max_output_tokens: int = _MAX_OUTPUT_TOKENS,
    ) -> None:
        load_dotenv()
        api_key = os.environ[_API_KEY_ENV_VAR]
        self._client = anthropic.Anthropic(
            api_key=api_key, timeout=CLIENT_TIMEOUT_SECONDS, max_retries=SDK_MAX_RETRIES
        )
        self._max_output_tokens = max_output_tokens
        self._cache: dict[str, str] = {}
        self.budget_usd = budget_usd
        self.total_cost_usd = 0.0
        self.calls_made = 0

    def _estimate_call_cost_usd(self, system: str, user: str) -> float:
        """Worst-case cost of one call: real input length, maximum possible output length."""
        input_tokens = _estimate_tokens(system) + _estimate_tokens(user)
        return input_tokens * INPUT_TOKEN_RATE_USD + self._max_output_tokens * OUTPUT_TOKEN_RATE_USD

    def _record_usage(self, usage: anthropic.types.Usage) -> None:
        call_cost = (
            usage.input_tokens * INPUT_TOKEN_RATE_USD + usage.output_tokens * OUTPUT_TOKEN_RATE_USD
        )
        self.total_cost_usd += call_cost
        self.calls_made += 1
        print(
            f"anthropic_client: call #{self.calls_made} cost ${call_cost:.4f} "
            f"({usage.input_tokens} in / {usage.output_tokens} out tokens), "
            f"cumulative ${self.total_cost_usd:.4f} of ${self.budget_usd:.2f} cap"
        )

    def complete(self, system: str, user: str) -> str:
        """Return the model's reply, from cache if this exact call was already made.

        Raises `BudgetExceededError` rather than place a call that would cross `budget_usd`.
        Any SDK error is printed with its real text -- never a canned message -- then re-raised
        unmodified.
        """
        key = _cache_key(system, MODEL_ID, user)
        if key in self._cache:
            return self._cache[key]

        estimated_cost = self._estimate_call_cost_usd(system, user)
        if self.total_cost_usd + estimated_cost > self.budget_usd:
            raise BudgetExceededError(self.total_cost_usd, estimated_cost, self.budget_usd)

        try:
            response = self._client.messages.create(
                model=MODEL_ID,
                max_tokens=self._max_output_tokens,
                system=system,
                thinking={"type": "disabled"},
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.AnthropicError as exc:
            # The real SDK exception, verbatim -- never a canned "call failed" message. An SDK
            # renaming a class between major versions fails identically to a genuine outage, and
            # a canned message here would send the next person chasing a phantom install problem.
            print(f"anthropic_client: real SDK error -- {exc!r}")
            raise

        text = _extract_text(response)
        self._record_usage(response.usage)
        self._cache[key] = text
        return text
