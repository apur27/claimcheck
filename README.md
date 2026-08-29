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
uv run claimcheck --diff                # scan only files staged in the current repo
```

No API key, no network — the four deterministic verifiers (below) never import or execute the
code they scan; they read it as text via Python's `ast` and `tokenize` modules only.

## The gate

```bash
make check           # ruff format, ruff check, mypy --strict, import-linter, pytest --cov
make check-falsify    # proves the scoring path itself is correct (see Method)
make harness-check    # verifies this repo's own .claude/ scaffolding is well-formed
```

## Architecture

```
cli -> adapters -> services -> domain
```

`domain` (claim model, the four verifiers, the scorer) and `services` (tree walking, claim
collection, the eval runner) never import a vendor SDK — `import-linter` enforces this as a
`forbidden` contract, and it's proven live in this repo's own test suite (a violating import was
added, confirmed the gate catches it, then removed). Only `adapters/anthropic_client.py` imports
`anthropic`, and only the model-backed verifier (out of the no-API-key path entirely) uses it.

**Why this layering**: the deterministic verifiers are the tool's actual reliability story — they
need no key, no network, and no model call to run, so they had to be structurally incapable of
depending on one. The model-backed verifier is additive, not load-bearing.

## Results

Measured against a 44-claim hand-labelled set (7 genuinely contradicted, built from three real
codebases before any verifier existed, so the checker couldn't be tuned to its own answer key):

| | Precision | Recall |
|---|---|---|
| Deterministic verifiers | 0.40 (2/5 findings) | 0.29 (2/7 labelled-contradicted) |
| + model-backed verifier | 0.40 (unchanged) | 0.29 (unchanged) |

The model-backed verifier, run live against the 30 claims the deterministic verifiers couldn't
settle, added zero true positives. See `REPORT.md`'s Error Analysis for why (most likely: it was
given only local file context, not the repo-wide search the deterministic `raises_propagates`
verifier performs).

## Error analysis

- 2 of the 7 labelled-contradicted claims were caught by fixing a real bug found mid-session: a
  docstring-ownership check used exact-line equality against a claim's line number, but a claim's
  line can point anywhere inside a multi-line docstring, not just its first line. Full story,
  including a wrong initial diagnosis that independent verification caught, in `REPORT.md`.
- 1 known false positive (`verify_raises_propagates` can't yet distinguish "no handler exists"
  claims from "a handler exists, by design" claims — it only checks whether a handler exists
  anywhere).
- 1 known gap (`verify_raises_propagates` only reads a function's own `raise` statements, not what
  its docstring says it *catches*).

## Future work, ranked

1. Give the model-backed verifier the same repo-wide evidence search the deterministic
   `raises_propagates` verifier already has — its 0/4 result on a live measurement is likely a
   missing-context problem, not a model-capability one.
2. Fix the claim-polarity blindness above (a scoped, already-identified false positive).
3. Scope `verify_defaults_to`'s nearest-constant search to the claim's enclosing function/class
   (currently whole-file by line distance — no observed failure yet, but a real risk class).
4. Consolidate AST-helper duplication across `domain/verifiers.py`, `services/extract.py`, and
   `scripts/check_exceptions.py`.

## Limitations

- The labelled set's contradicted-claim count (7) is below what was planned (10) — the three
  source corpora had already had their known defects fixed by prior review passes before this
  project's own labelling read them. Precision/recall on a 7-item denominator move by ~14 points
  per claim; read the headline numbers as directional, not precise.
- `data/labelled_claims.jsonl` points at real files in three sibling repos rather than vendored
  copies (deliberate — so the ground truth can't drift from its source), so `make check-falsify`
  and the real precision/recall measurement only reproduce on a machine that has those repos
  checked out alongside this one.
- No hermetic `make check-clean` target yet.

## AI-tool disclosure

Built by Claude Code (Sonnet 5) under RainMaker orchestration. Every commit was independently
re-verified (gate re-run, specific tests re-run, one bug reproduced by hand) rather than trusted
from a subagent's own report — including catching and correcting a wrong self-diagnosis of why an
early measurement showed zero true positives. Full disclosure in `REPORT.md`.
