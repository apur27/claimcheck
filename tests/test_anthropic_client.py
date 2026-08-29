"""Tests for `adapters/anthropic_client.py`: cache-key math, budget guard, error surfacing.

Never hits the real network. The one exception -- an SDK-error-propagation test -- exercises
`AnthropicClient.complete` after monkeypatching its already-constructed `_client.messages.create`
attribute, so constructing the real `anthropic.Anthropic` object (safe: no network, just reading
`ANTHROPIC_API_KEY` via `.env`) never means a real call goes out.
"""

from __future__ import annotations

import pytest

from claimcheck.adapters.anthropic_client import (
    MODEL_ID,
    AnthropicClient,
    BudgetExceededError,
    _cache_key,
)


def test_cache_key_same_pair_is_stable() -> None:
    """The same (system, model, user) triple always produces the same key."""
    first = _cache_key("system prompt", MODEL_ID, "user turn")
    second = _cache_key("system prompt", MODEL_ID, "user turn")
    assert first == second


def test_cache_key_differs_by_system_prompt() -> None:
    """Two different system prompts over the same user turn must not collide.

    This is the A/B trap named in the brief: a cache keyed on the user turn alone would return
    one arm's answers for the other.
    """
    key_a = _cache_key("system prompt A", MODEL_ID, "same user turn")
    key_b = _cache_key("system prompt B", MODEL_ID, "same user turn")
    assert key_a != key_b


def test_cache_key_differs_by_user_turn() -> None:
    """Two different user turns over the same system prompt must not collide."""
    key_1 = _cache_key("same system prompt", MODEL_ID, "user turn 1")
    key_2 = _cache_key("same system prompt", MODEL_ID, "user turn 2")
    assert key_1 != key_2


def test_cache_key_differs_by_model_id() -> None:
    """Two different model ids over the same prompt pair must not collide."""
    key_x = _cache_key("system", "model-x", "user")
    key_y = _cache_key("system", "model-y", "user")
    assert key_x != key_y


def test_complete_surfaces_real_sdk_exception_not_a_canned_message() -> None:
    """An SDK error propagates through `complete` with its own real text, unmodified.

    Never replaced by a canned "call failed"/"not installed" message -- an SDK renaming a class
    between major versions fails identically to a genuine outage, and a canned message sends the
    next person chasing a phantom install problem.
    """
    client = AnthropicClient()

    def _raise(**_kwargs: object) -> None:
        raise RuntimeError("distinctive-sdk-failure-jitterbug-42")

    client._client.messages.create = _raise  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="distinctive-sdk-failure-jitterbug-42"):
        client.complete("system prompt", "user turn")


def test_complete_refuses_a_call_that_would_cross_the_budget() -> None:
    """A near-zero budget hard-stops before any call is placed -- `BudgetExceededError`."""
    client = AnthropicClient(budget_usd=0.0)

    def _fail_if_called(**_kwargs: object) -> None:
        raise AssertionError("sdk-called-past-budget")

    client._client.messages.create = _fail_if_called  # type: ignore[assignment]

    with pytest.raises(BudgetExceededError):
        client.complete("system prompt", "user turn")
