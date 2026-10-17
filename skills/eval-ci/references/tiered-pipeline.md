# Designing the tiers

## Why not one gate

A single eval gate has to be two incompatible things: cheap and fast enough to run
on every change, and thorough enough to trust before a release. Trying to be both
produces one of two failures — a gate so slow and expensive that someone turns it
off, or one so thin it catches nothing.

So: two tiers, with a clear rule for what belongs in each.

## The per-change tier

**Metrics.** Deterministic only: `tool_trajectory_avg_score`, and
`response_match_score` if the wording is genuinely fixed.

**Why that is enough to be useful.** Trajectory matching is the only metric that
sees a skipped validation, a reordered pipeline, or a right tool called with the
wrong id — and those are the regressions a code change actually causes. No model
calls, seconds to run.

**What it costs.** Nothing, which is why it can run on every push without anyone
weighing it up.

## The scheduled tier

**Metrics.** Everything that pays a model: rubric response quality, restraint
rubrics, `hallucinations_v1`, `safety_v1`, and the `multi_turn_*` metrics if you
use simulation.

**Plus repeated runs.** Five, so `pass@1` and fragility are measured rather than
assumed.

**Why it is scheduled.** A judge metric costs one model call per invocation per
sample:

```
cases x turns x judge metrics x num_samples x runs
```

Twenty cases, two turns, two rubric metrics at three samples, five runs is around
1200 calls. That is fine nightly and unacceptable per push.

**What it catches that the fast tier cannot.** Response quality drift, invented
data, an agent that quietly stops refusing, and flakiness.

## A release tier, when it is warranted

For something that spends money or talks to customers, a manual workflow with 20
to 50 runs and an explicit per-case floor. Five runs cannot distinguish 95% from
90%, and that distinction is the one a release decision turns on.

## What each tier does on failure

| Tier | On failure |
|---|---|
| per change | block the merge; the divergence point is in the uploaded artifact |
| nightly | notify, with the pass@k table and the failure summary in the job summary |
| release | block the release, with per-case reliability |

A nightly failure that only appears in a job nobody reads is not a gate. Wire it
to wherever the team actually looks.

## Past the release tier: deploy is not release

The tiers above gate code. For a deployed agent the last step can be gated too:
deploy the new revision with no traffic, tagged with the commit, run the gate,
and only then move traffic to it. The pattern, with a GitHub Actions sketch and
no shipped template: [shadow-rollout.md](shadow-rollout.md).

## NOT_EVALUATED in CI

Fail on it. A never-scored case means the judge could not be reached, a reference
is missing, or a rubric list is empty — the gate has stopped working, and that is
more urgent than a case that failed honestly. A pipeline that is green because
nothing was scored is the worst state of all, because nobody notices.

## Pin the versions

Pin `google-adk[eval]`, pin the Python version, and name the model explicitly
rather than letting an alias drift mid-quarter. An eval suite whose behaviour
changes because a dependency floated is a suite whose failures nobody believes.

## What a tier gates on

A tier's gate is more than "every case passed". The PR tier usually is exactly
that; a judge tier often wants a global pass threshold, and every tier that
compares with the default branch should forbid regressions and can hold token,
latency and tool-call budgets. The two merge rules, the budget flags and frozen
baselines: [merge-gates.md](merge-gates.md).

## Growing it

The tiers stay the same as the suite grows; only the manifest changes. Once there
is more than one agent or more than two configs, move to a registry and give each
suite a tier, so CI runs `run_registry.py --tier pr` and the manifest is the thing
people edit.

---

Sources: this plugin's `run_evals.py`, `run_registry.py`;
`adk-python: cli/cli_tools_click.py`, `evaluation/eval_metrics.py`. The
cost-tiering practice follows the LinkedIn Learning course *Evaluating AI Agents
with Google ADK*, recommended as background reading. Merge rules: "Evaluating AI
agents: moving past the vibe check", Google Developers Blog, 2026-10-15,
https://developers.googleblog.com/evaluating-ai-agents-moving-past-the-vibe-check/.
