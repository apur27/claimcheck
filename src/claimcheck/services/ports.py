"""Port for a model-backed text completion, the seam between orchestration and any backend.

`ModelClient` is deliberately the smallest possible surface: one turn, no conversation state, no
vendor-shaped types. `services/model_verify.py` depends only on this protocol, never on a
concrete implementation, so `verify_with_model` runs identically against
`adapters.stub_client.StubClient` in tests and `adapters.anthropic_client.AnthropicClient` for
the one live measurement this session makes. Lives here rather than in `domain` because
"complete a prompt" is an orchestration capability, not pure claim-checking logic -- `domain`
stays free of even the vendor-agnostic shape of a network call. Structural typing (`Protocol`)
means neither adapter needs to import this module to satisfy it; it exists purely so callers can
type-hint the parameter they accept.
"""

from __future__ import annotations

from typing import Protocol


class ModelClient(Protocol):
    """A single-turn text completion: a system prompt and a user turn in, response text out."""

    def complete(self, system: str, user: str) -> str:
        """Return the model's reply to `user`, steered by `system`. Raises on transport failure."""
        ...
