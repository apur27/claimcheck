"""In-memory `ModelClient` implementations for tests that must never hit the network.

Neither class imports `anthropic` -- see `anthropic_client.py`'s module docstring for why that
matters. `StubClient` answers every call the same way (or raises the same exception), enough for
single-call orchestration tests. `FixtureClient` maps each distinct `(system, user)` pair to its
own canned response, for tests that drive several distinct calls in one run.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StubClient:
    """Returns `response` (or raises `error`, if set) from every `complete` call.

    Every call is recorded in `calls` so a test can assert what was actually sent.
    """

    response: str = "ok\nstub verdict, no real model was called"
    error: Exception | None = None
    calls: list[tuple[str, str]] = field(default_factory=list)

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if self.error is not None:
            raise self.error
        return self.response


@dataclass
class FixtureClient:
    """Maps each distinct `(system, user)` pair in `responses` to its canned reply.

    Raises `KeyError` -- the real, unmodified exception -- for any pair not seeded, so a test
    that drives an unexpected prompt fails loudly instead of silently returning the wrong fixture.
    """

    responses: dict[tuple[str, str], str]
    calls: list[tuple[str, str]] = field(default_factory=list)

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.responses[(system, user)]
