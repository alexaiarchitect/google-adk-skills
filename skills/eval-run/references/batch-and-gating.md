# Batch runs and gating

## Why a manifest

Once there is more than one agent, or more than one tier of evals, "is everything
green?" should be one command with one exit code. The registry says what exists:

```json
{
  "schema": "adk-skills.registry/1",
  "suites": [
    {"name": "trajectory_pr", "tier": "pr", "agent_dir": "app",
     "evalsets": ["tests/eval/evalsets/*.evalset.json"],
     "config": "tests/eval/eval_config.json", "runs": 1},
    {"name": "judge_nightly", "tier": "nightly", "agent_dir": "app",
     "evalsets": ["tests/eval/evalsets/*.evalset.json"],
     "config": "tests/eval/eval_config.nightly.json", "runs": 5,
     "pass_at_1_min": 0.95}
  ]
}
```

Paths are relative to the manifest, and evalsets may be globs. `--tier pr` runs
only the deterministic suites.

## The two tiers

| Tier | Metrics | When | Why |
|---|---|---|---|
| `pr` | `tool_trajectory_avg_score`, `response_match_score`† | every change | no model calls, seconds, and it catches skipped or reordered work |
| `nightly` | rubric metrics, `hallucinations_v1`, `safety_v1`, repeated runs | on a schedule | each costs model calls per invocation per sample |

† **`response_match_score` belongs in the per-change tier only if the wording
is genuinely fixed.** It is ROUGE-1 word overlap, so a correct paraphrase fails
it, and a tier that fails on rewording gets ignored and then disabled. Where the
reference text is not stable, set it to `0.0` and let
`tool_trajectory_avg_score` carry the tier; put `final_response_match_v2` in the
nightly tier when the *meaning* of the answer is what you need to gate on.

The split is about cost and speed, not importance. A judge metric on every push
is slow enough that people stop pushing, and expensive enough that someone
eventually turns it off — which is the worst outcome of the three.

## The exit-code contract

| Code | Meaning | In CI |
|---|---|---|
| 0 | every case passed | green |
| 1 | at least one case failed | red |
| 2 | the environment could not run it | red, and a different fix |
| 3 | nothing failed, but something was never scored | red with `--not-evaluated fail`, amber locally |

Code 3 exists because "the judge was unreachable" and "the agent regressed" are
different problems with different owners. In CI the default is to fail on it: a
suite that silently stops scoring is worse than one that fails loudly.

## Cost, before you spend it

A judge metric costs one model call **per invocation per sample**:

```
cases x turns per case x judge metrics x num_samples x runs
```

Twenty cases, two turns, two rubric metrics, three samples is 240 calls per run.
With `--runs 5` for pass@k, 1200. Say the number out loud before running it; it
is usually the whole argument for the tiering.

## Repeated runs

`--runs N` runs the suite N times, keeping each run's results as its own file,
which is what `pass_at_k.py` needs. Do not reach for
`AgentEvaluator(num_runs=N)` here: it averages scores and hides the variance.

How many: 5 while iterating, 10-20 before a release, 50-100 to qualify something
that spends money or talks to a person.

## Failing usefully

When a batch fails, report in this order:

1. **which suite**, so someone knows where to look;
2. **which cases**, by id;
3. **never-scored cases separately**, with the likely cause;
4. the result files, so `/adk:eval-analyze` can say why.

"3 of 24 failed" is a status. "`edge_frozen_cost_centre_no_submit` failed in the
nightly suite; the trajectory diverged at call 2" is a bug report.

---

Sources: this plugin's `run_registry.py` and `run_evals.py`;
`adk-python: cli/cli_tools_click.py`, `evaluation/agent_evaluator.py`.
