---
name: eval-analyze
description: >-
  Explains Google ADK eval results and measures reliability. Reads the result
  files in .adk/eval_history, reports per-case and per-metric status, places
  each failure in the trajectory-versus-response quadrant, finds the first
  point where the actual trajectory diverged from the expected one, reads rubric
  verdicts by id, separates NOT_EVALUATED from failure and names its likely
  cause, and computes pass@k and fragility across repeated runs. Use when asked
  "why did this eval fail", "show me the results", "what does this score mean",
  "is my agent reliable", "compute pass@k", "how flaky is this case", or "should
  I trust this suite". Don't use to run evals (use `/adk:eval-run`), to fix the
  agent (use `/adk:agent-debug`), or to change thresholds (use
  `/adk:eval-criteria`).
argument-hint: "[agent-dir | result.json] [--dir eval_history]"
allowed-tools: Read, Grep, Glob, AskUserQuestion, Skill, Bash(python3 *)
---

# Read ADK eval results

A score is not a diagnosis. This skill turns result files into three things
someone can act on: **which quadrant** a failure is in, **where** the trajectory
diverged, and **how often** the case actually passes.

`${CLAUDE_SKILL_DIR}` is set by the skill runtime to this skill's own
directory. Use it verbatim in every command below; a sibling skill's scripts
are at `${CLAUDE_SKILL_DIR}/../<skill>/scripts/`.

## Flow

1. **Read the results.**

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/summarize_results.py <agent-dir> --latest
   python3 ${CLAUDE_SKILL_DIR}/scripts/pass_at_k.py --dir <agent-dir>/.adk/eval_history --evalset <id>
   ```

2. **Report.** Per case: status, each metric against its threshold, the quadrant,
   the divergence point, failed rubrics by id. Lead with the count and the single
   most informative failure.

3. **Options.** Usually: triage the worst case with `/adk:agent-debug`, re-run
   several times to see whether it is flaky, adjust a threshold that is measuring
   the wrong thing, or add a regression case now that the failure is understood.

4. **Confirm and hand off.** This skill does not edit anything.

## The quadrant

Trajectory score against response score. Q3 is why trajectory metrics exist:

| | Response good | Response weak |
|---|---|---|
| **Trajectory good** | Q1 — keep the case as a guard | Q2 — presentation, often the metric rather than the agent |
| **Trajectory weak** | **Q3 — the dangerous one** | Q4 — a plain logic failure |

In Q3 the answer reads well, the agent skipped or reordered work to produce it,
and every text-only metric passes it. Treat it as a real failure: tighten the
instruction's ordering and refusal rules, and check whether the answer was
grounded in tool output at all.

What each quadrant implies in full, and the four kinds of divergence point
(`missing`, `extra`, `wrong_tool`, `arg_mismatch`) with where each one's fix
belongs, are in [diagnosis.md](references/diagnosis.md).

## The divergence point

The first call where actual and expected part company. Everything after it is
downstream noise, so that is where a fix belongs.

An early `arg_mismatch` followed by several correct-looking calls is a
**cascading error**: step one normalized something wrong and nothing revalidated
it. The symptom is at the end; the cause is at the start, and fixing the last
call fixes nothing.

## NOT_EVALUATED

Not a regression. The metric never produced a score, and there are three usual
reasons:

- the judge could not be reached — credentials, quota, or a Vertex-only metric
  with no project configured;
- no reference response, for a metric that compares against one;
- an empty rubric list, counting criterion-level rubrics plus type-matching
  per-case rubrics.

`adk eval`'s printed summary counts these as failures; the result files do not.
Say which you are looking at.

## INFORMATIONAL (2.10+)

ADK 2.10 and later append four efficiency metrics — `tool_call_count_v1`,
`inference_call_count_v1`, `token_usage_v1`, `invocation_duration_v1` — to every
eval, whatever the config says. Their entries carry `eval_status: 4` and no
threshold: they report a value, lower is better, and they pass and fail nothing.
Report them in their own table, never in the pass/fail count, and read a jump as
a question rather than a regression. Shape and the token breakdown:
[result-schema.md](references/result-schema.md).

`cached_tokens` (inside `prompt_tokens`) is where context caching shows up: the
summary's `cached` column and cache-hit ratio. Zero on every case usually means
caching is off, the suite has no multi-turn case, or the prefix changes per
request — see
[`../agent-create/references/context-caching.md`](../agent-create/references/context-caching.md).

## Before and after

When a change is meant to improve something — a prompt edit, caching, a new
tool — compare two runs rather than eyeballing two summaries:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/compare_runs.py <baseline> <candidate>
```

Pass rate, per-case verdict flips, mean tokens (prompt / cached / output),
duration and call counts, with deltas. A cheaper run that flipped a case to
failed is a regression, and the script exits 1 for it. This is the measuring
step of the eval-driven loop in `../adk/references/eval-engineering.md`. The
whole loop — five stages, the rule that whatever proposed a fix never grades
it, deltas over absolute judge scores, and one stable custom metric beside the
adaptive ones — is in [improvement-loop.md](references/improvement-loop.md).

As a merge gate it takes the two rules and the budgets: `--min-pass-rate 0.95
--max-regressions 0`, plus `--max-mean-prompt-tokens N`, `--max-mean-seconds S`,
`--max-mean-tool-calls N` (a figure reported as `n/a` never fails a budget), and
for a scheduled run `--max-mean-drop D`, which fails when any scored metric's
mean over the shared cases falls by more than `D` (alert on a drop, not a low
absolute).
With `--json` the output gains `gates` (each gate, its limit and value) and
`gates_passed`. Which to set and why:
[`../eval-ci/references/merge-gates.md`](../eval-ci/references/merge-gates.md).

## Reliability

`pass@1` is production behaviour: one attempt per request. `pass@k` is the chance
one of k attempts succeeds. The gap is **fragility**.

| pass@1 | Verdict |
|---|---|
| ≥ 0.95 | reliable |
| ≥ 0.70 | marginal |
| below | unreliable |

A case that passes 2 runs in 5 is not "mostly working"; it fails three requests
in five. Report `pass@1` first and treat a high `pass@k` as a statement about
retries, not about quality.

How many runs: 5 separates "usually works" from "often fails" but not 95% from
90%. Use 10-20 before a release, 50-100 to qualify something that spends money.

## Decision rules

- **Read the first divergence, not the last symptom.**
- **Read the per-case table before the mean.** Two unrelated changes can move
  a mean by the same amount and break different cases; keep the judge model
  fixed across a model swap, or the delta measures both.
- **A flaky case and a broken case need different fixes.** Before triaging,
  establish which it is: `/adk:eval-run --runs 5`.
- **Never average away the variance.** `AgentEvaluator(num_runs=N)` does exactly
  that, which is why repeated runs are kept separate here.
- **Calibrate occasionally.** Sample passing cases and check you agree they
  should have passed. Criteria that are too lenient produce a green suite and a
  bad agent, and nothing in the system notices.

## References

| Question | File |
|---|---|
| The result file's shape and every status | [result-schema.md](references/result-schema.md) |
| Quadrant and divergence in depth | [diagnosis.md](references/diagnosis.md) |
| pass@k, fragility, run counts | [reliability.md](references/reliability.md) |
| Human calibration: cadence, sample size, what to do on disagreement | [calibration.md](references/calibration.md) |
| Gating a merge on a comparison: pass threshold, regressions, budgets | [merge-gates.md](../eval-ci/references/merge-gates.md) |
| The improvement loop, who grades a fix, and what the hosted platform adds after launch | [improvement-loop.md](references/improvement-loop.md) |

## Scripts

| Command | Does | Exit codes |
|---|---|---|
| `scripts/summarize_results.py <path> [--dir D] [--latest] [--eval-id ID] [--json]` | per-case diagnosis, plus an "Efficiency (informational, lower is better)" table with the token breakdown, a `cached` column, a per-case "Cache-hit ratio" line and a run-level "Context cache served X%" when reported; JSON carries `case["efficiency"]`, `run["efficiency"]`, `cases[].cache` and `run.cache_hit_ratio`. On result files from ADK 2.13+ it also shows the model / tool / framework split of `invocation_duration_v1` (`details.duration_details`) and the judge's explanation (the `rubric_id` "explanation" entry, shown as the metric's rationale and `rubric_explanations`, never counted as a verdict) | 0 passed, 1 failures, 3 only never-scored, 2 nothing to read |
| `scripts/pass_at_k.py [--dir D] [--evalset ID] [--last N] [--k 1,3,5]` | reliability across runs | 0 all reliable, 1 not, 2 nothing to read |
| `scripts/compare_runs.py <baseline> <candidate> [--latest] [--evalset ID] [--min-pass-rate P] [--max-regressions N] [--max-mean-prompt-tokens N] [--max-mean-seconds S] [--max-mean-tool-calls N] [--max-mean-drop D] [--json]` | before/after table, plus "Metric means (shared cases)" (json: `metric_means`): pass rate, verdict flips, added/removed cases, per-invocation prompt / cached / uncached-prompt / output tokens, cache-hit ratio, seconds, tool and model calls, with deltas; verdict `REGRESSION` / `IMPROVED` / `NO REGRESSION`; missing figures read `n/a` | 0 no regression, 1 pass rate dropped, more than `--max-regressions` cases (default 0) flipped to failed, the candidate below `--min-pass-rate`, a budget exceeded, or a scored metric's mean over shared cases fell by more than `--max-mean-drop` on its own scale; 2 nothing to compare (or a directory holding several runs without `--latest`) |
| `scripts/calibration_sample.py [--dir D] [--n 5] [--seed 0]` | passing cases for human review, sampled on the gate metrics only | 0, 2 nothing to read |

## Completion checklist

- [ ] Every failing case has a quadrant and a divergence point.
- [ ] NOT_EVALUATED cases were reported separately, with a likely cause.
- [ ] Efficiency values were reported apart from the verdicts, and counted in
      neither the passed nor the failed total.
- [ ] Failed rubrics were named by id, not counted.
- [ ] Where reliability matters, `pass@1` was reported, not just `pass@k`.
- [ ] The user was pointed at the next skill rather than left with numbers.
