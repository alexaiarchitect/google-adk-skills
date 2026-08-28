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
| `EVS006` | warn | `eval_cases` is empty: the file asserts nothing |
| `EVS040` | warn | unrecognized top-level key. `EvalSet` ignores extras, so a typo is silent |
| `EVS041` | warn | the file mixes camelCase and snake_case keys |
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
| `EVS020`/`EVS021` | error/warn | invocation is not an object; unrecognized invocation key |
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
| `EVS080`-`EVS082` | error | rubric is not an object, has no `rubric_id`, or no `rubric_content.text_property` |
| `EVS083` | warn | per-case rubric with no `type`, so no criterion reads it |
| `EVS084` | error | rubric `type` outside `FINAL_RESPONSE_QUALITY`, `TOOL_USE_QUALITY`, `TRAJECTORY_QUALITY` |
| `EVS090`-`EVS092` | error | `conversation_scenario` malformed, or missing `starting_prompt` / `conversation_plan` |

Criterion-level rubrics are used unfiltered; per-case rubrics are filtered by
`type` and merged on top. The effective list must be non-empty or evaluation
raises.

## Coverage (`EVS07x`, `GAP-*`)

| Id | Severity | Trigger |
|---|---|---|
| `EVS070` | warn | the instruction yields no testable rules |
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

Sources: `adk-python: evaluation/eval_case.py`, `evaluation/eval_set.py`,
`evaluation/common.py`, `evaluation/local_eval_sets_manager.py`,
`evaluation/evaluation_generator.py`, `evaluation/trajectory_evaluator.py`,
`runners.py`, `tools/function_tool.py`, `flows/llm_flows/functions.py`;
`adk-docs: docs/evaluate/criteria.md`, `docs/evaluate/index.md`.
