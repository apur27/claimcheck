# claimcheck

Prose in a codebase drifts from the code it describes — a docstring says an exception propagates
uncaught, but a handler for it was added three files away and nobody updated the comment. Nothing
in a normal test suite or linter catches this, because the code still runs correctly; only the
*claim about* the code goes stale. `claimcheck` reads a repo's docstrings, comments and markdown,
extracts the checkable claims, and reports which ones the code actually contradicts — with the
file, the line, and the evidence. It doesn't judge writing quality, doesn't rewrite anything, and
doesn't check claims about anything outside the repo it's pointed at.

## Run it

```bash
uv sync --all-extras
uv run claimcheck path/to/a/repo        # scan a whole tree, print contradictions
uv run claimcheck --diff path/to/a/repo # scan only files staged in that repo
```

No API key, no network — nothing in this pipeline imports or executes the code it scans. Claim
extraction reads source as text via Python's `ast` and `tokenize` modules; the four deterministic
verifiers (below) then settle each claim using `ast` only.

## The gate

```bash
make check           # ruff format, ruff check, mypy --strict, import-linter, pytest --cov
make check-falsify    # proves the scoring path itself is correct (see Method)
make harness-check    # verifies this repo's own .claude/ scaffolding is well-formed
```

## Architecture

```
cli -> services -> domain
```

`domain` (claim model, the four verifiers, the scorer) and `services` (tree walking, claim
collection, the eval runner) never import a vendor SDK — `import-linter` enforces this as a
`forbidden` contract, and it's proven live in this repo's own test suite (a violating import was
added, confirmed the gate catches it, then removed). Only `adapters/anthropic_client.py` imports
`anthropic`; `services/ports.py` defines the `Protocol` a vendor client must satisfy, so `services`
never has to import `adapters` to use one, and only the model-backed verifier (out of the
no-API-key path entirely) reaches it.

**Why this layering**: the deterministic verifiers are the tool's actual reliability story — they
need no key, no network, and no model call to run, so they had to be structurally incapable of
depending on one. The model-backed verifier is additive, not load-bearing.

## Results and method

Measured against a 44-claim hand-labelled set (7 genuinely contradicted, built from three real
codebases before any verifier existed, so the checker could not be tuned to its own answer key):
precision 0.40, recall 0.29 on the deterministic verifiers. The model-backed verifier added no
true positives.

Method, falsification, error analysis, limitations and the AI-tool disclosure are in
[`REPORT.md`](REPORT.md), which owns all of it — this file does not repeat them.
