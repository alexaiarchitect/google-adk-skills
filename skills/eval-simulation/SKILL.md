---
name: eval-simulation
description: >-
  Sets up simulation-based Google ADK evaluation. Covers user simulation
  (conversation scenarios with a starting prompt and a plan, personas including
  EXPERT, NOVICE and EVALUATOR or custom ones whose violation rubrics judge the
  simulated user, the llm_backed and llm_audio simulator configs and their
  invocation caps), which metrics can and cannot score a simulated
  conversation, scenario generation through the Vertex AI evaluation service,
  and environment simulation, which mocks the tool backends with injected
  errors and latency so failure paths can be tested. Use when asked to
  "simulate users", "test with different personas", "generate conversation
  scenarios", "test multi-turn behaviour without scripting it", "mock a tool
  failure", or "test what happens when the API is down". Don't use for fixed
  golden-trace cases (use `/adk:eval-create`) or to pick metrics generally (use
  `/adk:eval-criteria`).
argument-hint: "[agent-dir] [--kind user|env|generate]"
allowed-tools: Read, Grep, Glob, Write, Edit, AskUserQuestion, Skill, Bash(python3 *), Bash(uv run *), Bash(adk *)
---

# Simulated users and simulated environments

Two different things, both called simulation:

- **User simulation** replaces the fixed user turns with an LLM playing a plan.
  It finds what scripted cases cannot: phrasings you did not think of, follow-ups
  you did not anticipate, and the human habit of not repeating yourself.
- **Environment simulation** replaces the *tools* with mocks that can return
  errors and add latency. It is how you test failure paths without breaking
  anything real. Through 2.13.0 it is an experimental ADK feature (it warns);
  main after 2.13.0 marks it stable.

The constraint that shapes all of user simulation: **no fixed conversation means
no reference**, so this plugin keeps reference-based metrics off simulated
cases (ADK itself would leave them never-scored).

`${CLAUDE_SKILL_DIR}` is this skill's directory.

## Flow

1. **Discover.** What exists, and whether the current config could score it:

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/../adk/scripts/discover.py <agent-dir> --focus layout,env,agent,evalsets,configs
   python3 ${CLAUDE_SKILL_DIR}/scripts/validate_sim_config.py <agent-dir>
   ```

2. **Report.** Scenario cases versus static cases, whether a simulator is
   configured, which metrics can score simulated runs, and which cannot.

3. **Options.** Four, and they have different costs:

   | Option | Cost | What it gets you |
   |---|---|---|
   | add scenario cases with a plan | judge | unscripted multi-turn coverage |
   | add a separate simulation config | judge | metrics that can actually score them |
   | generate scenarios from a description | vertex | breadth, quickly, and some noise |
   | add environment simulation | free | failure paths without touching production |

4. **Confirm, generate, validate.** Then run the simulated suite with its own
   config, not the deterministic one. Generated scenarios are a draft to read
   before committing, like a captured trace.

## What a scenario case looks like

```json
{
  "eval_id": "memory_simulated_repeat_claims",
  "conversation_scenario": {
    "starting_prompt": "I need to claim some expenses from last week's trip.",
    "conversation_plan": "Say you work in engineering. Ask about a 900 dollar flight, then a 40 dollar meal, without repeating the cost centre. Confirm both outcomes, then stop.",
    "user_persona": "NOVICE"
  },
  "session_input": {"app_name": "app", "user_id": "evaluator", "state": {}}
}
```

The **plan is the specification**. Write it as behaviour, not topic: what the
user states early, what they ask for later without repeating, when they push
back, when they stop.

## Metric compatibility

| Can score a simulated conversation | Cannot |
|---|---|
| `rubric_based_final_response_quality_v1` | `tool_trajectory_avg_score` |
| `rubric_based_tool_use_quality_v1` | `response_match_score` |
| `rubric_based_multi_turn_trajectory_quality_v1` | `final_response_match_v2` |
| `hallucinations_v1`, `safety_v1` | `response_evaluation_score` |
| `multi_turn_*`, `per_turn_user_simulator_quality_v1` | |

**Keep two configs.** The deterministic one scores the scripted cases; a
simulation one scores the scenarios. Mixing them means half the metrics silently
apply to half the cases.

## Decision rules

- **Simulation is a supplement, not a replacement.** Scripted cases are
  deterministic, cheap and localizing; simulated ones are broad and noisy.
- **Cap the conversation.** `max_allowed_invocations` bounds it; every turn costs
  a model call for the simulator *and* one for the agent.
- **Name the simulator's model.** Unset, it defaults to `gemini-2.5-flash`; this
  plugin's policy prefers an alias id.
- **Write the plan as behaviour.** "Ask about expenses" produces nothing
  reproducible; "state your cost centre once, then ask about two claims without
  repeating it" tests something specific.
- **A custom simulator prompt must keep its placeholders** — `stop_signal`,
  `conversation_plan`, `conversation_history` — or the simulator cannot see the
  plan or know when to stop.
- **Persona violation rubrics judge the simulated user**, not the agent. They are
  how you check the harness behaved; `per_turn_user_simulator_quality_v1` scores
  them.
- **Environment-simulation fields are exact.** `mock_strategy_type` is an
  integer enum, `injection_probability` defaults to **1.0**, an `InjectedError`
  needs an HTTP code and a message, and every injection carries exactly one of
  an error or a response. Keep probabilities at 0 except in the case that is
  about the failure.
- **A voice agent gets a voice suite.** `type: llm_audio` plus
  `live_model_config` runs the cases through TTS and the Live API; nothing in
  the audio block is required and ADK accepts misspelled keys, so generate it
  with `gen_sim_config.py --audio` and let `validate_sim_config.py` check it
  (`SIM032`–`SIM034`). Drop `live_model_config` to debug the same cases in text.
  Judge the conversation with an intent rubric, vary voices and personas, and
  keep live suites out of the PR tier unless the budget allows. Only the live
  path exercises streaming and non-blocking tools; in text they do not stream
  (`AGT081`). Grade acknowledgement-before-result, not update counts.
- **Semantic argument matching is main-only.** `InjectionConfig.args_matcher`
  (a `semantic` matcher that asks a model whether two values mean the same)
  arrives after 2.13.0; on 2.13.0 the key is ignored, so keep `match_args`
  exact until a release has it.
- **Scenario generation spends on the Vertex evaluation service** and needs
  `GOOGLE_API_KEY` or a project and location; the wrapper checks before paying.

## References

| Question | File |
|---|---|
| Scenarios, plans, simulator configs, live and voice evals | [user-simulation.md](references/user-simulation.md) |
| Personas and violation rubrics | [personas.md](references/personas.md) |
| Mocking tools, injecting errors and latency | [environment-simulation.md](references/environment-simulation.md) |

Templates: `assets/templates/` — a simulation eval config, a custom persona, a
generation config, and an environment-simulation module.

## Scripts

| Command | Does | Exit codes |
|---|---|---|
| `scripts/gen_scenarios.py <agent-dir> --spec JSON (--evalset F \| --new ID)` | writes scenario cases, checks the config can score them | 0, 1 findings or clash, 2 usage |
| `scripts/gen_sim_config.py --kind user\|env\|generate [--model M] [--tools ...] [--out F]` | generates a config with the real field names | 0, 2 usage |
| `scripts/gen_sim_config.py --kind user --audio [--audio-model M] [--voice V] [--language L] [--live-timeout S]` | an `llm_audio` simulator with the voice nested where ADK reads it (`audio_model_configuration.speech_config...`), defaults `cloud_tts`, `en-US-Studio-O`, `en-US`; `--live-timeout S` adds `live_model_config` (Live API inference), omit it to run in text | 0, 2 usage |
| `scripts/validate_sim_config.py <agent-dir> [--config F]` | checks scenarios, simulator and metrics agree; for live runs, `SIM032` timeout ≤ 0, `SIM033` live config on a model with no `live`/`native-audio` in its id (`gemini-3.8-live` counts), `CFG076` the multi-turn trajectory judge with a built-in tool, `SIM034` unknown simulator key | 0 clean, 1 findings, 2 nothing to check |
| `scripts/generate_eval_cases.py <agent-dir> <eval_set_id> --config F [--dry-run]` | asks the evaluation service for scenarios, validates the result | 0, 1 errors, 2 env |

## Completion checklist

- [ ] Every scenario case has a plan that describes behaviour, not a topic.
- [ ] The simulated suite has its own config, with metrics that can score it.
- [ ] The simulator names its model and caps its invocations.
- [ ] Personas are pre-built ids or fully specified objects with list-valued
      instructions and violation rubrics.
- [ ] Injected environment failures are off unless a case needs one.
- [ ] `validate_sim_config.py` exits 0.
