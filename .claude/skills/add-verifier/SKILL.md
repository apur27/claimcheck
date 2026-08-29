---
name: add-verifier
description: Add a new deterministic claim-shape verifier to claimcheck — a new entry in VALID_SHAPES, a verify_<shape> function in domain/verifiers.py, its dispatcher wiring, fixture cases in tests/fixtures/sample_repo/, and tests. Use when a claim shape currently falls through to "other"/unverifiable and should be settled mechanically without a model.
---

# Add a deterministic verifier

A verifier settles one shape of claim against the code using **`ast` only**. Everything in this
file follows from that: scanning a repo must never execute it.

## The hard constraints

- **No `import`, `importlib`, `exec`, `eval`, or `subprocess` of scanned code.** Read source text
  with `Path.read_text` and parse it with `ast.parse`. `tests/fixtures/sample_repo/unimportable.py`
  is valid Python that raises on import and is scanned by the suite — it exists to catch this.
- **`domain/` stays pure.** No vendor SDK, no network, no clock, no environment reads.
  `.importlinter` enforces the SDK half; the rest is on you.
- **Return a `Verdict`, never raise for a claim you cannot settle.** The reason codes are frozen in
  `REASON_CODES`: `ok`, `contradicted`, `unverifiable`, `unparsed`. Distinguish them carefully —
  `unparsed` means the *claim text* did not yield the thing to compare; `unverifiable` means the
  *code* did not. Both count as false negatives when the label says `contradicted`, so neither is
  an escape hatch.
- **Prefer `unverifiable` to a guess.** A false positive costs roughly double a false negative here:
  a wrong finding sends a reader to correct a docstring that was already right, and a tool that
  does that twice gets switched off. Recall you can improve later; trust you cannot.

## Steps

1. **Name the shape.** Add the string to `VALID_SHAPES` in `src/claimcheck/domain/models.py`. Use
   the existing naming style (`raises_propagates`, `defaults_to`, `returns_type`,
   `markdown_reference`) — snake_case, verb-ish, describing the prose pattern not the fix.

2. **Write `verify_<shape>(claim: Claim, repo_root: Path) -> Verdict`** in
   `src/claimcheck/domain/verifiers.py`, next to the others, under its own
   `# --- verify_<shape> ---` banner comment. Split claim-text parsing (a module-level compiled
   regex, named `_<THING>_PATTERN`) from AST inspection (a `_`-prefixed helper). Reuse
   `_parse_python_file`, `_iter_py_files`, `_base_name`, `_docstring_spans_line` and
   `_nearest_enclosing_function` rather than reimplementing them.

3. **Wire the dispatcher.** Add the shape to the `_VERIFIERS` dict at the bottom of the module.
   `verify()` needs no other change; an unmapped shape already falls through to `unverifiable`.

4. **Add fixture cases** under `tests/fixtures/sample_repo/`. You need at least one case the
   verifier must call `contradicted` and one it must call `ok` — a verifier with no negative case
   is a verifier nobody has shown can fail. Note that `tests/fixtures/**/*.py` is exempt from
   `TRY003` in `pyproject.toml` because its prose is deliberately the thing under test.

5. **Add tests** in `tests/test_verifiers.py`, one per reason code the new verifier can return,
   plus one asserting `verify()` routes the new shape to it. `pytest-randomly` shuffles order, so
   no test may depend on another having run.

6. **Only then consider the labelled set.** Adding rows to `data/labelled_claims.jsonl` is a
   separate, deliberate act — never done to make a new verifier score well. If you do add rows,
   they must be hand-verified against the real source repo, and you must update the frozen literals
   in **both** `tests/test_labelled_claims.py` and `scripts/check_falsify.py` in the same commit,
   by hand, saying in the commit body what you recounted.

## Before you call it done

```bash
make check          # ruff, mypy strict, lint-imports, exceptions, pytest+coverage
make check-falsify  # the scoring path is still falsifiable, and the score moved as expected
```

Report the `verify()` precision/recall line from `check-falsify` before and after. A new verifier
that raises recall while dropping precision is usually a bad trade in this repo — say so if that is
what happened, rather than reporting the recall alone.
