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

Current dated ids, if you must name one: `gemini-3.5-flash` (ADK's own
`LlmAgent.DEFAULT_MODEL` on both 2.9.2 and 2.10.0, and what `adk-recipes`
prescribes) and **`gemini-3.8-flash`**, which 2.10's `adk create` offers as its
second choice — 2.9.2 offered only `gemini-3.5-flash`. Neither is deprecated and
neither is preferred here: this plugin's own scaffolding still writes
`gemini-flash-latest`, because an alias is the thing that does not need
revisiting. `gemini-3.8-flash` is not mentioned in `adk-recipes: AGENTS.md`; the
`adk create` menu is the only source for it, which is reason enough not to treat
an agent that lacks it as out of date.

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
(`_prompt_for_model`, where `gemini-3.8-flash` appears as option 2 in 2.10.0).
`adk-recipes: AGENTS.md`'s `## Models` section was re-read after the 2026-09-27
upstream pull: unchanged, still the two deprecations and `gemini-3.5-flash` as the
replacement.
