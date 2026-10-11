# `adk` — Claude Code skills for Google ADK agents and evaluations

Eleven skills for building, reviewing and evaluating Google Agent Development Kit
(Python) agents. Checked against **ADK 2.11**; 2.9 compatible.

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

**`adk eval` always exits 0.** It prints a pass/fail summary and returns success
either way, and counts never-scored cases as failures. Every gate in this plugin
goes through a wrapper that reads the result files instead. A CI step that calls
the command directly is green forever.

**The judge model defaults to a deprecated id.** Leave `judge_model_options` out
of a config and you get `gemini-2.5-flash` sampled five times per invocation.
Generated configs here always name the model and the sample count.

**A reference-based metric cannot score a simulated conversation.** There is no
fixed expected answer, so trajectory and response matching are unavailable. Keep
a separate config for simulated suites.

**ADK 2.10 added four metrics you cannot configure** (unchanged in 2.11). `tool_call_count_v1`,
`inference_call_count_v1`, `token_usage_v1` and `invocation_duration_v1` are
appended to every eval and reported as informational values — lower is better, no
threshold, no pass or fail. Naming one in `criteria` raises at run time, so this
plugin refuses it (`CFG070`) and reports the values in their own table.

## Requirements

Python 3.10+ for the plugin's own scripts, which are **standard library only** and
never import `google.adk`. That is deliberate: a review has to work on a project
whose environment is broken.

Anything that actually runs ADK resolves an interpreter in this order:

1. `$ADK_PYTHON`
2. a virtualenv at or above the agent
3. the interpreter owning an `adk` on `PATH`
4. `uv run --with "google-adk[eval]==2.11.*"`

```bash
# either
brew install uv
# or
python3 -m venv .venv && .venv/bin/pip install "google-adk[eval]==2.11.*"
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

## Layouts

Both are detected automatically:

```
recipes:  <project>/app/agent.py          + <project>/tests/eval/{eval_config.json,evalsets/}
flat:     <agent>/agent.py                + <agent>/{*.test.json,test_config.json}
```

The difference that matters: in the flat layout `test_config.json` sits beside the
evalset, so `adk eval` finds it. In the recipes layout the config must always be
passed with `--config_file_path`.

## Development

```bash
python3 -m pytest tests -q          # the plugin's own tests
claude plugin validate . --strict
claude plugin eval .               # skill-level eval cases, if enabled for you
```

Scripts are exercised as subprocesses, the way a skill runs them. ADK-backed
scripts run against a fake interpreter in `tests/fixtures/bin/`, so the suite
needs no network and no credentials.

## Attribution

ADK behaviour is cited from `google/adk-python`, `google/adk-docs` and
`google/adk-recipes` (Apache-2.0) — see `THIRD_PARTY_NOTICES.md`. Every reference
page ends with the files it was distilled from, so any claim about ADK can be
checked.

The context-caching, eval-engineering and shadow-rollout pages draw on three
Google-authored articles (on context caching in agent harnesses, on continuous
evaluation, and on eval-driven development at Firebase), paraphrased and cited by
title and URL; the list is in `THIRD_PARTY_NOTICES.md`.

The evaluation practices described here — trajectory over final answer, the case
taxonomy, the branch-coverage rule, restraint rubrics, pass@k, the failure
taxonomy, the maturity ladder — were informed by the LinkedIn Learning course
*Evaluating AI Agents with Google ADK* by Jigyasa Grover, recommended as
background reading. Its exercise files are not redistributable, so nothing from
that repository is included: every reference, template and fixture here is
original work, and the fixtures use an unrelated domain.
