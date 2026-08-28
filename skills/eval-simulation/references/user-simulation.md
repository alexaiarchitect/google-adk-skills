# User simulation

## What it changes

A scripted case fixes the user's turns. A scenario case fixes the user's
**intent** and lets an LLM produce the turns:

```json
"conversation_scenario": {
  "starting_prompt": "I need to claim some expenses from last week's trip.",
  "conversation_plan": "Say you work in engineering. Ask about a 900 dollar flight, then a 40 dollar meal, without repeating the cost centre. Confirm both outcomes, then stop.",
  "user_persona": "NOVICE"
}
```

A case has either a `conversation` or a `conversation_scenario`, never both.

What that buys: phrasings nobody scripted, follow-ups nobody anticipated, and the
ordinary human behaviour of stating something once and expecting it to be
remembered. What it costs: the reference.

There is no fixed expected invocation to compare against, so ADK returns
`NOT_EVALUATED` for reference-based metrics here, and raises for the ones that
require an expected invocation outright. Nothing in the schema forbids the
combination — **this plugin refuses it up front**, as its own rule, so you are
told when you write the config instead of after a paid run comes back blank.

## Writing the plan

The plan is the specification, so write behaviour rather than topic.

```
Bad:  Ask about expenses.
Good: Say you work in engineering. Ask about a 900 dollar flight, then a 40 dollar
      meal, without repeating the cost centre. If the agent asks for it again,
      say you already told it. Confirm both outcomes, then stop.
```

Three things a good plan states:

1. **What the user reveals, and when.** This is how a memory test gets built
   without scripting turns.
2. **What they do not repeat.** The interesting failures live here.
3. **When they stop.** Otherwise the conversation runs until the cap.

## The simulator config

```json
"user_simulator_config": {
  "type": "llm_backed",
  "model": "gemini-flash-latest",
  "max_allowed_invocations": 8,
  "include_function_calls": false
}
```

| Field | Notes |
|---|---|
| `type` | `llm_backed`, or `llm_audio` for voice; a missing type is back-filled to `llm_backed` |
| `model` | set it: the default is a deprecated id |
| `max_allowed_invocations` | the cap. `-1` removes it, which is rarely what you want |
| `include_function_calls` | whether the simulated user sees the agent's tool calls. Usually false: real users do not |
| `custom_instructions` | a Jinja template replacing the default simulator prompt |

A custom prompt **must** keep `{{ stop_signal }}`, `{{ conversation_plan }}` and
`{{ conversation_history }}`, with `{{ persona }}` optional. Without them the
simulator cannot see the plan or know when to stop.

## Which metrics work

| Works | Does not |
|---|---|
| the three `rubric_based_*` metrics | `tool_trajectory_avg_score` |
| `hallucinations_v1` | `response_match_score` |
| `safety_v1` | `final_response_match_v2` |
| `multi_turn_*` (Vertex) | `response_evaluation_score` |
| `per_turn_user_simulator_quality_v1` | |

The rule is simple: if a metric needs something the eval case declared in
advance — an expected trajectory or an expected answer — it cannot score a
conversation that was invented at runtime.

**Use two configs.** One deterministic, for the scripted cases; one for the
simulated ones. Sharing a config means half the metrics quietly apply to half the
cases, which reads as a passing suite that is testing less than you think.

## Rubrics that suit simulated runs

Since there is no reference, the rubrics carry the assertion. The ones that earn
their place:

```
The agent uses a value the user gave earlier in the conversation instead of
asking for it again.

The agent does NOT call `submit_claim` when the cost centre is frozen.

When the agent declines, its response names the specific constraint that blocked
the request.
```

All three are true-or-false about a transcript, whatever words the simulated user
chose.

## Generating scenarios

```bash
adk eval_set generate_eval_cases <agent-dir> <eval_set_id> \
    --user_simulation_config_file generation_config.json
```

The generation config takes a `count`, a `generation_instruction`, an
`environment_context` and a `model_name`. It calls the Vertex AI evaluation SDK,
so it needs a project and location.

Treat the output as a draft. Generated scenarios drift toward the obvious cases
and occasionally describe something the agent cannot do at all; read them before
committing, as you would a captured trace.

## Cost

Every turn costs two model calls — one for the simulated user, one for the agent
— plus the judge calls for each metric on each invocation. With
`max_allowed_invocations: 8` and two judge metrics at three samples, a single case
can be over sixty calls. Cap the conversations, and keep simulated suites in the
nightly tier.

## When a simulated suite goes red

Check the harness before the agent. `per_turn_user_simulator_quality_v1` scores
whether the simulated user behaved as instructed; if it did not, the agent's
failure may be a reasonable response to an unreasonable user.

---

Sources: `adk-python: evaluation/conversation_scenarios.py`,
`evaluation/simulation/llm_backed_user_simulator.py`,
`evaluation/simulation/user_simulator_provider.py`, `evaluation/eval_config.py`,
`cli/cli_tools_click.py`; `adk-docs: docs/evaluate/user-sim.md`,
`docs/evaluate/criteria.md`.
