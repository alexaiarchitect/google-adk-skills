# Validation rules

Every check the eval-review scripts perform, with what triggers it and where the
rule comes from. Ids are stable.

## Evalset structure (`EVS0xx`)

| Id | Severity | Trigger |
|---|---|---|
| `EVS001` | error | file is unreadable or not valid JSON |
| `EVS002` | error | no `eval_set_id` |
| `EVS003` | error | `eval_set_id` outside `^[a-zA-Z0-9_]+$` (ADK enforces this when it writes an eval set, so a dash or a dot breaks the round trip) |
| `EVS004`/`EVS005` | error | `eval_cases` missing, or not an array |
| `EVS076` | error (2.13+) / warn | `eval_set_id` or `session_input.app_name` is drive-qualified — a letter then a colon at the start (`c:pass`). 2.13.0 rejects it with `ValueError: ... must not be drive-qualified.` when it saves the results, after inference has been paid for; earlier versions accept it, so it is a warning there and when the version is unknown. A colon later in the id is allowed |
| `EVS006` | warn | `eval_cases` is empty: the file asserts nothing — and on ADK 2.10+ the pytest path raises `ValueError` for it, where 2.9 ran and reported nothing |
| `EVS040` | warn | unrecognized top-level key. `EvalSet` ignores extras, so a typo is silent |
| `EVS041` | warn | the file mixes camelCase and snake_case keys |
| `EVS047` | error | key on `intermediate_data` that fits neither of its two shapes (`IntermediateData` with `tool_uses`, or `InvocationEvents` with `invocation_events`, which is what a captured run writes), or the two shapes mixed in one object; both forbid extras, so the file fails to load |
| `EVS049` | info | the case carries a 2.10+ invocation field (`duration`): 2.10 and later read it, ADK 2.9.2 refuses the whole file with `extra_forbidden` |
| `EVS050` | warn | legacy list format; ADK converts it on read and assigns a random `eval_set_id` |

## Cases (`EVS01x`, `EVS03x`, `EVS06x`)

| Id | Severity | Trigger |
|---|---|---|
| `EVS011` | error | case has no `eval_id` |
| `EVS012` | error | duplicate `eval_id` |
| `EVS013` | warn | unrecognized key on the case (`EvalCase` allows extras, so typos never raise) |
| `EVS014` | error | both `conversation` and `conversation_scenario`; the model validator allows exactly one |
| `EVS015` | error | neither |
| `EVS016` | error | `conversation` is empty |
| `EVS030` | warn | no `session_input`: the case runs as app `EvaluationGenerator`, user `test_user_id` |
| `EVS032` | error | `session_input.app_name` missing |
| `EVS033` | warn | `app_name` differs from the agent's directory (see below) |
| `EVS034` | warn | no `user_id`, so user-scoped state has no stable owner |
| `EVS060` | warn | `eval_id` like `test_1` that says nothing about the behaviour |
| `EVS061` | info | `eval_id` with no category prefix, so coverage cannot bucket it |
| `EVS062` | warn | refusal or trap cases scored with `IN_ORDER`/`ANY_ORDER`, which let the forbidden call through; use `EXACT` or a restraint rubric |

### Why `EVS033` is a warning and not an error

The eval session is created from `session_input.app_name`, and the runner then
takes **its** `app_name` from that session, so the two always agree and any value
runs. What a mismatch costs: `app:`-prefixed state, memory and artifacts are
scoped under that name rather than the one production uses, ADK appends a
mismatch hint to error messages, and the results file is named after the
directory instead. Recommended value: the agent directory name.

## Invocations and tool calls (`EVS02x`, `EVS03x`, `EVS04x`)

| Id | Severity | Trigger |
|---|---|---|
| `EVS020` | error | invocation is not an object |
| `EVS021` | error | unrecognized key on the invocation. `Invocation` is `extra="forbid"` — still true on 2.12 — so the file fails to load. Its legal keys on 2.10 through 2.12 are `invocation_id`, `user_content`, `final_response`, `intermediate_data`, `creation_timestamp`, `duration`, `rubrics`, `app_details`; `InvocationEvent`, by contrast, gained `extra="ignore"` in 2.10, so an unknown key on an **event** is dropped rather than rejected |
| `EVS022`/`EVS023` | error | no `user_content`; `user_content` with no text and no other part |
| `EVS024` | warn | `user_content.role` is not `user` |
| `EVS025` | error | no `final_response` text while a **response**-reference metric is active |
| `EVS026` | info | no `final_response` text, and none is needed: a trajectory-only case |
| `EVS027`-`EVS029` | error | `intermediate_data` or `tool_uses` malformed |
| `EVS035` | error | tool use with no `name` |
| `EVS036` | error | expected call to a tool the agent does not define |
| `EVS037` | error | `args` is not an object |
| `EVS038` | info | tool is bound but defined outside the project, so arguments were not checked |
| `EVS039` | error | tool takes a Pydantic model, but the arguments are not nested under its parameter |
| `EVS042` | warn | a required parameter is missing |
| `EVS043` | warn | an argument the tool has no parameter for |
| `EVS044` | error | value outside a `Literal` |
| `EVS045` | error | value fails a `pattern` |
| `EVS046` | error | value breaks a `ge`/`le`/`gt`/`lt` bound |

`EVS025` fires only for metrics whose reference is the **response text**
(`response_match_score`, `final_response_match_v2`, `response_evaluation_score`).
`tool_trajectory_avg_score` is reference-based too, but its reference is the
expected tool calls, so it needs no written answer. A response metric with
threshold 0 is disabled and does not trigger it either.

## Scenarios and rubrics (`EVS08x`, `EVS09x`)

| Id | Severity | Trigger |
|---|---|---|
| `EVS080`-`EVS081` | error | rubric is not an object, or has no `rubric_id` |
| `EVS082` | error / warn | no `rubric_content` object (error); `rubric_content` without `text_property` is an **error** only when the runner version is known and below 2.12 (2.9–2.11 reject it), otherwise a **warn**: 2.12 loads it, but a rubric with no sentence asks the judge nothing. `--adk-version V` overrides the detected runner. `CFG044` is the same rule for rubrics in an eval config |
| `EVS083` | warn | per-case rubric with no `type`, so no criterion reads it |
| `EVS084` | error | rubric `type` outside `FINAL_RESPONSE_QUALITY`, `TOOL_USE_QUALITY`, `TRAJECTORY_QUALITY` |
| `EVS090`-`EVS092` | error | `conversation_scenario` malformed, or missing `starting_prompt` / `conversation_plan` |

Criterion-level rubrics are used unfiltered; per-case rubrics are filtered by
`type` and merged on top. The effective list must be non-empty or evaluation
raises.

## Findings from the neighbouring scripts

Ids are stable across the plugin, so these are worth knowing where you read an
eval-review report next to a config or conformance one:

| Id | Severity | Trigger |
|---|---|---|
| `CFG070` | error | `criteria` names one of the four ADK 2.10 efficiency metrics. They take no threshold: 2.10+ raises `ValueError` at run time, 2.9 raises `NotFoundError`, and no config-level validation in either version catches it. Remove the entry — they are reported automatically |
| `CFG071` | info | ADK 2.10+ also reports those four informational metrics on every run, so a result file carries entries your config never asked for. Reported when the resolved runner is 2.10 or newer, or unknown |
| `CFG072` | info | the config has `multi_run_config`. Only `adk-python` main after 2.13.0 reads it; every released version drops it silently and runs each case once, so batch with `run_evals.py --runs` and `pass_at_k.py` |
| `CFG073` | error | `multi_run_config` has the wrong shape for main's `MultiRunConfig`: `num_runs` < 1, `max_crashed_runs` < 0, an unknown key, a method that is neither built in (`mean_over_invocations`, `mean_of_runs`, `min_over_runs`, `pass_rate`) nor in `custom_methods`, a `pass_rate` rule without a `threshold` in 0..1, or a `cross_run_scoring_by_metric` key not in `criteria` |
| `SIM032` | warn | `live_model_config.timeout_seconds` ≤ 0. ADK enforces no bound, so the live run times out at once or never; the default is 300 |
| `SIM033` | warn | `live_model_config` is set but the agent's model id is not a Live model (`*-live-*`, `*native-audio*`), so the live path is pointed at a model that cannot serve it |
| `SIM034` | warn | an unknown key in `user_simulator_config`. Both simulator types are `extra="allow"`, so a typo such as `max_allowed_invocation` is accepted and ignored |
| `CI001` | error / warn | (`lint_workflows.py`) a workflow runs bare `adk eval`. **Error** when the ADK pin is below 2.13 or unknown: it exits 0 whatever happened. **Warn** from 2.13: it exits 1 on a failure, but cannot gate on a pass rate, regressions or budgets; prefer `run_evals.py`. The pin is `--adk-version`, else the lowest `google-adk` specifier in `pyproject.toml` / `requirements*.txt`, else the workflow's `--with "google-adk[eval]==X.*"` |
| `AGT080` | warn | (`lint_agent.py`) an instruction placeholder `{key}` that no `output_key`, `tool_context.state[...]`, `callback_context.state[...]` or `state_delta` writes and no evalset seeds. Building the instruction raises `KeyError` ("Context variable not found"); seed it, write it, or use `{key?}` for an optional value |
| `AGT015` | warn | (`lint_agent.py`) two agents in the tree share a `name` — siblings, cousins or an agent and its descendant. ADK only logs a warning (siblings before 2.13, the whole tree from 2.13), and `find_agent()` returns the first match in a depth-first walk |
| `CFG057` | warn | an unrecognized key on a `custom_metrics` entry (e.g. `metric`), which ADK ignores; the range belongs in `metric_info.metric_value_info.interval` |
| `CFG058` | error | `metric_info` on a custom metric lacks `metric_name` or `metric_value_info`, or its interval lacks numeric `min_value` / `max_value`, so the config fails to load |
| `CNF015` | error (exit 2) | a conformance case has `input.yaml` or `test.yaml` and no `spec.yaml`. `adk conformance record --help` names those files on 2.9.2–2.11.0 (2.12.0 fixed the help), but the code reads only `spec.yaml` |
| `CNF020` | error | a conformance case directory has no `generated-recordings.yaml` (or `-sse` variant) for replay mode. ADK skips such a case with a yellow warning and the run can still exit 0 |
| `CNF021` | error (exit 2) | streaming mode `bidi`: the CLI accepts it, and ADK's recorder and replayer raise "Unsupported streaming mode" |
| `EVS120` | error (exit 2) | `manage_cases.py remove`, `rename`, `move` (either end), `split`, `rebase-app-name` or `dedupe --apply` would touch a case in a suite marked `"frozen": true` in `eval_registry.json` (the nearest one above the evalset, or `--registry F`). `--unfreeze` overrides it with a note; make that its own reviewed change. Adding a case to a frozen suite is allowed |
| `ENV014` | info | (discovery) the target is an Agent-Plugins skill layer (`plugin.json`, `skills/*/SKILL.md`, `EVAL.yaml`), not an ADK agent; it replaces `ENV011` there and points at `/adk:eval-review` |

## `EVAL.yaml` (`EVY0xx`)

`validate_eval_yaml.py` checks the recipes' plugin-skill format
(`/adk:eval-create` `managing-evalsets.md`). It never runs a case.

| Id | Severity | Trigger |
|---|---|---|
| `EVY001` | error | the YAML cannot be parsed |
| `EVY002` | error | no `cases` list, or an empty one |
| `EVY003` | error | a case without an `id`, or a case that is not a mapping |
| `EVY004` | error | a case without a `prompt` |
| `EVY005` | error | a duplicate case `id` |
| `EVY006` | error / warn | an empty `rubric`: an error when the case has no assertions either, a warning when assertions still check it |
| `EVY007` | warn | a rubric line without MUST or SHOULD, so it does not say what is required |
| `EVY008` | error | an unknown assertion type, or `assertions` that is not a list |
| `EVY009` | error | an assertion missing the field its type needs (`tool_called` without `tool`, `price_under` without `value`, ...) |
| `EVY010` | info | `tool_called` names a tool the sibling `SKILL.md` never mentions |

## Coverage (`EVS07x`, `GAP-*`)

| Id | Severity | Trigger |
|---|---|---|
| `EVS070` | warn | the instruction yields no testable rules |
| `EVS073` | warn | (`coverage_matrix.py`, discovery) skills the agent registers that no case loads with `load_skill`. Reported only when the agent registers skills and at least one case exists |
| `EVS075` | info | (`validate_evalset.py --agent-dir`) a case seeds a `session_input.state` key that no instruction placeholder, tool or callback in the agent reads: the seed probably has a typo, or the agent stopped using the key. Prefixes `app:` / `user:` / `temp:` are compared without the prefix |
| `EVS074` | warn | (`validate_evalset.py`) a case expects `load_skill` (or a prefixed `*_load_skill`) with a `skill_name` the agent does not register. Silent when the registered set is unknowable (a registry, GCS, a list built at runtime) or there is no `SkillToolset` |
| `GAP-BRANCH` | gap | fewer cases than branches + 1 |
| `GAP-RULE-<n>` | gap | a specific branch with no matching case |
| `GAP-SHAPE` | gap | no adversarial case, no multi-turn case, or too few narrow ones |
| `GAP-TOOLS` | gap | a tool no case ever exercises |

## Argument comparison semantics

Trajectory matching compares arguments as dictionaries, so:

- `2499` and `2499.0` are equal (both Python numbers);
- `2499` and `"2499"` are not;
- key order does not matter, nesting does.

---

Sources: `adk-python: evaluation/eval_case.py` (the per-model `extra` settings and
`Invocation.duration`, read on the 2.10.0 wheel), `evaluation/agent_evaluator.py`
(the empty-eval-set `ValueError`, 2.10+; absent from 2.9), `evaluation/eval_set.py`,
`evaluation/common.py`, `evaluation/local_eval_sets_manager.py`,
`evaluation/evaluation_generator.py`, `evaluation/trajectory_evaluator.py`,
`runners.py`, `tools/function_tool.py`, `flows/llm_flows/functions.py`,
`evaluation/_path_validation.py` (the drive-qualified rule, `29bb053`, in
`v2.13.0`, measured through `adk eval` on 2.12.0 and 2.13.0),
`evaluation/simulation/_llm_audio_user_simulator.py` and `LiveModelConfig`
(measured on 2.12.0 and 2.13.0), `MultiRunConfig` on main `12c7bed76`,
`utils/instructions_utils.py` (`inject_session_state`),
`agents/base_agent.py` (`validate_sub_agents_unique_names`, `6031bb1`);
`adk-docs: docs/evaluate/criteria.md`, `docs/evaluate/index.md`.
