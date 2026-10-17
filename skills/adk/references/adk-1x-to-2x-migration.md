# ADK 1.x patterns and their 2.x replacements

These skills target ADK 2.x. Nothing here breaks an eval *file* — the evalset and
config schemas did not change, and the legacy list format still loads — but
several agent-side patterns are deprecated or now behave differently.

## Agent construction

| 1.x pattern | 2.x |
|---|---|
| `SequentialAgent`, `ParallelAgent`, `LoopAgent` | `google.adk.workflow.Workflow` with explicit edges. Keep the old shell only where an `LlmAgent` must be able to transfer into the orchestrated block, which `Workflow` cannot yet be a sub-agent of. |
| `global_instruction=...` on the agent | `GlobalInstructionPlugin` on the `App`, imported from `google.adk.plugins.global_instruction_plugin` (not from `google.adk.plugins`) |
| exporting only `root_agent` | export `app = App(name=..., root_agent=root_agent)`; the loader prefers it, and plugins, context caching, compaction and resumability only reach a run through it |
| `output_schema` excludes tools | both are supported together now; the model may still answer with structured output instead of calling a tool, so check the trajectory before blaming the prompt |
| overriding `_run_async_impl()` on a `BaseAgent` subclass | `BaseAgent` extends `BaseNode`; the override is silently bypassed. Use before/after agent callbacks or a `Workflow` node |
| `session.events.append(...)`, manual event enqueue | yield events |
| `except BaseException` around a run | never: it traps the interrupt that drives human-in-the-loop |

`Agent` is still an alias for `LlmAgent`, so `from google.adk.agents import Agent`
needs no change.

## Sampling parameters

Passing `temperature=`, `top_p=`, `max_output_tokens=` and friends straight to
`LlmAgent` raises at construction. They belong in
`generate_content_config=types.GenerateContentConfig(...)` — which in turn must
**not** carry `tools`, `system_instruction` or `response_schema`, because ADK owns
those (they are `tools=`, `instruction=` and `output_schema=` on the agent).

## State

Write through `ctx.state` / `tool_context.state`, or an `Event(state=...)`. A
direct `session.state[key] = value` on a session fetched from a session service
is not persisted, is not part of the event history, and is not replayed — so it
works in a demo and vanishes in production. State prefixes: `user:` per user
across sessions, `app:` app-wide, `temp:` for the current invocation only.

## Evaluation surface

| Thing | Status in 2.x |
|---|---|
| evalset JSON schema | unchanged; camelCase and snake_case both parse |
| legacy list format (`[{query, expected_tool_use, reference}]`) | still loads, converted on read with a warning and a random `eval_set_id`; migrate for a stable diff |
| `criteria` as bare floats | still works; criterion objects (`{"threshold": ..., "match_type": ...}`) are the current form |
| `EvalMetric.threshold` | soft-deprecated in favour of `criterion.threshold` |
| `EvalCaseResult.eval_set_file`, `.eval_metric_results` | deprecated; use `eval_set_id` and `overall_eval_metric_results` |
| `AgentEvaluator(criteria={...})` | deprecated; pass an `EvalConfig` |
| dev-server eval routes | moved under `/dev/...` with hyphenated paths; underscore variants are deprecated aliases |
| `ignore_args` on tool trajectory | new in 2.9, unchanged through 2.12 |
| the four efficiency metrics | new in 2.10: reported on every eval, informational (`eval_status: 4`), and refused if you name them in `criteria` |
| `EvalStatus` | gained `INFORMATIONAL = 4` in 2.10; anything parsing the int must not read 4 as a pass or a fail |
| `NOT_EVALUATED` | reported as "not a score regression" by `AgentEvaluator`, still counted as failed by the `adk eval` summary |

## Custom metrics

The evaluation function now receives the threshold on the metric's criterion:
ADK blanks `eval_metric.threshold` before calling, so read
`eval_metric.criterion.threshold`. The signature also gained a
`conversation_scenario` parameter.

## YAML config agents

`root_agent.yaml` still loads and the docs still describe it as experimental, but
the config classes behind it are deprecated in the library. Migrate an existing
one to code; do not start a new agent that way.

## What to do with a 1.x project

1. Run `/adk:agent-review` — the drift rules here map to this table.
2. Fix construction-time errors first (sampling kwargs, `generate_content_config`
   contents, reserved names): those stop the agent loading at all.
3. Add an `App` export, then move plugins onto it.
4. Leave the evalsets alone. They still work, and changing the specification at
   the same time as the implementation costs you the ability to tell which one
   broke.

---

Sources: `adk-python: agents/llm_agent.py`, `agents/base_agent.py`,
`agents/loop_agent.py`, `apps/app.py`, `evaluation/eval_metrics.py`,
`evaluation/eval_result.py`, `evaluation/agent_evaluator.py`,
`evaluation/custom_metric_evaluator.py`, `evaluation/local_eval_sets_manager.py`,
`evaluation/_efficiency_evaluators.py` (2.10.0),
`cli/dev_server.py`, `CHANGELOG.md`; `adk-docs: docs/2.0/index.md`,
`docs/sessions/state.md`, `docs/agents/config.md`.
