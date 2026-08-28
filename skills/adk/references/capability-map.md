# Capability map

Eleven skills. The router (`/adk:adk`) picks one; each does its own discovery, so
any of them can also be invoked directly.

## Agent side

| Skill | Use it to | Reads | Writes | Cost |
|---|---|---|---|---|
| `/adk:agent-create` | scaffold an eval-ready agent, or add eval scaffolding to an existing one | your answers to a short interview | agent package, starter evalset, eval config, pytest runner | free |
| `/adk:agent-review` | review agent code against ADK 2.x rules, model policy, tool contracts, state handling | agent source (statically, never imported) | nothing, unless you approve fixes | free |
| `/adk:agent-debug` | triage a misbehaving or flaky agent to a layer and a fix | `adk run --jsonl` traces, sessions, eval results | a repro trace, optionally | free (running a repro costs model calls) |

## Evaluation side

| Skill | Use it to | Reads | Writes | Cost |
|---|---|---|---|---|
| `/adk:eval-create` | write eval cases, capture golden traces, migrate legacy files, plan coverage | agent tools and instruction, existing evalsets, traces | evalset files | free |
| `/adk:eval-criteria` | choose metrics and thresholds, write rubrics, register custom metrics | agent tools, existing config, backend | `eval_config.json` / `test_config.json` | free to write |
| `/adk:eval-review` | validate evalsets and configs, cross-check tools and arguments, measure coverage | eval files plus agent source | nothing | free |
| `/adk:eval-run` | run the suite the right way for the layout; repeat runs for pass@k; conformance | evalsets, config, runner | result files under `.adk/eval_history/` | depends on criteria |
| `/adk:eval-analyze` | explain results: quadrant, divergence point, rubric scorecards, reliability | result files | nothing | free |
| `/adk:eval-simulation` | set up simulated users and personas, or mock tool backends | agent tools, existing evalsets | scenario cases, simulator and env-sim configs | judge (and vertex for scenario generation) |
| `/adk:eval-ci` | add cost-tiered CI gates, assess evaluation maturity | repo layout, config, existing workflows | GitHub Actions workflows | free to write |

## Cost tiers

- **free** — deterministic. No model calls. `tool_trajectory_avg_score` and
  `response_match_score` are the only metrics in this tier, which is why they
  carry the per-change gate.
- **judge** — a judge model scores each invocation, multiplied by `num_samples`.
  Rubric metrics, `final_response_match_v2`, `hallucinations_v1`.
- **vertex** — the Vertex AI Gen AI Evaluation Service. `safety_v1`,
  `response_evaluation_score`, `multi_turn_*`. Needs a project and location.

## A normal sequence

Building something new:

```
/adk:agent-create  →  /adk:eval-create  →  /adk:eval-criteria  →  /adk:eval-run  →  /adk:eval-ci
```

Taking over something that exists:

```
/adk:adk  →  /adk:agent-review  →  /adk:eval-review  →  /adk:eval-create (close gaps)  →  /adk:eval-run
```

Something is failing:

```
/adk:eval-analyze  →  /adk:agent-debug  →  fix  →  /adk:eval-create (regression case)  →  /adk:eval-run --runs 5
```

## Where the deterministic work happens

Every skill's judgement is backed by a script under `skills/<skill>/scripts/`.
They are standard library only and never import `google.adk`, so they run on a
project whose environment is broken — which is often exactly when you need them.
The scripts that do need ADK (`load_agent.py`, `run_evals.py`, conformance) shell
out through a resolved interpreter and say which one they used.

---

Sources: this plugin.
