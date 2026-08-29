# claimcheck

Finds claims in docstrings, comments and markdown that the code contradicts. A "claim" is a
sentence of prose asserting something checkable about the code near it — *raises X uncaught*,
*defaults to 30*, *returns a Path*, *see `scripts/foo.py`*. claimcheck extracts those, settles
each one against the actual source, and prints the ones the code disagrees with.

```bash
uv sync --all-extras                 # setup
uv run claimcheck tests/fixtures/sample_repo/   # scan a repo, print contradictions
uv run claimcheck --diff                        # scan only staged files (git diff --cached)
make check                           # the gate — must pass before any commit
```

## Layout and the import direction

```
src/claimcheck/
  cli/         argument parsing and terminal output. The only layer that prints findings.
  adapters/    vendor SDKs live here and nowhere else (anthropic_client, stub_client).
  services/    orchestration: extract.py, eval.py, model_verify.py, ports.py.
  domain/      pure logic: models.py, verifiers.py, scorer.py. No I/O, no SDK, no clock.
scripts/       standalone checks run by the Makefile, never imported by the package.
tests/         pytest suite; tests/fixtures/sample_repo/ is deliberately-wrong sample code.
data/          labelled_claims.jsonl — the frozen ground truth.
```

Dependencies point inward: `cli -> services -> domain`. `domain` and `services` must never import
`anthropic`, `httpx` or `requests`. Both rules are mechanically enforced by `.importlinter` and run
as `uv run lint-imports` inside `make check` — if you need a vendor call from a service, add it
behind a `Protocol` in `services/ports.py` and implement it in `adapters/`, the way
`ModelClient`/`AnthropicClient` already does. This is what lets `domain` be tested with nothing but
Python and no API key.

## The gate

`make check` runs, in order: `ruff format --check`, `ruff check --no-fix`, `mypy` (strict, on
`src`/`scripts`/`tests`), `lint-imports`, `scripts/check_exceptions.py`, and
`pytest --cov --cov-fail-under=80`. All of it must pass. Do not silence a rule to get through it —
if a rule genuinely does not apply to a file, add a `per-file-ignores` entry in `pyproject.toml`
with a comment saying why, which is how every existing exemption there is justified.

Two extra targets, not in `check`:

- `make check-falsify` — proves the scoring path can fail (see below).
- `make harness-check` — validates this repo's own `.claude/` scaffolding offline.

## The METRIC is frozen

Precision and recall against `data/labelled_claims.jsonl`, **reported separately, each with its own
denominator, never blended into a single number**. Read the module docstring of
`src/claimcheck/domain/scorer.py`; it is the authority, and it says precisely:

- precision = true positives / findings, where a *finding* is a claim the checker reported
  `contradicted`. `None` when there are no findings — 0-over-0 is undefined, not zero.
- recall = true positives / labelled-`contradicted` claims.
- **A claim the checker cannot parse (`unparsed`) still counts as a false negative** when the label
  says `contradicted`. It is never excluded from either denominator. Dropping hard cases from the
  denominator is the single easiest way to make this tool look better than it is.

`make check-falsify` runs the real `verify()`, a `null_checker` that flags everything and an
`empty_checker` that flags nothing through the *identical* path, and fails if the null/empty
results do not show the mathematically forced pattern. If that check breaks, no number this repo
reports about the real checker means anything.

### Frozen constants are asserted as literals, on purpose

`tests/test_labelled_claims.py` and `scripts/check_falsify.py` pin 44 total claims and 7 labelled
`contradicted` as hand-counted integer literals. They are deliberately **not** recomputed from the
file and compared to themselves. A test that derives the constant it defends from the data it is
checking cannot detect that data changing — this repo hit exactly that pattern once and it was
fixed. If you change the labelled set, change the literals in the same commit, by hand, and say in
the commit body what you recounted.

## `data/labelled_claims.jsonl` is committed once and never regenerated

It was hand-built and verified against three real repos before any verifier existed. Never
regenerate it, relabel a row, or drop a row because a verifier gets it wrong — that is fitting the
ground truth to the result, and it silently destroys every number derived from it. A verifier being
wrong is a bug in the verifier. If a *label* is genuinely wrong, fix that one row in its own commit,
with the evidence in the commit body, and update the frozen counts.

Row shape: `id`, `source_repo`, `file`, `line`, `claim_kind`, `claim_text`, `reason`, `evidence`,
`shape`. `reason` is one of `ok` / `contradicted` / `unverifiable` / `unparsed`. `source_repo` is one
of `tomoro-task` / `llmRun` / `rainmaker`, resolved to a path on disk by `REPO_ROOTS` in
`services/eval.py` — those checkouts live outside this repo and are never copied in.

## The deterministic verifiers never run the code they check

`src/claimcheck/domain/verifiers.py` holds four pure functions, one per `Claim.shape` in
`VALID_SHAPES` minus `"other"`, dispatched by `verify(claim, repo_root) -> Verdict`:

| shape | settles |
|---|---|
| `raises_propagates` | "raises X uncaught" against every `except` clause in the repo |
| `defaults_to` | "defaults to N" against the nearest AST default literal |
| `returns_type` | "returns X" against the enclosing function's return annotation |
| `markdown_reference` | a named file or command against what exists on disk |

**They read scanned source with `ast.parse` only.** No `import`, no `importlib`, no `exec`, no
`eval`, no `subprocess` of the code under scan — the same hard constraint `services/extract.py`
holds. Scanning a repo must never execute that repo. `tests/fixtures/sample_repo/unimportable.py`
exists to keep that honest: it is valid Python that blows up on import, and the suite scans it.

`shape == "other"` has no deterministic verifier and always resolves `unverifiable`; that shape is
the model-backed path in `services/model_verify.py`, which goes through the `ModelClient` port.

## Conventions that will bite you

- **Every exception class needs a handler or a `PROPAGATES:` line.** `scripts/check_exceptions.py`
  walks `src/claimcheck`, finds classes whose base name ends in `Error`/`Exception`, and fails
  unless each is either named in an `except` somewhere or carries a `PROPAGATES:` line in its
  docstring saying what happens when it reaches the top level. Say *why* it propagates, not just
  that it does.
- **Scanning needs no API key and no network.** `uv run claimcheck <path>` must keep working with
  `ANTHROPIC_API_KEY` unset; `tests/test_cli.py` asserts it. Only the model-backed path needs a key.
- **`ruff` runs with a wide rule set** (`S`, `TRY`, `FBT`, `T20`, `PL`, `ARG`, `PTH`, `ERA` and
  more) at line length 100, target py312. Printing is banned outside `cli/`, `scripts/` and the
  places already exempted in `pyproject.toml`.
- **mypy is `strict` with `warn_unreachable`.** New code needs full annotations; `tests/` is
  exempt from `disallow_untyped_defs` only.
- **`pytest-randomly` shuffles test order.** A test that depends on another test having run will
  fail intermittently, not reproducibly.
- Docstrings in this repo are themselves claims about the code. claimcheck scans its own source in
  its fixtures and evals — a stale docstring here is a self-inflicted finding.
