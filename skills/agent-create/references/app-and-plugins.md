# App, callbacks and plugins

## Export an App

```python
from google.adk.apps import App

root_agent = Agent(name="claims_agent", model=MODEL, instruction=INSTRUCTION, tools=[...])
app = App(name="app", root_agent=root_agent)
```

The loader looks for `app` before `root_agent`, and an `App` is the only way
plugins, context caching, event compaction and resumability reach a run —
including an eval run, which builds its runner from the App when one is exported.

`App.name` must start with a letter and use letters, digits, underscores and
hyphens, and cannot be `"user"`. Keep it the same as the agent directory name:
the eval runner derives its `app_name` from the session, the results file is
named after the directory, and ADK warns when the two disagree.

## Callbacks: per agent

Eight hooks. Each takes a single callable or a list (run in order, stopping at
the first non-`None`), and each is sync or async.

| Hook | Return a value to |
|---|---|
| `before_agent_callback` | skip the agent, returning your `Content` |
| `after_agent_callback` | replace the output |
| `before_model_callback` | skip the model call, returning your `LlmResponse` |
| `after_model_callback` | replace the response |
| `on_model_error_callback` | suppress the error with a response |
| `before_tool_callback` | skip the tool, returning your dict (**even an empty one**) |
| `after_tool_callback` | replace the tool result |
| `on_tool_error_callback` | suppress the error with a result |

**Return `None` to continue.** Returning a value is an override, which is why an
accidental `return {}` from a `before_tool_callback` silently disables the tool.

## Plugins: per app

The same hooks, registered once on the `App` and applied everywhere, plus run-level ones (`on_user_message_callback`, `before_run_callback`, `on_event_callback`, `after_run_callback`, and the error hooks). All async, keyword-only.

Built-ins worth knowing — **and where each one actually imports from**, because only some are re-exported by the package:

| Plugin | Import from |
|---|---|
| `LoggingPlugin`, `DebugLoggingPlugin` | `google.adk.plugins` |
| `ReflectAndRetryToolPlugin`, `ReflectAndRetryModelPlugin` | `google.adk.plugins` |
| `ToolCallIntegrityPlugin`, `ToolCallIntegrityError` (**2.11+**) | `google.adk.plugins` — lazily exported; `ImportError` on 2.9 and 2.10 |
| `GlobalInstructionPlugin` (replaces the deprecated `global_instruction`) | `google.adk.plugins.global_instruction_plugin` |
| `SaveFilesAsArtifactsPlugin` | `google.adk.plugins.save_files_as_artifacts_plugin` |
| `AutoTracingPlugin` | `google.adk.plugins.auto_tracing_plugin` |
| `ContextFilterPlugin` | `google.adk.plugins.context_filter_plugin` |
| `ModelArmorPlugin`, `ModelArmorConfig` | `google.adk.integrations.model_armor` — **not** under `plugins` at all |

`google.adk.plugins.__all__` is exactly `BasePlugin`, `PluginManager` and the names in the first three rows, so `from google.adk.plugins import GlobalInstructionPlugin` raises `ImportError` — the package's `__getattr__` lazily resolves only the names it lists. The others need their submodule path. The lazy list was four names on 2.9.2 and 2.10.0; 2.11.0 adds the integrity pair, so code that imports them has to require `google-adk>=2.11`. 2.13.0 adds `ForceEscalationPlugin`, `ModelConsultObservabilityPlugin`, `ToolResultClassifier` and `ToolResultClassification` (read in the installed 2.13.0 wheel).

### 2.13, in one line each

- **Stable from 2.13**: tool confirmation (`require_confirmation`), tool config, authenticated function tools, progressive SSE streaming, JSON-schema function declarations, the `AGENT_CONFIG` flag and six more. Their `[EXPERIMENTAL] feature ... is enabled.` warnings are gone; the YAML loader still warns through 2.13.0 (main after 2.13.0 drops that one too, below).
- **`ADK_SUPPRESS_EXPERIMENTAL_FEATURE_WARNINGS=1`** silences the experimental warnings that remain through 2.13.0 (decorator ones on 2.9.2 already, feature-registry ones from 2.13); `/adk:adk` `check_env.py` knows it.
- **`pip install "google-adk[otel]"`** pulls the OTLP exporter and GenAI/gRPC/httpx instrumentation; without it a configured exporter now degrades to a warning.
- **`ForceEscalationPlugin()`** (`google.adk.plugins`) forces `ModelConsultTool` calls a weak executor would skip: the first step, after repeated identical calls, or before named state-changing tools. Forced consults are tool calls, so the trajectory changes.
- **Duplicate agent names** anywhere in the tree are logged (siblings before 2.13; cousins and an agent reused below itself from 2.13); `find_agent()` returns the first depth-first match. `AGT015` reports the whole tree.
- **Eval views from production logs** (`BigQueryAgentAnalyticsPlugin(project_id, dataset_id, config=BigQueryLoggerConfig(create_eval_views=True))`, or `await plugin.create_eval_views()` on demand; absent on 2.9.2): `v_eval_session` (one row per session: turn, tool-call and error counts, `has_tool_loop`, token and latency totals, `eval_ready`, a multi-turn `trace` JSON) and `v_eval_turn` (one row per invocation), prefix set by `view_prefix`. They need the `content` column (denylisting it raises `ValueError`) and all 15 required events (`USER_MESSAGE_RECEIVED` … `INVOCATION_COMPLETED`; an allow- or denylist that drops one is rejected). Use them to pick the production sessions worth turning into eval cases; they do not score anything.
- **Deferred capacity** (`RunConfig(service_tier=ServiceTier.DEFERRED)`, 2.10+, Preview, allowlisted projects, `Gemini(use_interactions_api=True)` only): `/adk:eval-run` `batch-and-gating.md` has what it takes and why `adk eval` cannot pass it on.

### Coming after 2.13.0 (unreleased main)

Post-tag at `adk-python` `dfd534a88`; nothing here is in a release:

- **No more `[EXPERIMENTAL]` on App features** (`c54b253eb`): through 2.13.0 `App(resumability_config=...)`, `EventsCompactionConfig`, context caching (the warning comes from `GeminiContextCacheManager`; `ContextCacheConfig` never warned), the credential services behind tool auth and the YAML loader all warn; main graduates them.
- **`google.adk.plugins` exports every shipped plugin** (`4496bef70`), incl. `GlobalInstructionPlugin` and `BigQueryAgentAnalyticsPlugin`; the submodule paths above work on every version, so keep them while you support 2.13.0.
- **Which hook is running** (`2084efbe1`): contexts carry `callback_info` (`CallbackInfo(type=CallbackType.BEFORE_TOOL)` and so on), so one function can serve several hooks.
- **`InMemorySessionService.create_session` copies `state`** (`cec52fc00`): a test that mutated the dict it passed and expected the session to follow breaks.

Checked again at `ee8720162` (2026-11-02), also main only:

- **Compaction stops when the run stops** (`87d9dcdf5`): if the invocation aborts (`abort_signal`, a cancelled task), an in-flight compaction summary is cancelled and no compaction event is appended afterwards.
- **`FsspecArtifactService`** (`6bf806a01`, experimental, behind the `FSSPEC_ARTIFACT_SERVICE` feature, off by default; `pip install "google-adk[fsspec]"` plus the protocol backend such as `gcsfs`): reads and lists artifacts in the GCS object layout over any fsspec filesystem; saving and deleting raise `NotImplementedError`, so it is not yet a drop-in artifact service.
- **YAML configs refuse more references** (`1c7e8f76b`): the module denylist, on by default, now also blocks `anyio`, `click`, `dotenv`, `graphviz`, `uvicorn`, `watchdog`, anything under `google.adk.cli`, `pydantic._internal` or `pydantic.v1`, and any private `google.adk` module or symbol (`Blocked module reference: ...`). The key denylist (`args`) now also runs in `BaseAgent.from_config`, but stays off unless `adk web` turns it on. A `root_agent.yaml` that names such a callback stops loading on main.
- **`TeamsRunner`** (`c4376a657`, `google.adk.integrations.teams`, `pip install "google-adk[teams]"`, experimental): serves an ADK runner to Microsoft Teams, acknowledging at once and answering in the background.

Checked again at `70e1cbde7` (2026-11-04), also main only:

- **`on_model_error` runs on cancellation** (`36cb10ddc`): every plugin then agent `on_model_error` callback is called with the `asyncio.CancelledError` when a model call is cancelled (or a stream is closed after partial chunks), for cleanup only; a returned `LlmResponse` is ignored and the cancellation propagates. Through 2.13.0 the handler catches only `Exception` (`flows/llm_flows/core/_finalizer.py:405`), so a cancelled model call skips `on_model_error`; tool cancellation already runs `on_tool_error`. Release a lock or a span you opened in `before_model` from somewhere that runs on 2.13.0 too.
- **Eval-view SQL in template files** (`6b0171729`): the 2.13.0 views above, same rows, now under `plugins/bigquery_agent_analytics_sql/*_v1.sql`.
- **Classifier request labels** (`7bfe227f0`): calls made by the labs classifiers carry `google-adk-classifier/<version>+<kind>` (`llm`, `typesafe`, `openai`, `custom`); an outer eval label is kept, so judge and classifier spend stay separable in billing.
- **`MongoDbMemoryService(retrieval_mode="keyword" | "vector" | "auto", memory_ttl_days=None)`** (`1c84bb955`): keyword stays the default; `vector` embeds at ingest and recalls with `$vectorSearch` scoped to app and user; `memory_ttl_days` adds an expiry index.

Checked again at `175682204` (2026-11-05), also main only:

- **Tool retry guidance** (`03b4fbcee`): `ReflectAndRetryToolPlugin(..., reflection_guidance=..., retry_exceeded_guidance=...)`, keyword-only, each a string or a sync or async callable taking a `ToolReflectionContext` (with `default_guidance`). On 2.13.0 either keyword raises `TypeError: ... got an unexpected keyword argument 'reflection_guidance'`, and subclasses overriding the private reflection hooks break on main.
- **BigQuery eval views** (`10b716235`, `16898f30c`, `5e83dd5b2`): failed tool calls get `{"error": ...}` responses, human-in-the-loop and transfer requests stop counting as tool calls, a turn with a truncated payload is not eval-ready, protected columns cannot be denied, and `build_eval_session_query_sql` / `build_eval_turn_query_sql` are exported without the BigQuery extra. `eval_view_mode` (`"full"` makes TOOL_ERROR a required event) is main only too.
- **A2A graduates** (`95d4a5660`): no experimental warning on `RemoteA2aAgent` and friends ([workflow-graphs.md](workflow-graphs.md)).

### Tool-call integrity (2.11)

`ToolCallIntegrityPlugin(secret_key=b"...")` signs every function call with an HMAC as its event is emitted, re-verifies every stored call before each run, and lets a tool execute only when its call verifies. It exists for sessions kept in a store someone else could write to: a tampered call cannot be given a valid stamp. `secret_key` also takes a list for rotation (the first key signs, all verify); `allow_unstamped_calls=True` logs instead of raising for calls stored before the plugin was installed. Register it **first** in `plugins=[...]`. A failed check surfaces as a `RuntimeError` whose `__cause__` is `ToolCallIntegrityError`. Tools run as workflow nodes are not covered.

An eval case starts a fresh session, so the plugin stays out of the way; events restored via the create-session API or `adk run --resume` are never executed.

### Two other 2.11 additions, in one line each

- **`ModelConsultTool`** (`from google.adk.tools import ModelConsultTool`) lets the executing model escalate a hard step to an advisor model mid-run (default advisor `gemini-3.1-pro-preview`, a preview id the model policy warns about). The advisor's call is a tool call, so it shows in the trajectory and an eval case has to expect it.
- **`abort_signal`** on `Runner.run_async` takes an `asyncio.Event`; setting it cancels the invocation. Useful in a harness that enforces its own time budget.

### Model Armor

```python
from google.adk.integrations.model_armor import ModelArmorPlugin, ModelArmorConfig

app = App(name="app", root_agent=root_agent, plugins=[
    ModelArmorPlugin(config=ModelArmorConfig(
        prompt_template_name="projects/<p>/locations/<l>/templates/<t>",
        response_template_name="projects/<p>/locations/<l>/templates/<t>",
    )),
])
```

It screens the user's text against the prompt template in `before_model_callback`
and the model's output against the response template, and returns your
`input_blocked_message` / `output_blocked_message` instead when Model Armor finds
a match. `block_on_screening_failure` defaults to `True`, so a screening call that
errors blocks rather than passes the text through. `ModelArmorPlugin`'s arguments
are keyword-only.

**2.12 screens tool output too** (commit `fdda17c`). Set
`tool_output_template_name` on `ModelArmorConfig` and the plugin's
`after_tool_callback` screens each tool result's text (bytes and media parts are
skipped; long results are screened in overlapping 64k-character chunks) and
replaces a flagged result with `{"error": tool_output_blocked_message}`. That is
the guard for indirect prompt injection arriving through a search or fetch tool.
On 2.9–2.11 the field does not exist (and the config needs a prompt or response
template); 2.12 also accepts a config with only the tool-output template. An eval
for it is a trap-mock case: a tool fixture whose result carries the injected
text, and a rubric that the agent did not act on it (`/adk:eval-create`
`trap-mocks.md`).

**It needs an extra.** On a plain `google-adk[eval]` install the import raises
`ImportError: Model Armor support requires google-cloud-modelarmor. Install it
with: pip install 'google-adk[gcp]'.` (shipped since 2.9.2).

## Where a guardrail belongs

| Concern | Put it |
|---|---|
| this agent only | a callback on the agent |
| every agent in the app | a plugin on the `App` |
| inside one tool's logic | in the tool, returning a structured refusal |
| model-level harm | `before_model_callback` or a plugin, plus `safety_v1` in the suite |

Evaluation and guardrails are not alternatives. A guardrail stops the bad thing happening; the eval proves the guardrail works and catches what it misses.

**Tier the actions, not the prompt.** Google's IAM Helper agent skills (packaged
on the open Agent Skills standard, usable from ADK) draw the line by what a call
can change: read-only discovery and simulations run unattended, while any change
to an access policy pauses for a person, with a configurable risk tier deciding
when. A change is simulated against past access before it applies, broad roles are
blocked outright, and elevation is granted just in time, bounded in time and
revoked when the task ends instead of held as a standing privilege. In ADK: leave
read-only tools unconfirmed, give state-changing ones `require_confirmation=True`
or a callable that inspects the arguments and asks only above a threshold (use
`tool_context.request_confirmation(hint=..., payload=...)` when the person must
see or edit a payload), and refuse the never-allowed cases in a
`before_tool_callback` or plugin. A DevFest workshop recap puts the reason in one
line: an instruction steers the model but is "not an execution boundary". So a
constraint that matters lives in a callback, a plugin or a confirmation gate, not
only in the instruction, and the suite holds a case for the approve path and one
for the reject path.

**Policies are contracts.** An instruction ("never approve above the limit") is a soft constraint that an injected prompt, a prompt rewrite or a model upgrade can undo. A rule that must hold lives outside the model: a `before_tool_callback` or plugin that checks tool arguments against fixed bounds, a deterministic gateway for secrets and jailbreak phrasing, Model Armor, a sandboxed code executor for generated code. Test the enforcement directly — a blocked token, a hijack prompt, an out-of-bounds update, an allowed one — and the agent end to end with the policy-contract behaviour from `gen_test_eval.py --behavioral` (`/adk:eval-create` `behavioral-tests.md`).

**Governed change.** When a pipeline can change the agent itself (generated tools, rewritten instructions, a new sub-agent), the idea moves up a level. Google's reference architecture for self-evolving agents chains gates: screen inputs, sandbox generated code, sign artefacts and refuse unsigned ones, enforce policy as code, require human approval for structural changes, keep a tamper-evident log, and replay candidates offline and in a shadow environment before production. Here: a structural change is a new baseline, gated by `/adk:eval-ci` and rolled out per its `shadow-rollout.md`.

## Callback discipline

- One purpose per callback. They run on every invocation, so cost shows up.
- Handle your own errors: an exception in a callback takes the run with it.
- Make anything with an external side effect idempotent; retries and replays
  happen.
- Never catch `BaseException` — it traps the interrupt that drives
  human-in-the-loop.
- Test them with a mock context rather than a live run.

## Workflows

Orchestration in 2.x is `google.adk.workflow.Workflow`, a node graph that supersedes `SequentialAgent`/`ParallelAgent`/`LoopAgent`. Its two raising rules (one output per node execution, one terminal output per workflow), node confirmation (2.12) and remote A2A steps are in [workflow-graphs.md](workflow-graphs.md).

## Skills and context

`SkillToolset` (progressive disclosure, lifecycles, and 2.12's `save_output_artifacts=True`, which saves files a skill script writes as artifacts) is in [skills-and-progressive-disclosure.md](skills-and-progressive-disclosure.md). Event compaction (`App(events_compaction_config=...)`), `include_contents`, and keeping large payloads out of history are in [context-engineering.md](context-engineering.md).

---

Sources: `adk-python: apps/app.py`, `agents/llm_agent.py`, `plugins/__init__.py`
(`__all__` and the lazy `__getattr__`), `plugins/`,
`plugins/_tool_call_integrity_plugin.py` (2.11.0, commit `b7efc4e`),
`tools/model_consult/_model_consult_tool.py` (`DEFAULT_ADVISOR_MODEL`),
`runners.py` (`abort_signal`), the three read on the installed 2.11.0 wheel and
confirmed absent from 2.9.2,
`integrations/model_armor/` (`_plugin.py`, `_config.py`, `__init__.py`'s
`__all__` and its import error), `integrations/model_armor/_config.py` (`tool_output_template_name`, commit
`fdda17c`, in `v2.12.0`; both read on the installed 2.12.0 wheel and absent from
2.11.0); `adk-docs: docs/apps/index.md`, `docs/callbacks/*`,
`docs/plugins/index.md`, `docs/2.0/index.md`, `docs/safety/index.md`. 2.13: `features/_feature_registry.py` (`ea298d4`),
`utils/feature_decorator.py`, `tools/model_consult/_force_escalation_plugin.py`
(`a84d978`), `agents/base_agent.py` (`6031bb1`), the `[otel]` extra in the 2.13.0
wheel's `METADATA`. Main after 2.13.0 (`dfd534a88`): `c54b253eb`, `4496bef70`,
`2084efbe1` (`agents/_callback_metadata.py`), `cec52fc00`. Governed change:
Nawalgaria, "Architecting Governed, Self-Evolving AI Agents on Google Cloud",
Google Developers Blog, 2026-10-22,
https://developers.googleblog.com/architecting-governed-self-evolving-ai-agents-on-gemini-agent-platform/.
Policies as contracts: Saboo and Dong, "Build zero-trust AI
agents with Google's Agent Development Kit", Google Developers Blog, 2026-08-17,
https://developers.googleblog.com/build-zero-trust-ai-agents-with-googles-agent-development-kit/.
Tiered actions: Deekshit and Balla, "Scale agents securely with IAM Helper Agent
Skills and MCP Tools", Google Cloud Blog, 2026-10-28,
https://cloud.google.com/blog/products/identity-security/scale-agents-securely-with-iam-helper-agent-skills-and-mcp-tools;
Christina Lin, "The DevFest Community Workshop Experience: Building Real Agents
Together", Google Cloud Blog, 2026-09-19,
https://cloud.google.com/blog/topics/developers-practitioners/the-devfest-community-workshop-experience-building-real-agents-together
(paraphrased; one phrase quoted). `require_confirmation` (bool or callable) and
`request_confirmation(hint=, payload=)` read in the 2.13.0 wheel. Main at
`ee8720162`: `apps/compaction.py` (`87d9dcdf5`), `integrations/fsspec/`
(`6bf806a01`), `agents/config_agent_utils.py` and `agents/base_agent.py`
(`1c7e8f76b`), `integrations/teams/` (`c4376a657`), all post-tag. Eval views:
`plugins/bigquery_agent_analytics_plugin.py` on 2.13.0 (`create_eval_views`,
`_validate_eval_view_config`), `adk-docs: docs/integrations/bigquery-agent-analytics.md`
("Agent evaluation views"). Main at `70e1cbde7`: `flows/llm_flows/core/_finalizer.py`
and `utils/_callback_pipeline.py` (`36cb10ddc`), `labs/classifiers/` (`7bfe227f0`),
`integrations/mongodb/_memory_service.py` (`1c84bb955`), all post-tag. Main at `175682204`: `plugins/reflect_retry_tool_plugin.py` (`03b4fbcee`; the `TypeError` measured on 2.9.2 and 2.13.0), `plugins/bigquery_agent_analytics_plugin.py` and `plugins/_bigquery_agent_analytics_sql_utils.py` (`10b716235`, `16898f30c`, `5e83dd5b2`; `eval_view_mode` is `5762ef81f`), all post-tag.
