# Merge gates: two rules, four traps, many small steps

A gate is only worth having if it can say no for the right reason. This page
covers what a merge gate on an ADK agent should check, how this plugin's scripts
check it, and the four ways a gate goes quietly wrong. The framing (Day 0, Day 1,
Day 2, the two merge rules, the anti-patterns) comes from Google's "moving past
the vibe check" post; the commands are this plugin's.

## Day 0, Day 1, Day 2 on the maturity ladder

The post splits an evaluation practice into three stages. They line up with the
ladder in [maturity.md](maturity.md):

| Stage | What happens | Ladder | Here |
|---|---|---|---|
| **Day 0**: discover | write the critical user journeys, break each into concrete scenarios, simulate users against the agent, and read 5 to 20 traces end to end, marking every wrong step | **L0** while it stays here | `/adk:eval-create` (cases from journeys), `/adk:eval-simulation` (simulated users), `/adk:agent-debug` (reading a trace) |
| **Day 1**: automate | four layers of checks, from cheap to expensive: unit tests on tools and callbacks, deterministic metrics on trajectories, LLM-judge rubrics, integration tests through the real runner; then **freeze a golden dataset** | **L1 to L2** | behavioural tests ([behavioral-tests.md](../../eval-create/references/behavioral-tests.md)), `tool_trajectory_avg_score`, rubric metrics (`/adk:eval-criteria`), `adk eval` through `run_evals.py`, a `"frozen": true` suite |
| **Day 2**: gate and watch | a headless run on every change with hard merge rules, traces exported to a warehouse, dashboards over time | **L2 to L4** | `/adk:eval-ci` workflows, `compare_runs.py` gates, nightly pass@k, production failures fed back as `regression_*` cases |

"Day 0 never left" is the honest description of most L0 projects: careful
reading, nothing replayable. The stage names are about what exists, not about
time: a project can be on Day 2 for its refund flow and Day 0 for a feature
added last week.

## The two merge rules

The post proposes two rules for blocking a merge, and they need each other:

1. **A global pass threshold.** The candidate's pass rate over the gating suite
   must reach a fixed bar, 95% in the post's example.
2. **Zero regressions on the core metrics.** No case that passed on the baseline
   may fail on the candidate.

The threshold alone lets a swap through: fix three cases, break the one that
matters, and the rate goes up. The regression rule alone lets a suite stuck at
60% merge forever, as long as it does not get worse. Together they say "good
enough, and not worse anywhere".

### How the scripts implement them

```bash
# a single run, rule 1 only: exit 1 below 95%
python3 ${CLAUDE_SKILL_DIR}/../eval-run/scripts/run_evals.py . \
    --config tests/eval/eval_config.json --min-pass-rate 0.95

# baseline versus candidate, both rules: exit 1 on either
python3 ${CLAUDE_SKILL_DIR}/../eval-analyze/scripts/compare_runs.py \
    baseline-history/ app/ --latest --min-pass-rate 0.95 --max-regressions 0
```

| Flag | Script | Fails (exit 1) when |
|---|---|---|
| `--min-pass-rate P` | `run_evals.py` | the run's pass rate is below `P` (a fraction, `0.95`); the rate is passed over every case, so a never-scored case counts against it |
| `--min-pass-rate P` | `compare_runs.py` | the **candidate's** pass rate is below `P` |
| `--max-regressions N` | `compare_runs.py` | more than `N` cases flipped from passed to failed; the default is 0 |

Without `--min-pass-rate`, `run_evals.py` keeps its plain contract: exit 1 on any
failed case, 3 when nothing failed but a case was never scored, 2 when the
environment could not run it (`/adk:eval-run` `batch-and-gating.md`). A threshold is for the suite where you have decided
that one flaky judge case should not block a merge; the deterministic PR tier
usually wants every case green, which is the default.

The baseline is a run of the **default branch**, not the previous commit of the
pull request. In CI that means restoring the default branch's last
`eval_history` artifact (or running the suite on it first) and passing that
directory as the first argument. "Core metrics" is a choice of suite: gate on the
frozen golden suite, not on the exploratory cases added this week.

Bare `adk eval` cannot implement either rule. It exits 0 on a failure on every
release through 2.12.0; main after the tag fixes that, but even then it reports
a failure, not a rate or a flip. The wrapper's exit code is the gate.

## Four anti-patterns, and the answer to each

### 1. Baseline erosion

Tuning an agent against a suite, then deleting or rewriting the cases it keeps
failing, produces a green suite and a worse agent. The post's rule: never swap
or delete golden cases mid-tuning.

**Here:** mark the golden suite frozen in `eval_registry.json`:

```json
{"name": "golden", "tier": "pr", "agent_dir": "app",
 "evalsets": ["tests/eval/evalsets/golden.evalset.json"],
 "config": "tests/eval/eval_config.json", "frozen": true}
```

`manage_cases.py remove|rename|move|split|dedupe|rebase-app-name` then refuses any
case in that suite with finding `EVS120` (exit 2). `--unfreeze` overrides it;
make that its own reviewed change with the reason in the description, and
re-take the baseline after it. `run_registry.py --init` writes `"frozen": false`.
Adding cases to a frozen suite is fine: growth is not erosion.

### 2. Fuzzy 1 to 5 grading

A judge asked for a score from 1 to 5 drifts between runs and between judges,
and nobody can say what a 3 means. Ask yes/no questions instead.

**Here:** already the rule. ADK's rubric metrics score each rubric 1.0 or 0.0
after a majority vote over `num_samples`, and `gen_rubrics.py --lint` flags
graded scales, subjective words and compound sentences
(`/adk:eval-criteria` `rubric-authoring.md`).

### 3. Ignoring operational cost

A correct, polite answer that costs 60 cents and takes 40 seconds is still a
failure for most products; that is the post's example, and its point. Latency, tokens, cost per query,
tool-call counts and cache use belong in the gate, not in a dashboard nobody
opens.

**Here:** ADK 2.10+ reports four efficiency metrics on every run (informational,
never pass or fail on their own). `compare_runs.py` turns them into budgets:

| Flag | Fails when the candidate's mean per invocation exceeds |
|---|---|
| `--max-mean-prompt-tokens N` | `N` prompt tokens |
| `--max-mean-seconds S` | `S` seconds |
| `--max-mean-tool-calls N` | `N` tool calls |

A figure the run did not report (a 2.9 runner, a model that reports no usage)
reads `n/a` and never fails a budget. Cost per query is tokens times your price;
the cache-hit ratio column says how much of the prompt was billed at the cached
rate (`/adk:agent-create` `context-caching.md`). Set budgets from a measured
baseline plus headroom, not from a guess.

### 4. Framework-locked evaluation

An evaluation that only one framework can read is lost when the framework
changes. The post's answer is OpenTelemetry traces with the OpenInference
conventions, so any backend can score them.

**Here:** ADK emits OpenTelemetry spans for agent, model and tool steps
(`/adk:agent-debug` `evidence.md`), and the `BigQueryAgentAnalyticsPlugin`
(`google.adk.plugins.bigquery_agent_analytics_plugin`) is the in-box route to a
warehouse. The evalset format is ADK's own, so keep the portable parts portable:
the journey list, the scenarios and the rubric sentences are plain text that
survives a framework change.

## 50 to 60 small iterations

The post reports that production quality took 50 to 60 small iterations, each
changing one variable. That is the loop `compare_runs.py` exists for:

1. take the baseline on the frozen suite;
2. change **one** thing: an instruction sentence, a tool description, a
   threshold, a model;
3. run, compare with both merge rules and the budgets;
4. keep it only if nothing regressed, and write down what changed.

Two variables at once make the result unreadable: a fix and a regression cancel
out, and the next iteration starts from a guess. Use `--runs 5` and `pass_at_k.py`
when a judge metric is involved, or one noisy run decides the iteration.

## Other harnesses

- **`agent-eval`** (GoogleCloudPlatform/professional-services) is an open-source
  evaluation toolkit the post links. It is an alternative harness, not something
  this plugin drives; the merge rules above apply to it unchanged.
- **User simulation** for Day 0 scenarios: `/adk:eval-simulation`.
- **Adaptive rubrics** (Vertex-generated, per prompt) are the Vertex-served rubric
  metrics: `/adk:eval-criteria` `vertex-requirements.md`.

---

Sources: Selbie, Zamora, Carah and DeMartino, "Evaluating AI agents: moving past
the vibe check", Google Developers Blog, 2026-10-15,
https://developers.googleblog.com/evaluating-ai-agents-moving-past-the-vibe-check/
(Day 0/1/2, the two merge rules, the four anti-patterns, the 50 to 60 iterations;
paraphrased). This plugin's `run_evals.py`, `compare_runs.py`, `manage_cases.py`,
`run_registry.py`, `gen_rubrics.py`; `adk-python: cli/cli_tools_click.py` (bare
`adk eval` exit 0 measured on 2.12.0, fix `c2ff634` on main),
`evaluation/_efficiency_evaluators.py`, `plugins/bigquery_agent_analytics_plugin.py`
(2.12.0 wheel).
