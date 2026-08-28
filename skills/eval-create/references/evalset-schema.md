# The eval case, field by field

## A minimal single-turn case

```json
{
  "eval_set_id": "edge",
  "name": "Refusals",
  "description": "Every branch where the agent must stop.",
  "eval_cases": [
    {
      "eval_id": "edge_frozen_cost_centre_no_submit",
      "conversation": [
        {
          "invocation_id": "inv_1",
          "user_content": {
            "parts": [{"text": "Claim CLM-000501 for 60 dollars of meals on marketing."}],
            "role": "user"
          },
          "final_response": {"parts": [{"text": ""}], "role": "model"},
          "intermediate_data": {
            "intermediate_responses": [],
            "tool_uses": [
              {"name": "lookup_policy", "args": {"req": {"category": "meals"}}},
              {"name": "check_cost_centre_funds",
               "args": {"req": {"cost_centre": "marketing", "amount_usd": 60.0}}},
              {"name": "reject_claim",
               "args": {"req": {"claim_id": "CLM-000501", "reason": "the cost centre is frozen"}}}
            ]
          }
        }
      ],
      "session_input": {"app_name": "app", "user_id": "evaluator", "state": {}}
    }
  ]
}
```

The assertion that matters here is what is **absent**: `submit_claim` is never
called. Exact trajectory matching turns that absence into a failure if the agent
submits anyway. There is no `forbidden_tools` key, and none is needed.

## Fields

### Eval set

| Field | Required | Notes |
|---|---|---|
| `eval_set_id` | yes | `^[a-zA-Z0-9_]+$` when ADK writes the file; use the filename stem |
| `name`, `description` | no | for humans; the description is where "what this file is for" belongs |
| `eval_cases` | yes | may be empty while you capture traces |

### Eval case

| Field | Required | Notes |
|---|---|---|
| `eval_id` | yes | unique; `<category>_<condition>_<expected>` |
| `conversation` | one of | a list of invocations |
| `conversation_scenario` | one of | simulated user instead of fixed turns; exactly one of the two |
| `session_input` | recommended | `app_name`, `user_id`, `state`, optional `session_id` |
| `rubrics` | no | filtered by `type`, merged into matching criteria |
| `final_session_state` | no | declared, and **evaluated by nothing** — see below |

### `final_session_state` is not checked

`EvalCase.final_session_state` parses, serializes and round-trips, and no
evaluator in ADK 2.9 ever reads it. A case that "asserts" state through it
asserts nothing, and passes whatever the agent did.

**Do not rely on it.** Assert state the way the run can actually observe it:

- through a **tool result** — the case expects a later call whose arguments could
  only have come from the stored value;
- through a **rubric** on `rubric_based_tool_use_quality_v1` or
  `rubric_based_final_response_quality_v1`, which reads the transcript.

`session_input.state` is the other half and *is* honoured: it pre-loads state, so
a case can start from a condition instead of talking the agent into one.

### Unknown keys, per model

There is no single rule — it depends which object the key is on:

| Model | An unknown key is |
|---|---|
| `EvalSet` | **ignored** (dropped on parse) |
| `EvalCase`, `SessionInput` | **kept** (`extra="allow"`), and read by nothing |
| `Invocation`, `IntermediateData`, `ConversationScenario`, `Rubric` | **rejected** (`extra="forbid"`) |

So a typo inside `intermediate_data` stops the file loading with a clear error,
while the same typo one level up on the eval case is silent. The validator in
`/adk:eval-review` reports both.

### Invocation

| Field | Notes |
|---|---|
| `invocation_id` | free text; useful in failure output |
| `user_content` | `{"parts": [{"text": "..."}], "role": "user"}` |
| `final_response` | the expected answer; leave the text empty for a trajectory-only case |
| `intermediate_data.tool_uses` | the expected calls, in order, with arguments |
| `intermediate_data.tool_responses` | optional; what the tools returned |
| `intermediate_data.intermediate_responses` | `[author, parts]` pairs for intermediate messages |

### Session input

`app_name` names the session the case runs in. Set it to the agent directory
name: the run works either way, but `app:` state, memory and artifacts are scoped
by it, and ADK warns when it disagrees with where the agent was loaded from. With
no `session_input` at all, the case runs as app `EvaluationGenerator`, user
`test_user_id`.

`state` pre-loads session state, which is how you start a case from a condition
rather than talking the agent into one.

## Multi-turn

Several invocations in one `conversation`, one `eval_id`, one `session_input`.
Turn 1 may legitimately have no tool calls:

```json
"conversation": [
  {"invocation_id": "inv_1",
   "user_content": {"parts": [{"text": "I work in the engineering cost centre."}], "role": "user"},
   "final_response": {"parts": [{"text": ""}], "role": "model"},
   "intermediate_data": {"intermediate_responses": [], "tool_uses": []}},
  {"invocation_id": "inv_2",
   "user_content": {"parts": [{"text": "Submit CLM-000901 for 900 dollars of travel."}], "role": "user"},
   "final_response": {"parts": [{"text": ""}], "role": "model"},
   "intermediate_data": {"intermediate_responses": [], "tool_uses": [
     {"name": "check_cost_centre_funds",
      "args": {"req": {"cost_centre": "engineering", "amount_usd": 900.0}}}
   ]}}
]
```

The assertion lives in turn 2's arguments: `engineering` appears there, and the
user never said it twice.

## Tool arguments

Arguments must match the tool's real signature. A tool taking a Pydantic model
nests them under the parameter name (`{"req": {...}}`); a tool with plain
parameters takes them flat. Comparison is by value, so `2499` equals `2499.0` but
not `"2499"`.

## Key style

**The top level is snake_case only.** `EvalSet` has no alias generator, so
`evalSetId` and `evalCases` are not recognised and the file fails to load with
`eval_set_id / Field required` — the camelCase key is simply dropped as an
unknown extra and the real field is then missing.

Inside `eval_cases` and below it, both styles parse: `EvalCase`, `Invocation`,
`IntermediateData`, `ConversationScenario`, `SessionInput` and `Rubric` all carry
`alias_generator=to_camel` with `populate_by_name=True`, so `evalId` and
`eval_id` are the same field. That is why a file written by the dev UI can look
camelCase throughout except at the top.

Pick one style per project. This plugin writes snake_case everywhere, matching
the docs and the CLI.

## Scenario cases

```json
{
  "eval_id": "memory_simulated_repeat_claims",
  "conversation_scenario": {
    "starting_prompt": "I need to claim some expenses from last week's trip.",
    "conversation_plan": "Say you work in engineering. Ask about a 900 dollar flight and then a 40 dollar meal, without repeating the cost centre. Confirm both outcomes, then stop.",
    "user_persona": "NOVICE"
  },
  "session_input": {"app_name": "app", "user_id": "evaluator", "state": {}}
}
```

No fixed turns means no reference, so reference-based metrics cannot score these.
See `/adk:eval-simulation`.

## Legacy format

A top-level JSON list of `{name, data: [{query, expected_tool_use, reference}],
initial_session}` still loads, converted on read with a random `eval_set_id`.
Migrate it with `migrate_legacy.py` for a stable id and a readable diff.

---

Sources: `adk-python: evaluation/eval_case.py`, `evaluation/eval_set.py`,
`evaluation/common.py`, `evaluation/evaluation_generator.py`,
`evaluation/local_eval_sets_manager.py`, `evaluation/conversation_scenarios.py`;
`adk-docs: docs/evaluate/index.md`.
