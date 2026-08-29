---
name: verify-labelled-set
description: Re-measure claimcheck's precision and recall against data/labelled_claims.jsonl and confirm every number quoted in CLAUDE.md, docstrings, commit bodies or a report still matches a fresh run. Use when the labelled set, domain/verifiers.py, domain/scorer.py or services/eval.py changed, when a document quotes a precision/recall/claim-count figure, or before reporting any metric from this repo.
---

# Verify the labelled set and every number quoted from it

claimcheck exists to catch prose that the code contradicts. Its own reported numbers are prose
about its own code, so they get checked the same way — by measuring, not by trusting the last
person who wrote them down.

**Never edit `data/labelled_claims.jsonl` to make a number come out right.** If a fresh
measurement disagrees with a document, the document is wrong or the checker regressed. Those are
the only two options. Relabelling a row to close the gap destroys the ground truth for every
future measurement, and nothing downstream can detect it.

## 1. Measure

```bash
make check-falsify
```

This prints three lines — `verify()`, `null_checker`, `empty_checker` — each with precision and
recall and their raw numerators/denominators. Copy the `verify()` line verbatim; that is the
measurement. The other two are the falsification control: `null_checker` must show recall 1.0 and
precision equal to the labelled contradiction rate, `empty_checker` must show recall 0.0 and
undefined precision. If either control is off, **stop** — the scoring path is broken and the
`verify()` line means nothing, regardless of how reasonable it looks.

`services/eval.py`'s `REPO_ROOTS` resolves `source_repo` to absolute paths outside this repo
(`tomoro-task`, `llmRun`, `rainmaker`). If one is missing on this machine the run raises
`UnknownSourceRepoError` or produces verdicts against a repo that is not there — check those
checkouts exist before trusting a number, and say so in the report if one was absent.

## 2. Confirm the frozen counts

```bash
uv run pytest tests/test_labelled_claims.py -v
```

`FROZEN_CLAIM_COUNT` and `FROZEN_CONTRADICTED_COUNT` are hand-counted integer literals, asserted
with `==`. They are not recomputed from the file. If these fail, the labelled set changed — find
out who changed it and why before touching the constants. The same two numbers appear again as
`_FROZEN_TOTAL_CLAIMS` and `_FROZEN_CONTRADICTED_COUNT` in `scripts/check_falsify.py`; both copies
must move together or `check-falsify` will fail with a confusing precision mismatch.

## 3. Find every quoted number and compare it

Search the tracked text for figures that claim to be measurements:

```bash
git grep -nE 'precision|recall|[0-9]+/[0-9]+|\b44\b|\b7\b' -- '*.md' 'src/**/*.py' 'scripts/*.py'
```

For each hit, decide: is this a number about the labelled set or the checker's score? If yes,
compare it against step 1 or step 2 output. Report each as one of:

- **matches** — quoted figure equals the fresh measurement.
- **stale** — quoted figure differs. Name the file, line, quoted value and measured value. Fix the
  document, not the data.
- **unmeasurable** — the figure is not reproducible from this repo (a live model run, a wall-clock
  time). Say so explicitly rather than implying it was checked.

## 4. Report

Give the `verify()` line verbatim, the two control lines, the frozen-count test result, and the
matches/stale/unmeasurable list. Do not round, do not summarise a precision as "good", and never
state a number you did not just see printed.
