# The improvement loop: from a failing suite to a better agent

A suite that fails tells you the agent is wrong somewhere. Getting from there to a
better agent is its own loop, and it goes wrong in predictable ways: the fix is
graded by whoever wrote it, one noisy score is read as progress, or a metric that
rewrites itself between runs is used to compare two runs. This page sets out the
loop with this plugin's scripts, and then what Google's hosted evaluation
platform adds once the agent is live.

The five-stage shape and the separation rule come from Google's quality-flywheel
post; the after-launch capabilities from the Agent Platform evaluation GA post
(sources below). The mapping onto ADK and onto these scripts is ours.

## Five stages, five scripts

| Stage | What happens | Here |
|---|---|---|
| 1. Prepare data | cases from captured sessions, written by hand, or synthesised by a user simulator | `/adk:eval-create` `gen_cases.py`, `session_to_evalset.py`; `/adk:eval-simulation` `gen_scenarios.py` |
| 2. Run inference | the agent runs every case and leaves a trajectory | `/adk:eval-run` `run_evals.py` (skip it when grading traces you already have) |
| 3. Grade | metrics score each trajectory | the config's `criteria` and `custom_metrics`, written by `/adk:eval-criteria` |
| 4. Analyse failures | read verdicts and rationales; group failures | `summarize_results.py` (quadrant, first divergence, rubric ids); `calibration_sample.py` for the passes |
| 5. Change one thing, compare | a targeted fix, then stages 2 to 4 again against the previous baseline | `compare_runs.py <baseline> <candidate> --min-pass-rate P --max-regressions 0` |

Stage 3 is the only one that always runs: when production traces already exist,
there is nothing to infer, only something to grade. Stage 4 is where most of the
time goes. With a handful of failures, read each one; past roughly ten, group
them first — by quadrant and divergence point from `summarize_results.py`, then
by the shapes in `/adk:agent-debug` `failure-shapes.md` — so that one fix
targets a cluster rather than one case.

Expect several passes before a metric moves. A case that is still red after one
change has not proved the change useless; a case that turned green while
another turned red has proved nothing yet.

## The proposer never grades its own fix

Whatever proposes a change — a person, an automated optimiser, a coding agent
such as the one running this plugin — must not also be the thing that says the
change worked. An optimiser that scores itself learns to satisfy the score.

In this plugin that is a protocol rule, not advice:

- **The assistant proposes.** An instruction sentence, a tool description, a
  threshold — offered as a numbered option with its cost, never applied silently.
- **The scripts and the judge grade.** `run_evals.py` and the config's metrics
  produce the verdict; `compare_runs.py` turns two verdicts into a delta. The
  assistant reports what they printed and does not restate a result it would
  prefer.
- **The user approves.** Nothing is kept until the comparison has been shown and
  the user has said yes.
- **The cases are not the proposer's to edit.** Changing a golden case so the
  candidate passes is grading by deletion; `manage_cases.py` refuses it on a
  frozen suite (`EVS120`), and `/adk:eval-ci` `merge-gates.md` explains why.

## Trust the delta, not the number

A model-based score is a judgement, not ground truth. The post's phrase is
"treat the scores as a strong directional signal": a 0.64 on its own means
little, but the same suite scoring 0.64 before and 0.81 after, with no case
flipped to failed, means a lot. So:

- compare a candidate with a baseline from the same suite, config and judge,
  never with last month's number;
- gate the merge on the comparison (`compare_runs.py --max-regressions 0`), and
  on a floor (`--min-pass-rate`) only as a backstop;
- when a judge metric is involved, compare batches (`run_evals.py --runs 5`,
  `pass_at_k.py`) rather than single runs, or one noisy run decides.

## Adaptive metrics need a stable partner

ADK's adaptive judges — the managed `multi_turn_task_success_v1` and
`multi_turn_trajectory_quality_v1`, and per-case rubrics generated for each
prompt — write their criteria afresh for every case and run. That makes them good
broad health signals and poor before/after instruments: the rubric that failed
the baseline may not be the rubric that scores the candidate.

Pair them with **one stable custom metric for the behaviour under change**,
whose rule is fixed for the length of the experiment:

- a code metric where the property is checkable — scaffold it with
  `/adk:eval-criteria` `gen_custom_metric.py --name ... --kind sync`;
- or a single fixed rubric sentence on `rubric_based_final_response_quality_v1`,
  scored yes or no.

Give it named outcomes you can count (for a revision request: honoured, ignored,
partly honoured, no revision asked), and decide the action threshold before the
first run — the post acted when more than a fifth of cases came back "ignored".
A task-success score of 0.80 can hide the one criterion out of five you were
trying to fix; a count of "ignored" cannot.

## Make sure the judge can see the tools

A judge that does not know which tools the agent has will mark legitimate calls
as unexpected. ADK's rubric and groundedness judges read the tool declarations
from the invocation's `app_details`, which `adk eval` records during inference;
an invocation without them is presented to the judge as "Agent has no tools."
That happens when grading traces recorded elsewhere, or a custom LLM-judge
metric that builds its own prompt. Check the judge's input before trusting a
trajectory-quality drop, and when the declarations cannot be supplied, compare
runs on task success and the stable custom metric instead of trajectory quality.

## Two worked examples, in the expense-claim domain

**Stale values in the final reply.** A claimant files CLM-000412 for 900 dollars,
then says "sorry, make that 650". The agent calls
`submit_claim(claim_id="CLM-000412", amount_usd=650)` — the state is right — and
then writes "Your claim for 900 dollars has been submitted." Trajectory metrics
pass; a task-success judge scores it high because four of five criteria hold.
The stable metric is a code check: the amount in the final reply equals the
`amount_usd` of the last `submit_claim` call in `intermediate_data`. Twenty
revision cases (amount, cost centre, category, date, a withdrawn claim) give a
countable "ignored" rate. The fix is one instruction rule — reconcile the final
reply with the latest value the user gave — and `compare_runs.py` shows whether
the ignored rate fell without flipping any other case.

**A disclosure footer nobody wrote.** The instruction says every reply ends with
the tools used ("Tools used: lookup_policy, submit_claim"). A broad run with no
hypothesis shows the work is right in nearly every case and the footer is in
almost none — a failure no trajectory metric can see. The stable metric is a
regex over the final reply; the fix is a short, explicit paragraph naming the
footer's exact form. A rule that was 0% compliant can reach near-100% in one
cycle; a guard case keeps it there.

## After launch: the same metrics, on live traffic

Google's Agent Platform evaluation service (generally available) uses one engine
and one set of metrics in development and in production. What it adds beyond a
local suite:

| Capability | What it does | The local counterpart |
|---|---|---|
| Experiments | a dataset plus metrics plus scores, run locally or server-side with artefacts kept in Cloud Storage | an evalset, a config and `.adk/eval_history/`; `--eval_storage_uri gs://` |
| Online monitors | grade a sample of live traffic or Cloud Trace traces continuously, with dashboards and drift alerts | none in this plugin; the shadow gate in `/adk:eval-ci` `shadow-rollout.md` scores a revision before it takes traffic |
| Issue clustering | groups failures against a failure taxonomy you supply | `summarize_results.py` quadrants plus `failure-shapes.md`, by hand |
| Case generator, user and environment simulators | synthesise cases from instructions and tools, play personas, mock tool backends | `/adk:eval-simulation` |
| Metric registry | built-in, adaptive-rubric and custom (code or judge) metrics, versioned per organisation | `custom_metrics` in a config under version control |

Using the same metric names in development and production is what closes the
loop: a monitored drop in task success arrives as traces, the failing ones
become `regression_*` cases (`session_to_evalset.py`), and the next change is
graded on them locally. That is maturity **L4** in `/adk:eval-ci` `maturity.md`.

## The hosted path, and this plugin's equivalent

Google's Agents CLI drives the same loop against the platform:

| `agents-cli eval` | Local equivalent |
|---|---|
| `eval dataset synthesize` | `gen_scenarios.py`, `generate_eval_cases.py` (user simulator) |
| `eval generate` | `run_evals.py` (inference) |
| `eval grade --traces F --config F` | the config's metrics, via `run_evals.py` |
| `eval compare` | `compare_runs.py` |
| `eval analyze` | `summarize_results.py` |

The hosted path needs a Google Cloud project with the evaluation service
enabled, and its model-based metrics are billed at model rates. The local path
needs only what `/adk:adk` `check_env.py` reports. Neither is a substitute for
real traffic: simulated cases are a cold start, and the suite should grow from
what production actually gets wrong.

---

Sources: Dima Melnyk and Jason Dai, "Driving the Agent Quality Flywheel from Your
Coding Agent", Google Developers Blog, 2026-06-30,
https://developers.googleblog.com/driving-the-agent-quality-flywheel-from-your-coding-agent/
(the five stages, the separation of proposer and grader, deltas over absolutes,
adaptive metrics paired with a stable custom one, the tool-schema caveat, and the
two case studies, retold here in this plugin's domain; paraphrased). Alex Martin
and Dima Melnyk, "Agent and Model Evaluations in Gemini Enterprise Agent Platform
are now GA", Google Developers Blog, 2026-07-31,
https://developers.googleblog.com/agent-and-model-evaluations-in-gemini-enterprise-agent-platform-are-now-ga/
(experiments, online monitors, issue clustering, simulators, metric registry).
Ippolito and Secchi, "Powering the Agent Development Lifecycle with any
framework, from ADK to LangChain and beyond", Google Developers Blog, 2026-10-21,
https://developers.googleblog.com/powering-the-agent-development-lifecycle-with-any-framework-from-adk-to-langchain-and-beyond/
(the `eval` subcommands). `adk-python: evaluation/llm_as_judge_utils.py`
(`get_tool_declarations_as_json_str`), `evaluation/rubric_based_tool_use_quality_v1.py`,
`evaluation/rubric_based_final_response_quality_v1.py` ("Agent has no tools."),
`evaluation/app_details.py`, `evaluation/evaluation_generator.py` (recording
`app_details`), at `12c7bed76`. This plugin's `summarize_results.py`,
`compare_runs.py`, `calibration_sample.py`, `gen_custom_metric.py`,
`manage_cases.py`.
