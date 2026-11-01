# Model policy

## The rule

Read the model id from the environment:

```python
MODEL = os.getenv("MODEL_NAME", "gemini-flash-latest")
root_agent = Agent(name="claims_agent", model=MODEL, ...)
```

```dotenv
MODEL_NAME=gemini-flash-latest
```

Two reasons. A dated id gets retired, and an agent that hardcodes one stops
working on a day nobody wrote down. And an environment variable lets the same
code run against a cheaper model in development and a stronger one in
production, without a diff.

## Grades

| Grade | Examples | Verdict |
|---|---|---|
| **alias** | `gemini-flash-latest`, `gemini-pro-latest` | preferred: it follows the current release |
| **pinned** | a dated or versioned id | warning when hardcoded; fine when it comes from the environment and someone owns the upgrade |
| **preview** | anything with `preview` or `-exp` | warning: preview ids disappear without notice. Fine for an experiment, wrong for anything committed |
| **retiring** | `gemini-2.0-flash`, `gemini-2.5-flash` | warning: replace it when you touch the file |

Current dated ids, if you must name one. They disagree, which is itself the
argument for an alias:

| Source | Id |
|---|---|
| `adk-recipes: AGENTS.md` (`## Models`, re-read 2026-10-11) | **`gemini-3.7-flash`** — the recommended replacement for the two retiring ids (it said `gemini-3.5-flash` until this sync) |
| `LlmAgent.DEFAULT_MODEL`, 2.9.2 through 2.12.0 | `gemini-3.5-flash` |
| `adk create` menu, 2.10.0 through 2.12.0 | `gemini-3.5-flash` (option 1), `gemini-3.8-flash` (option 2); 2.9.2 offered only the first |

None of the three is deprecated and none is preferred here: this plugin's own
scaffolding still writes `gemini-flash-latest`, because an alias is the thing
that does not need revisiting. An agent that names `gemini-3.5-flash` is not out
of date because the recipes moved on; recommend the alias, or the recipes' id if
the team wants a dated one, and say whose recommendation each is.

## Where the "retiring" verdict comes from

This is **the plugin's policy, not an ADK rule.** The source is
`adk-recipes: AGENTS.md`, which says not to use `gemini-2.0-flash` or
`gemini-2.5-flash` because both are deprecated. ADK itself has no such rule and
still ships `gemini-2.5-flash` as the default for `JudgeModelOptions.judge_model`
and `EnvironmentSimulationConfig.simulation_model`.

So the reviewer **warns**; it does not error. An error is reserved for an id that
no longer resolves at all. Cite the source when you report one of these, so the
user can weigh a recipes convention against their own.

## The judge model is the one people forget

`JudgeModelOptions` defaults to **`gemini-2.5-flash`** with `num_samples: 5`. So
an eval config that omits `judge_model_options` quietly runs a retiring judge
five times per invocation — the cost is the bigger half of that sentence.

Always write it out:

```json
"judge_model_options": {"judge_model": "gemini-flash-latest", "num_samples": 3}
```

The same applies to the user-simulator model and the environment-simulation
model, which default the same way.

## Where ids hide

- the agent's `model=`, directly or through a wrapper such as `Gemini(model=...)`
- `judge_model` in every judge metric in every eval config
- `user_simulator_config.model`
- `EnvironmentSimulationConfig.simulation_model`
- `.env` and `.env.example`
- CI workflow environment blocks

The review checks the first; `/adk:eval-criteria` checks the rest.

## Live and embedding models

Live mode uses a different family (`gemini-live-...-native-audio`), and
embeddings another (`gemini-embedding-001`). They follow the same rule: read them
from the environment.

## An output cap is not a thinking budget

On a thinking model, `max_output_tokens` is a ceiling on everything the model
emits, thoughts included — "a ceiling, not a thinking-budget dial", in Google's
own write-up. Lowering it does not make an easy turn think less (easy answers
never come near the cap); it makes a hard turn stop mid-reasoning, so the reply
is truncated or wrong, and nothing warns: ADK accepts
`GenerateContentConfig(max_output_tokens=300)` silently on every version
(measured on 2.9.2 and 2.13.0). `AGT084` (info) reports a cap below 1024 on an
agent whose model id is in the plugin's thinking family — `gemini-2.5-*`, every
`gemini-3*` text model, and the `-latest` aliases (adk-docs lists no thinking
ids, so the family is this plugin's, from the Gemini thinking behaviour the
article describes). A config that sets `thinking_budget=0` or
`thinking_level=MINIMAL` is exempt; an id that only resolves at runtime is
skipped. To spend less, set the thinking level (`types.ThinkingConfig(thinking_level=...)`)
and bound the answer's length in the instruction.

Two more cost habits from the same source. **Start with a fast model at a low
thinking level** and measure cost per passing case before building a router
(`/adk:agent-create` `workflow-graphs.md`, "Routing with a classifier"). And **a
context cache belongs to one model**: an agent tree that switches models between
turns forfeits the cache discount, so keep long, context-heavy turns on one model
(`/adk:agent-create` `context-caching.md`). Off-peak capacity for batch work is a
run setting, not a model choice: `RunConfig(service_tier=ServiceTier.DEFERRED)`
(2.10+, Preview, `/adk:eval-run` `batch-and-gating.md`).

**Coming after 2.13.0 (unreleased main, `d5ecb990f`).** A `LiteLlm` tool call
whose arguments cannot be used (a non-string key at any depth, a set or bytes
from the Python-literal repair, an oversized integer, very deep nesting) is
reported as `MALFORMED_FUNCTION_CALL` instead of raising out of response parsing
or reaching the tool as that value. Through 2.13.0 such a turn crashes the run or
hands the tool a set; on main it is a finish reason an eval can count.

## When a literal is unavoidable

Sometimes a specific version is the point — reproducing a result, or pinning
behaviour during a migration. That is fine when it is deliberate: leave a comment
saying why and when to revisit. The warning exists to catch the id that was
typed once and forgotten.

---

Sources: `adk-recipes: AGENTS.md` (the deprecation list this policy is built on
— the sole source; ADK does not deprecate these ids);
`adk-python: agents/llm_agent.py` (`DEFAULT_MODEL`),
`evaluation/eval_metrics.py` (`JudgeModelOptions`),
`evaluation/simulation/llm_backed_user_simulator.py`,
`tools/environment_simulation/environment_simulation_config.py`;
`adk-docs: docs/agents/models/*`; `adk-python: cli/cli_create.py`
(`_prompt_for_model`, where `gemini-3.8-flash` appears as option 2 in 2.10.0 through
2.12.0; `cli_create.py` is unchanged on main `a6ed859`). `adk-recipes: AGENTS.md`'s `## Models` section was re-read after the
2026-10-11 upstream pull (`d079ffce`): still the two deprecations, with
`gemini-3.7-flash` now named as the replacement. Output cap and cost
habits: Villani, "Cost per task: Using cheap classifiers to route across Gemini
models and thinking levels", Google Developers Blog, 2026-11-03,
https://developers.googleblog.com/cost-per-task-using-cheap-classifiers-to-route-across-gemini-models-and-thinking-levels/
(paraphrased; one phrase quoted; no figures reproduced); the silent acceptance of
`max_output_tokens=300` measured on 2.9.2 and 2.13.0 with `gemini-3.8-flash`,
`gemini-3.8-pro`, `gemini-flash-latest` and `gemini-2.5-flash`. LiteLlm:
`models/lite_llm.py` at `70e1cbde7` (`d5ecb990f`, post-tag).
