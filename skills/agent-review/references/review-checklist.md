# Review rules

Every check `lint_agent.py` performs, with what triggers it and where the rule
comes from. Ids are stable.

## Agent configuration

| Id | Severity | Trigger | Why |
|---|---|---|---|
| `AGT001` | warn | `SequentialAgent`, `ParallelAgent` or `LoopAgent` | superseded by `google.adk.workflow.Workflow`; keep only where an `LlmAgent` must transfer into the block |
| `AGT002` | warn | `global_instruction=` | deprecated; use `GlobalInstructionPlugin` on the `App`, imported from `google.adk.plugins.global_instruction_plugin` |
| `AGT003` | **error** | `generate_content_config` carrying `tools`, `system_instruction` or `response_schema` | ADK owns these and raises at construction; they belong in `tools=`, `instruction=`, `output_schema=` |
| `AGT004` | warn | a hardcoded dated model id | it will be retired |
| `AGT005` | warn | `gemini-2.0-flash` or `gemini-2.5-flash` | retiring. This is the plugin's policy, from `adk-recipes: AGENTS.md` — ADK does not deprecate these ids and still defaults the judge and simulation models to `gemini-2.5-flash`, so it warns rather than errors |
| `AGT006` | warn | a preview or `-exp` model id | withdrawn without notice |
| `AGT007` | info | no `model=` | fine for a sub-agent, which inherits; the root agent should be explicit |
| `AGT011` | **error** | a name that is not a Python identifier, or is `"user"` | rejected by the validator |
| `AGT012` | **error** | an `LlmAgent` with no `name` | required |
| `AGT015` | warn | duplicate `sub_agents` names | ADK only warns, but routing by name becomes ambiguous |
| `AGT016` | **error** | `temperature`, `top_p`, `max_output_tokens` and friends passed to the agent | raises at construction; move them into `generate_content_config` |
| `AGT019` | info | `output_schema` together with `tools` | supported in 2.x, but the model may answer with structured output instead of calling a tool |
| `AGT020` | warn | no `instruction` | behaviour is whatever the model guesses, and nothing is testable |

## Tools

| Id | Severity | Trigger | Why |
|---|---|---|---|
| `AGT040` | **error** | no docstring | the docstring is the entire description the model gets |
| `AGT041` | warn | a docstring under ~40 characters | too thin to disambiguate from a neighbouring tool |
| `AGT042` | warn | no `Args:` section | the model fills arguments from this text |
| `AGT043` | **error** | a parameter with no type annotation | ADK builds the declaration from the hints |
| `AGT044` | warn | `raise` inside a tool | an exception ends the invocation; a returned `{"status": "error"}` is an observation the model can correct from |
| `AGT045` | info | returns something that is not a dict or a string | a dict gives judges and groundedness checks something to read |
| `AGT046` | info | a returned dict with no `status` key | makes success and failure explicit |
| `AGT047` | warn | `time.sleep`, `requests`, `subprocess` in a sync tool | a sync tool is awaited inline, so a blocking call freezes the whole run |
| `AGT030` | info | a bound tool defined outside the project | its contract could not be read statically |

## State, app and structure

| Id | Severity | Trigger | Why |
|---|---|---|---|
| `AGT013` | **error** | `session.state[...] = ...` on a service-fetched session | not recorded as an event, so not persisted and not replayed |
| `AGT014` | info | no `App` exported | the loader prefers one, and plugins, caching and resumability only reach a run through it |
| `AGT050` | warn | a `root_agent.yaml` config agent | the config classes are deprecated in 2.x |
| `AGT051` | **error** | nothing loadable exported | `AgentLoader` looks for `app`, then `root_agent`; `adk eval` and `AgentEvaluator` additionally accept `get_agent_async`, so a factory-only module evaluates but will not load in `adk web` / `adk run` |
| `AGT000` | **error** | the file could not be parsed | |

## Context caching

When each is worth acting on, and how to prove a caching change paid:
`../../agent-create/references/context-caching.md`.

| Id | Severity | Trigger | Why |
|---|---|---|---|
| `AGT060` | warn | `static_instruction` built with an f-string, `.format()`, `%`, a `datetime`/`date`/`time`/`uuid`/`random`/`secrets` call, a `{var}` placeholder, or an `InstructionProvider` / lambda (followed through `Content`/`Part` wrapping, lists, `+`, `dedent`, module constants) | it is the cached prefix, so any change invalidates the cache; and ADK sends it verbatim, so placeholders are never filled. Move the varying part to `instruction=` |
| `AGT061` | info | an instruction of ≥ 8,000 characters, no `static_instruction`, no App `context_cache_config` | about 2k tokens resent every request; split and cache it if the agent is multi-turn |
| `AGT062` | **error** | `ContextCacheConfig` out of bounds (`cache_intervals` 1–100, `ttl_seconds` > 0, `min_tokens` ≥ 0) or an unknown field; a name bound to a constant is followed | building the App raises `ValidationError` |
| `AGT063` | info | caching on, and every eval case is one turn with no expected tool call | each session makes one model request, and the first only records a fingerprint: the suite cannot show a cache hit |
| `AGT065` | warn | a plugin imported from `google.adk.plugins._…` | a private module path; import from `google.adk.plugins` (`ToolCallIntegrityPlugin` / `Error` need 2.11+) |
| `AGT066` | **error** | `ToolCallIntegrityPlugin` with a resolved runner older than 2.11 (reported by discovery) | the import fails before the agent loads; the only check gated on 2.11 |
| `AGT067` | info | `context_cache_config` is set but the estimated static prefix (instruction chars / 4 plus about 60 tokens per bound tool, exposed as `cache_prefix_tokens_estimate`) is below `max(min_tokens, 2048)`; skipped when the prefix is built at runtime | ADK creates a cache only once the previous request reached `min_tokens`, and the model floor is 2048 (Gemini 2.5) or 4096 (Gemini 3), so early turns never qualify and later hits are mostly history; grow the procedure, lower `min_tokens` only above the floor, or drop the config. The upstream `cache_analysis` sample earns this note (about 3,370 tokens against `min_tokens=4096`) |

No `AGT064`: `global_instruction` is `AGT002`. `AGT020` does not fire when only
`static_instruction` is set.

## Evaluation readiness

| Id | Severity | Trigger |
|---|---|---|
| `AGT052` | warn | the instruction yields no testable rule |
| `AGT053` | info | the instruction has no conditional or prohibition, so there is one path to test |
| `AGT055` | info | the agent is not statically readable: `Agent(**kwargs)`, or an export that is an object's attribute such as `root_agent = wrapper._agent`. Model, instruction and tool checks are skipped, an expected tool call counts as unverifiable rather than unknown, and the Found block says so in `agent_unresolved` / `agent_unresolved_reason` / `tools_unresolved`; run `load_agent.py` for the loaded facts |
| `GAP-EVALSET` | gap | no evalset anywhere in the project |

## Environment

| Id | Severity | Trigger |
|---|---|---|
| `ENV003` | warn | no backend configured in the agent's `.env` |
| `ENV004` | **error** | a backend selected but missing project or location |
| `ENV006` | info | the model comes from the environment and `MODEL_NAME` is unset, so the code default applies |
| `ENV013` | warn | `App.name` differs from the agent directory |

## Reading the severities

- **error** — ADK rejects it, or the behaviour cannot work. Some stop the agent
  loading at all.
- **warn** — it runs, but not as intended, or not for much longer.
- **info** — worth knowing; no action implied.

A clean review is not a guarantee the agent works: it is a guarantee that these
specific things are not wrong. The load check (`load_agent.py`) proves it
imports; evals prove it behaves.

---

Sources: `adk-python: agents/base_agent.py`, `agents/llm_agent.py`,
`agents/loop_agent.py`, `apps/app.py`, `agents/context_cache_config.py`
(`AGT062`'s bounds), `tools/function_tool.py`,
`cli/utils/agent_loader.py`, `agents/config_agent_utils.py`;
`adk-docs: docs/tools-custom/function-tools.md`, `docs/sessions/state.md`,
`docs/2.0/index.md`, `docs/context/caching.md` (`AGT060`–`AGT063`, `AGT065`–`AGT067`); `adk-recipes: AGENTS.md` (`AGT005`, the model-deprecation
policy — the sole source for it).
