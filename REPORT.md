# claimcheck — Report

## Method

`claimcheck` finds claims — natural-language assertions in docstrings, comments and markdown —
that the code they describe actually contradicts. It never imports or executes the code it scans
(`ast` and `tokenize` only), so it runs with no API key and no network for the deterministic path.

Pipeline: `services/extract.py` walks a repo and pulls claims into a shape (`raises_propagates`,
`defaults_to`, `returns_type`, `markdown_reference`, or `other`). `domain/verifiers.py` holds four
pure, deterministic verifiers, one per checkable shape, each returning `ok` / `contradicted` /
`unverifiable` / `unparsed`. `services/model_verify.py` + `adapters/anthropic_client.py` add a
model-backed verifier for claims no deterministic shape settles (`shape == "other"`, or anything a
deterministic verifier itself reports `unverifiable`). `domain/scorer.py` scores predictions
against a hand-labelled ground truth (`data/labelled_claims.jsonl`, 44 claims, built by hand from
three corpora — `llmRun`, `tomoro-task`, this run's own `rainmaker` — before any verifier existed,
so the checker could not be tuned to its own answer key).

**Architecture**: `cli -> services -> domain`, enforced by `import-linter` — `domain`
and `services` may never import `anthropic`/`httpx`/`requests`; only `adapters/anthropic_client.py`
does, confirmed live (temporarily adding a violating import and watching the gate fail).

**Falsification**: `make check-falsify` drives a null checker (reports every claim `contradicted`)
and an empty checker (reports nothing) through the identical scoring path used for the real
verifier — proving the scoring path itself is correct, independent of verifier quality. Null
checker: precision 0.1591 (7/44, chance level), recall 1.0000. Empty checker: recall 0.0000,
precision undefined (0 findings). Both match the mathematically expected pattern.

## Results

**Deterministic verifiers**, measured against all 44 labelled claims:

| | Value | Denominator |
|---|---|---|
| Precision | 0.40 | 2/5 (findings) |
| Recall | 0.29 | 2/7 (labelled-contradicted) |

**Model-backed verifier**, measured on the 30 claims the deterministic verifiers could not settle
(real Anthropic API calls, `claude-sonnet-5`, `thinking={"type":"disabled"}` in place of an
explicit temperature — Sonnet 5 rejects explicit sampling parameters with HTTP 400): **0 additional
true positives** (TP=0, FP=0, FN=4 on the 4 labelled-contradicted claims within that subset).
Combined precision/recall is therefore unchanged from deterministic-only. Real spend: **$0.0885
of the $2.00 cap**, 30 calls, cost read from actual per-call token usage.

**A self-correction worth reporting as a finding in its own right**: the first real measurement of
the deterministic verifiers showed TP=0 (precision/recall both 0.0), and the engineer who built
that measurement diagnosed it as a fundamental limitation of single-file AST verification against
multi-file evidence. That diagnosis was wrong. Independent reproduction found the actual cause: a
docstring-ownership check used exact-line equality against a `claim.line` that, for a multi-line
docstring, points at the specific sentence making the claim rather than the docstring's first
line — so the (correctly implemented) repo-wide handler search never ran. Fixing the line-matching
bug moved TP from 0 to 2. This is a direct, in-session instance of the exact defect class this
project exists to find — an assertion about what the code does or does not do, wrong, and not
caught by the test suite that shipped alongside it — caught only by a human/agent re-deriving the
result rather than trusting the first explanation.

## Error Analysis

- **lc-007** (a `raises_propagates` claim): stays `unverifiable` even after the line-matching fix.
  Its docstring describes what the function *catches*, not what it *raises* — a different claim
  shape than `_find_exception_name`'s function-docstring branch handles (it only looks at the
  function's own `raise` statements). A real, separate gap, not chased further this session.
- **lc-009** (new false positive from the same fix): a docstring saying "a handler exists
  elsewhere, by design" now resolves `contradicted`, because `verify_raises_propagates` checks
  only *whether* a handler exists anywhere, not the claim's *polarity* — it cannot yet distinguish
  "no handler exists" claims from "a handler exists" claims. This is exactly the false-positive
  risk this project's own scoring discipline weights double.
- **Model-backed verifier, 0/4 on the claims it was asked to judge**: not chased further this
  session given the $2 budget was not the binding constraint (spend stayed under $0.09) — time
  was. The likely cause is that the model was given only the claim's local file context, not the
  repo-wide search the deterministic `raises_propagates` verifier performs; a claim requiring
  cross-file evidence handed to the model without that evidence is unanswerable correctly by
  construction, independent of prompt quality.

## Limitations

- **The labelled set undershoots its own contradicted-claim floor.** Planned "at least 10
  genuinely contradicted"; landed at 7. The other three corpora's known defects had already been
  found and fixed by prior review passes before this run read them (two of the brief's four named
  "known instances" no longer held). Not remediated by inflating the set — false positives are
  weighted double in this project's own metric, and padding with marginal claims would produce a
  less trustworthy ground truth than 7 solid ones.
- **Precision/recall on a 7-item contradicted denominator is statistically noisy** — a single
  verifier miss moves recall by roughly 14 points. Read the headline numbers as directional.
- **The labelled set (and `make check-falsify`) depend on three sibling repos existing on disk**
  (`llmRun`, `tomoro-task`, `rainmaker`) at fixed paths — a deliberate choice (real corpora over
  vendored copies, so the ground truth cannot drift from its source), but it means the measurement
  is reproducible only alongside those corpora, not from a bare clone of this repo alone.
- **`verify_defaults_to`'s nearest-constant heuristic** searches a whole file by line-distance
  rather than scoping to the claim's enclosing function/class first — a file with several unrelated
  literals near a docstring could attach evidence to the wrong constant. No case in the current
  labelled set or fixture repo triggers this, but it is a real risk class, not fixed this session.
- **AST-helper duplication**: docstring-ownership lookup and exception-handler-walk logic now
  exist in three places (`domain/verifiers.py`, `services/extract.py`, `scripts/check_exceptions.py`).
  Flagged by review as worth consolidating before a fourth copy appears.
- **The model-backed verifier's `--check-imports` distinction is currently unreachable.** The MCP
  server (`mcp_server/scorer_server.py`) imports `claimcheck.domain.*` at module level, so a broken
  import kills the process before the exit-3 "SDK unavailable, logic sound" branch can run. The
  `--selftest`/`--check-imports` split exists structurally (confirmed by two independent read-backs)
  but its present value is a construction/schema probe, not a real import-failure probe.
- **`mcp_server/` and `test/harness_check.py` sit outside mypy's strict scope** — deliberate
  (both do untyped JSON-RPC-shaped dict handling) but a real type-checking gap.
- **A live finding about RainMaker itself, worth recording for the control plane, not just this
  target**: mid-session, this target's freshly-authored `.claude/skills` and `.claude/agents`
  became available as callable tools in the orchestrating session — contradicting RainMaker's own
  `CLAUDE.md` claim that a target's `.claude/` is invisible during a run. The orchestrator declined
  to use them for self-review (using RainMaker's own `reviewer`/`auditor` instead, to avoid the
  target grading its own freshly-written config) but did not fix or fully diagnose the discovery
  mechanism — likely tied to the Bash tool's working directory being inside the target, not the
  original launch directory. Worth a `/maintain` follow-up on RainMaker's side.

## AI-tool disclosure

Built entirely by Claude Code (Sonnet 5) under RainMaker orchestration — `engineer` for
implementation slices, `reviewer`/`auditor` for structural review and claim-integrity checks,
`skillwright` for the `.claude/` scaffold, `evaluator` for running checks a subagent must not grade
itself on. Every commit was independently re-verified by the orchestrating session (re-running the
gate, re-running the specific tests, reproducing bugs by hand) before being reported as done —
including catching and correcting a wrong self-diagnosis from one of the implementation slices (see
Results). The one live model-backed measurement used a real Anthropic API key supplied by the
human operator, at real (small) cost, reported above.
