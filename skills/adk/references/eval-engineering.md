# Eval engineering: how the pieces fit

This plugin's skills each do one job. This page is the frame that connects them:
two modes of evaluation, three levels of metric, a dataset treated as code, and
a loop that writes the test before the change. The frame comes from two
Google-authored posts (sources below); the mapping onto ADK and onto this
plugin's skills is ours.

## Two modes: discovery and defense

| | Discovery | Defense |
|---|---|---|
| Inputs | a handful, 1–10, chosen to probe | a suite, from ~50 up to thousands |
| Who judges | a person reading transcripts | metrics with thresholds |
| Goal | raise the ceiling: find what the agent cannot yet do | protect the floor: make sure it never does worse |
| A failure means | an idea for the next change | the change does not merge |
| In ADK | `adk web` (Trace and Eval tabs), `adk run --jsonl`, reading sessions | `adk eval` through a wrapper that exits non-zero, `AgentEvaluator` in pytest |
| In this plugin | `/adk:agent-debug`, `/adk:eval-create` capturing a golden trace | `/adk:eval-run`'s `run_evals.py`, `/adk:eval-ci`'s tiers |

Both are necessary and neither substitutes for the other. A team that only does
discovery is at maturity **L0** however carefully it reads transcripts: nothing
it learned is replayable. A team that only does defense stops finding new
failure modes and its suite converges on what it already knew. The bridge is
mechanical: **every discovery finding becomes a case**, and from then on defense
owns it.

A defense gate is a number, e.g. the article's example of failing the build when
grounding drops below 0.85. In ADK terms that is `hallucinations_v1` with
`"threshold": 0.85` in the nightly config, gated by `run_evals.py`, which exits 1
when a case misses it — and never bare `adk eval`, which exits 0 either way
through 2.12.0 and, from 2.13.0, exits 1 on a failure but has no pass-rate,
regression or budget gate.

## Three levels of metric

Ordered by cost and by how much judgement they encode:

| Level | What it checks | ADK metrics | Cost tier, CI tier |
|---|---|---|---|
| **Computation** | deterministic properties of the output or the trajectory: exact or fuzzy text overlap, a regex, valid JSON, a forbidden phrase, which tools ran in which order | `tool_trajectory_avg_score` (`EXACT`, `IN_ORDER`, `ANY_ORDER`), `response_match_score` (ROUGE-1), any custom metric — including the plugin's trajectory precision / recall kinds | free, per change |
| **Rubric (LLM judge)** | a yes/no property a judge reads off the transcript | `rubric_based_final_response_quality_v1`, `rubric_based_tool_use_quality_v1`, `rubric_based_multi_turn_trajectory_quality_v1`, `final_response_match_v2`, `hallucinations_v1` | judge, nightly |
| **Managed autorater** | a property scored by a hosted service with its own model and spec | `safety_v1`, `response_evaluation_score`, `multi_turn_task_success_v1`, `multi_turn_trajectory_quality_v1`, `multi_turn_tool_use_quality_v1` | vertex, nightly |

Two distinctions inside the rubric level matter when choosing:

- **Static versus adaptive rubrics.** A static rubric is written once and applied
  to every case — in ADK, rubrics on the criterion. An adaptive rubric is written
  for one prompt — in ADK, rubrics on the eval case, filtered by `type`. Static
  rubrics state the agent's invariants; per-case rubrics state what *this*
  request should produce. `/adk:eval-criteria`'s `rubric-authoring.md`.
- **Reference-free versus reference-based.** `final_response_match_v2` compares
  against an expected answer; the `rubric_based_*` metrics and
  `hallucinations_v1` need none. Reference-free metrics are the only ones that
  can score a simulated conversation.

The managed level differs by more than price: `judge_model_options` does nothing
there, the service picks its model, and a missing credential produces
`NOT_EVALUATED` rather than a low score. Managed tool-use scoring
(`multi_turn_tool_use_quality_v1`) needs no expected trajectory, which is what
makes it usable on simulated suites where `tool_trajectory_avg_score` is not.

Since 2.10 ADK adds a fourth column the taxonomy has no slot for: the four
**efficiency** metrics (`tool_call_count_v1`, `inference_call_count_v1`,
`token_usage_v1`, `invocation_duration_v1`). They are computation-level, always
on, and never pass or fail; they are what makes a cost claim checkable.

Climb the levels only as far as the question needs. A trajectory check that
answers it deterministically beats a judge that answers it probabilistically.

## The dataset is source code

Treat the evalset the way you treat the agent: in version control, changed in
reviewed commits, with stable ids. The article's field list maps onto ADK's
evalset schema directly:

| Generic field | ADK evalset field |
|---|---|
| prompt | `conversation[].user_content` |
| reference (answer) | `conversation[].final_response` |
| reference trajectory | `conversation[].intermediate_data.tool_uses` |
| session inputs | `session_input` (`app_name`, `user_id`, `state`, optional `session_id`) |
| response, after inference | `actual_invocation.final_response` in the result file |
| intermediate events, after inference | `actual_invocation.intermediate_data` in the result file |

The rules that follow from "it is code": never edit a golden case to make a
failing run pass without saying so; add a sibling case instead. Rename and
deduplicate through tooling that keeps ids stable. Review a new case for what it
asserts, not only for whether it parses. `/adk:eval-create`'s
`managing-evalsets.md` and `/adk:eval-review` cover the mechanics.

Inference over a large suite is parallel work. ADK exposes
`InferenceConfig.parallelism` / `EvaluateConfig.parallelism` (a semaphore) on the
programmatic path only; `adk eval` has no flag for it. Running that inference
against a not-yet-released revision of a deployed agent is the shadow pattern in
`/adk:eval-ci`'s `shadow-rollout.md`.

## Two traces

A **reasoning trace** is what the agent decided: the events — model turns,
function calls with arguments, function responses. It answers *was this the
right thing to do?*, and it is what every trajectory metric reads. A **system
trace** is what the infrastructure did: OpenTelemetry spans with timings, token
counts, finish reasons and the exact request the model received. It answers
*why was it slow, expensive, or truncated?* Where each lives in ADK, and which to
open for which question: `/adk:agent-debug`'s `evidence.md`.

## The eval-driven loop

The Firebase team's process for building agent skills, generalized:

1. **Write the case first.** The behaviour you want, as an eval case that the
   current agent should fail.
2. **Baseline.** Run the suite without the change and keep the result. A case
   that already passes was not testing the change.
3. **Make the change** — an instruction rule, a tool, a docstring.
4. **Compare.** `/adk:eval-analyze`'s `compare_runs.py <baseline> <candidate>`:
   pass rate, verdict flips, tokens, duration.
5. **Hill-climb.** Keep the change if it helped and broke nothing; otherwise
   revise and compare again. Every kept improvement leaves its case behind as a
   guard.

Google's "moving past the vibe check" post adds the discipline around the loop:
change one variable per iteration (production quality took them 50 to 60 small
ones), freeze the golden cases while tuning, and merge only on a global pass
threshold plus zero regressions. Those rules and the flags that enforce them:
`/adk:eval-ci` `merge-gates.md`. Google's quality-flywheel post adds who may
grade: whatever proposed the change never scores it, judge scores are read as
deltas between runs, and an adaptive metric is paired with one stable custom
metric for the behaviour under change — `/adk:eval-analyze`
`improvement-loop.md`, which also maps the hosted `agents-cli eval` family and
the platform's online monitors onto this plugin. An agent that itself uses ADK skills has its own
version of the three scopes below: `/adk:agent-create`
`skills-and-progressive-disclosure.md`.

They test at three scopes, which map onto an ADK agent:

| Firebase scope | For an ADK agent |
|---|---|
| a single skill, 5–10 cases | one tool or one instruction branch: happy, edge, refusal |
| activation: is the right skill picked? | routing: the right tool, or the right sub-agent transfer, for the request |
| end to end | multi-turn cases and user simulation (`/adk:eval-simulation`) |

Their reported result of adding skills built this way, across their suite:

| | Without | With |
|---|---|---|
| pass rate | 31.7% | 78.0% |
| input tokens | 285.1k | 169.5k |
| output tokens | 9.5k | 5.8k |
| duration | 144.3 s | 101.2 s |

The point for an ADK project is the shape of that table, not its numbers: a
quality column and a cost column, before and after, from the same suite. That
is what `compare_runs.py` prints, and on 2.10+ the efficiency metrics fill the
cost half without extra configuration.

---

Sources: `adk-python: evaluation/eval_metrics.py`, `evaluation/eval_case.py`,
`evaluation/eval_set.py`, `evaluation/eval_config.py`,
`evaluation/local_eval_service.py` (`_parallelism_semaphore`),
`evaluation/_efficiency_evaluators.py`; `adk-docs: docs/evaluate/index.md`,
`docs/evaluate/criteria.md`. The two modes, the three-level taxonomy, the
dataset-as-code fields, the 0.85 grounding example and the two-trace distinction
are paraphrased from Vlad Kolesnikov, "From 'vibe checks' to continuous
evaluation: engineering reliable AI agents", Google Cloud blog, 2026-02-28,
https://cloud.google.com/blog/topics/developers-practitioners/from-vibe-checks-to-continuous-evaluation-engineering-reliable-ai-agents.
The eval-driven loop, the three scopes and the before/after figures are from
Charlotte Liang, "Eval-driven development: how we build better agent skills for
Firebase", Firebase blog, 2026-08-11,
https://firebase.blog/posts/2026/08/eval-driven-development-agent-skills. The
one-variable rule and the merge rules are from Selbie, Zamora, Carah and
DeMartino, "Evaluating AI agents: moving past the vibe check", Google Developers
Blog, 2026-10-15,
https://developers.googleblog.com/evaluating-ai-agents-moving-past-the-vibe-check/.
The proposer/grader rule is from Melnyk and Dai, "Driving the Agent Quality
Flywheel from Your Coding Agent", Google Developers Blog, 2026-06-30,
https://developers.googleblog.com/driving-the-agent-quality-flywheel-from-your-coding-agent/.
The maturity framing (L0) follows the LinkedIn Learning course *Evaluating AI Agents
with Google ADK*, recommended as background reading.
