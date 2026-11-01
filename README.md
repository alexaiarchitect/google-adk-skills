# `adk` — Claude Code skills for Google ADK agents and evaluations

Eleven skills for building, reviewing and evaluating Google Agent Development Kit
(Python) agents. Checked against **ADK 2.13**; 2.9 compatible. Python is where
ADK's evaluation tooling lives, so these skills apply even when the production
service is written in Go, Java, TypeScript or Kotlin: the evals stay in Python.

The premise: an agent's instruction is a promise written in prose, and **eval
cases are the only form of that promise a machine can check**. Prose drifts
quietly; a case that stops passing says so. Every skill here exists to keep the
two in step — to get the promise written down as cases, to check the cases say
what their author meant, and to run them somewhere a failure is noticed.

## Install

Run these commands inside Claude Code:

```text
/plugin marketplace add alexaiarchitect/google-adk-skills
/plugin install adk@alexaiarchitect-adk
```

To try a local checkout without installing:

```bash
claude --plugin-dir /path/to/google-adk-skills
```

Then start with `/adk:adk` on any agent directory, or go straight to the skill you
want.

## The skills

| Skill | Use it to |
|---|---|
| `/adk:adk` | discover a project and route to the right skill |
| `/adk:agent-create` | scaffold an eval-ready agent, or add eval scaffolding |
| `/adk:agent-review` | review an agent statically against ADK 2.x rules |
| `/adk:agent-debug` | triage looping, stalling, wrong tools, flakiness |
| `/adk:eval-create` | write eval cases; capture golden traces; migrate legacy files |
| `/adk:eval-criteria` | choose metrics and thresholds; write rubrics |
| `/adk:eval-review` | validate evalsets and configs; measure coverage |
| `/adk:eval-run` | run evals with an exit code that means something |
| `/adk:eval-analyze` | explain results; measure reliability |
| `/adk:eval-simulation` | simulated users, personas, mocked tool backends |
| `/adk:eval-ci` | cost-tiered CI gates; evaluation maturity |

## How they behave

Every skill follows the same five steps: **discover** the project with a script,
**report** what it found, **offer options** with their cost, **confirm** with a
question, then **act and verify**. Nothing is written and nothing billable runs
before you answer.

Costs are always stated up front as a tier: `free` (deterministic, no model
calls), `judge` (Gemini judge calls, multiplied by the sample count), `vertex`
(the Vertex AI Gen AI Evaluation Service).

## Three things worth knowing before you start

**Bare `adk eval` is not a gate.** Through 2.12.0 it exits 0 whatever happened:
it prints a pass/fail summary and returns success either way. From 2.13.0 it
exits 1 when a case fails or none ran, but it still counts never-scored cases as
failures and cannot gate on a pass rate, on regressions against a baseline or on
a budget. Every gate in this plugin goes through a wrapper that reads the result
files instead, so its exit code is the one to trust on every version. A CI step
that calls the command directly is green forever on a 2.12 pin, and
`lint_workflows.py` reports it (`CI001`: an error below 2.13 or with no pin, a
warning from 2.13).

**The judge model defaults to a deprecated id.** Leave `judge_model_options` out
of a config and you get `gemini-2.5-flash` sampled five times per invocation.
Generated configs here always name the model and the sample count.

**A reference-based metric cannot score a simulated conversation.** There is no
fixed expected answer, so trajectory and response matching are unavailable. Keep
a separate config for simulated suites.

**ADK 2.10 added four metrics you cannot configure** (unchanged through 2.13). `tool_call_count_v1`,
`inference_call_count_v1`, `token_usage_v1` and `invocation_duration_v1` are
appended to every eval and reported as informational values — lower is better, no
threshold, no pass or fail. Naming one in `criteria` raises at run time, so this
plugin refuses it (`CFG070`) and reports the values in their own table.

**A judge grades the question its metric asks.** A rubric that asks whether the
agent called or checked something, scored by the final-response metric, is
graded through the final answer rather than as a question about the
trajectory (`CFG075`, info); a compound rubric grades
inconsistently (`RUB006`); and through 2.13.0 the multi-turn trajectory judge
crashes on agents with built-in tools (`CFG076`). Streaming tools only stream
under `run_live` (`AGT081`). The nightly workflow compares each run with its
baseline and fails on a drop in any scored metric's mean
(`compare_runs.py --max-mean-drop`), so drift raises an alert overnight
instead of surfacing in the next pull request; `lint_workflows.py` notes a
scheduled run without that step (`CI007`). Before a climb is trusted, the judge
itself is audited: its stability on repeated scoring, chance-corrected agreement
with human labels, one property per rubric, and two runs that differ only in
`judge_model` compared for divergence (`eval-analyze` `calibration.md`).

**Three things released ADK gets wrong quietly.** A top-level `parallelism` key
in an eval config is read only by main after 2.13.0 and dropped by every release
(`CFG077`); an app directory named like `my-agent` is written by 2.13.0's `adk
create` and then refused by the loader (`ENV015`); and memory tools inside an
`AgentTool` search a fresh, empty store through 2.13.0 (`AGT082`).

**Two quiet costs the review now names.** Two skills on one agent described in
mostly the same words (`AGT083`): the model picks a skill from its name and
description alone, so the broader one takes the other's requests. And a
`max_output_tokens` under 1024 on a thinking Gemini model (`AGT084`): the cap is
a ceiling on thoughts and answer together, so hard turns come back truncated.
Both are info findings. Separately, through 2.13.0 a user turn the agent answers
with no event aborts a whole `adk eval` run with no case scored; `run_evals.py`
recognises it and says what to change.

**Two more things released ADK gets wrong quietly.** Through 2.13.0 a multi-turn
case whose turns carry different rubric lists is scored against the last turn's
list, so an earlier turn's failing rubric vanishes and the case can pass
(`EVS121`, with `EVS122` for a `rubric_id` repeated between case and turn); and
`LlmAgent(max_parallel_workers=...)` exists only on main after 2.13.0, so every
release refuses the agent at construction (`AGT085`).

## Requirements

Python 3.10+ for the plugin's own scripts, which are **standard library only** and
never import `google.adk`. That is deliberate: a review has to work on a project
whose environment is broken.

Anything that actually runs ADK resolves an interpreter in this order:

1. `$ADK_PYTHON`
2. a virtualenv at or above the agent
3. the interpreter owning an `adk` on `PATH`
4. `uv run --with "google-adk[eval]==2.13.*"`

```bash
# either
brew install uv
# or
python3 -m venv .venv && .venv/bin/pip install "google-adk[eval]==2.13.*"
export ADK_PYTHON=.venv/bin/python
```

## Backend

Evaluation defaults to Vertex AI, configured in the **agent's own** `.env`, which
is the file ADK reads:

```dotenv
GOOGLE_GENAI_USE_VERTEXAI=TRUE
GOOGLE_CLOUD_PROJECT=your-project
GOOGLE_CLOUD_LOCATION=us-central1
MODEL_NAME=gemini-flash-latest
```

A Gemini API key works for judge metrics and for the Vertex-served ones too —
`safety_v1`, `response_evaluation_score`, `multi_turn_*` — because the evaluation
facade reads `GOOGLE_API_KEY` before any project. A project buys Workload
Identity in CI and the Vertex-hosted session, memory and artifact services, not
extra metrics. Check before you run a suite:

```bash
python3 skills/adk/scripts/check_env.py <agent-dir> --config <config>
```

## Beyond the evalset

Pages that fold a Google article into the plugin's own commands (the first five
added in the 2.13 sync, the next five in the sync with main after 2.13.0, the
next three in the sync with main through 2026-11-02, the next four in the sync
with main through 2026-11-04, the last two in the sync with main through
2026-11-05):

| Page | Covers |
|---|---|
| `skills/eval-analyze/references/improvement-loop.md` | the five-stage improvement loop on this plugin's scripts, why whatever proposes a fix never grades it, deltas over absolute judge scores, one stable custom metric beside the adaptive ones, and what the hosted platform (`agents-cli eval`, online monitors) adds after launch |
| `skills/eval-simulation/references/user-simulation.md` | live and voice evals: `llm_audio` with its real defaults, `live_model_config`, voices and personas, `adk web`'s Live toggle |
| `skills/eval-create/references/case-taxonomy.md` | the `resume` category: simulating idle time by seeding `session_input.state`, hold-at-gate and proceed-from-step cases |
| `skills/agent-create/references/state-and-memory.md` | long-running agents: a step machine in state, `{key}` / `{key?}` placeholders, tools as checkpoints, resuming with a `state_delta` |
| `skills/eval-create/references/behavioral-tests.md` | policies as contracts: enforce outside the model, test the enforcement deterministically |
| `skills/eval-ci/references/merge-gates.md` | Day 0/1/2, the two merge rules (`--min-pass-rate`, `--max-regressions 0`), frozen golden suites, token/latency/tool-call budgets |
| `skills/eval-create/references/behavioral-tests.md` | pytest assertions on discrete actions, one failure mode at a time, batch pass rates (`gen_test_eval.py --behavioral`) |
| `skills/agent-create/references/skills-and-progressive-disclosure.md` | ADK `SkillToolset` skills: `SKILL.md` limits, L1/L2/L3, lifecycles, 2.12 script artifacts, activation evals |
| `skills/agent-create/references/context-engineering.md` | the compiled-view model: compaction, `include_contents`, agents as tools, artifacts |
| `skills/agent-create/references/live-agents.md` (new) | voice agents that never go silent: streaming and non-blocking tools, `stop_streaming`, barge-in, `response_scheduling`, why their evals need `live_model_config` (`AGT081`) |
| `skills/eval-analyze/references/improvement-loop.md` | hill-climbing: per-case tables over means, a constant judge across model swaps, scheduled runs that alert on a drop, trajectory over summary, live and plan-only runs, golden sets |
| `skills/eval-criteria/references/rubric-authoring.md` | atomic expectations (`RUB006`), action rubrics need a trajectory view (`CFG075`), built-in tools and the multi-turn judge (`CFG076`) |
| `skills/eval-ci/references/merge-gates.md` | cheap deterministic gates first, a fresh eval report per PR, expensive suites on demand, the `agents-cli eval` mapping |
| `skills/agent-create/references/workflow-graphs.md` | remote A2A steps: an explicit step enum, an unreachable remote held for manual review, deterministic validators audited by repetition |
| `skills/eval-analyze/references/calibration.md` | auditing the judge: five biases, stability, chance-corrected agreement, decomposed rubrics, two judges compared with `compare_runs.py` |
| `skills/agent-create/references/context-engineering.md` | heavy generation (a UI catalog) in its own sub-agent, client-side validation, retry with the error then fall back |
| `skills/agent-create/references/app-and-plugins.md` | tiered actions: read-only tools unattended, changes behind `require_confirmation`, constraints in the execution layer rather than the prompt |
| `skills/agent-create/references/skills-and-progressive-disclosure.md` | evaluating skills: trigger collisions and negative sibling cases (`AGT083`), paired with/without runs at three repetitions, deleting a skill that is pure context tax, clean sandboxes, habits over documentation, a thin discovery skill |
| `skills/agent-review/references/model-policy.md` | an output cap is not a thinking budget (`AGT084`), a fast model at low thinking before a router, caches per model; `workflow-graphs.md`: classifier routing with one yes/no question per factor and cost per successful task |
| `skills/eval-ci/references/merge-gates.md` | gate, then score: hard checks first, correctness, efficiency and security kept apart, per-constraint versus per-case rates, a held-out set; long-horizon deliverable cases in `case-taxonomy.md` |
| `skills/agent-create/references/skills-and-progressive-disclosure.md` | a discovery skill that loads specialised skills on demand, guardrail skills that validate CLI syntax before a state-changing call |
| `skills/adk/references/environment.md` | what ADK 2.12 loads lazily and why a server needs a warm-up request; `eval-analyze` `reliability.md`: timing a speed change with an instant model, a hidden benchmark, identical requests and medians, then reviewing what an agent proposed; ADK's own `.agents/skills` as the L1/L2/L3 example |
| `skills/eval-analyze/references/improvement-loop.md` | automated prompt or skill search: paired candidate and baseline runs, a holdout the search never sees, the answer leak, plateaus and slow model windows; held-out cases in `merge-gates.md` |

The `adk-recipes` three-layer layout and its plugin-skill `EVAL.yaml` format are
in `skills/adk/references/layouts.md` and `skills/eval-create/references/managing-evalsets.md`.

## Layouts

All three are detected automatically:

```
recipes:  <project>/app/agent.py          + <project>/tests/eval/{eval_config.json,evalsets/}
flat:     <agent>/agent.py                + <agent>/{*.test.json,test_config.json}
src:      <project>/src/<pkg>/agent.py    (agents-cli projects; `src/` is the agents dir)
```

agents-cli datasets (`tests/eval/datasets/*.json` with `tests/eval/eval_config.yaml`)
are detected and listed, but not read or validated: they are not ADK evalsets.

The difference that matters: in the flat layout `test_config.json` sits beside the
evalset, so `adk eval` finds it. In the recipes layout the config must always be
passed with `--config_file_path`.

## Development

```bash
python3 -m pytest tests -q          # the plugin's own tests
claude plugin validate . --strict
# the skills' own behavioural suite (Claude Code 2.1.318+), smoke run:
claude plugin eval . --scaffold --allow-tools Bash Write Edit --trust-plugin \
  --runs 1 --ablation with-without --max-cost-usd 30 --threshold 0.8 --no-publish
```

Each case in `evals/` runs with and without the plugin: a delta of zero means the
skill never fired, and two equal passes mean the skill adds nothing. Case layout
and how to read a run: [`evals/README.md`](evals/README.md).

Scripts are exercised as subprocesses, the way a skill runs them. ADK-backed
scripts run against a fake interpreter in `tests/fixtures/bin/`, so the suite
needs no network and no credentials.

## Attribution

ADK behaviour is cited from `google/adk-python`, `google/adk-docs` and
`google/adk-recipes` (Apache-2.0) — see `THIRD_PARTY_NOTICES.md`. Every reference
page ends with the files it was distilled from, so any claim about ADK can be
checked.

The context-caching, eval-engineering, shadow-rollout, merge-gates,
behavioural-tests, skills, context-engineering, layouts, improvement-loop,
user-simulation, case-taxonomy, state-and-memory, workflows, maturity,
rubric-authoring, live-agents, workflow-graphs, app-and-plugins, model-policy, environment and reliability pages draw on
Google-authored articles (on context caching in agent harnesses, continuous
evaluation, eval-driven development at Firebase, moving past the vibe check,
harness engineering, building ADK agents with skills, context-aware multi-agent
design, the `adk-recipes` tour, the agent quality flywheel, live and voice
evaluation, long-running resumable agents, the Agent Platform evaluation GA,
Agents CLI for any framework, ADK in five languages, zero-trust agents,
hill-climbing with Agents CLI evaluation, four eval-engineering lessons,
shipping Agent Skills like products, voice agents that never go silent,
cross-language A2A teams, governed self-evolving agents, evaluating agent
skills, cost per task with classifier routing, Physical-Engineering's Last Exam,
the Google Cloud Developer plugin, optimising ADK's own codebase overnight, and
an agent skill optimizer),
paraphrased and cited by title and URL; the list is in `THIRD_PARTY_NOTICES.md`.

The evaluation practices described here — trajectory over final answer, the case
taxonomy, the branch-coverage rule, restraint rubrics, pass@k, the failure
taxonomy, the maturity ladder — were informed by the LinkedIn Learning course
*Evaluating AI Agents with Google ADK* by Jigyasa Grover, recommended as
background reading. Its exercise files are not redistributable, so nothing from
that repository is included: every reference, template and fixture here is
original work, and the fixtures use an unrelated domain.
