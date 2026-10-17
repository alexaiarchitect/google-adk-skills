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

The same hooks, registered once on the `App` and applied everywhere, plus
run-level ones (`on_user_message_callback`, `before_run_callback`,
`on_event_callback`, `after_run_callback`, and the error hooks). All async,
keyword-only.

Built-ins worth knowing — **and where each one actually imports from**, because
only some are re-exported by the package:

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

`google.adk.plugins.__all__` is exactly `BasePlugin`, `PluginManager` and the
names in the first three rows, so `from google.adk.plugins import
GlobalInstructionPlugin` raises `ImportError` — the package's `__getattr__`
lazily resolves only the names it lists. The others need their submodule path.
The lazy list was four names on 2.9.2 and 2.10.0; 2.11.0 adds the integrity pair,
so code that imports them has to require `google-adk>=2.11`.

### Tool-call integrity (2.11)

`ToolCallIntegrityPlugin(secret_key=b"...")` signs every function call with an
HMAC as its event is emitted, re-verifies every stored call before each run, and
lets a tool execute only when its call verifies. It exists for sessions kept in a
store someone else could write to: a tampered call cannot be given a valid
stamp. `secret_key` also takes a list for rotation (the first key signs, all
verify); `allow_unstamped_calls=True` logs instead of raising for calls stored
before the plugin was installed. Register it **first** in `plugins=[...]`.
A failed check surfaces as a `RuntimeError` whose `__cause__` is
`ToolCallIntegrityError`. Tools run as workflow nodes are not covered.

An eval case starts a fresh session, so the plugin signs and verifies its own
calls and stays out of the way. Events restored through the create-session API
or `adk run --resume` are marked restored: not verified, and their calls are
never executed.

### Two other 2.11 additions, in one line each

- **`ModelConsultTool`** (`from google.adk.tools import ModelConsultTool`) lets
  the executing model escalate a hard step to an advisor model mid-run (default
  advisor `gemini-3.1-pro-preview`, a preview id the model policy warns about).
  The advisor's call is a tool call, so it shows in the trajectory and an eval
  case has to expect it.
- **`abort_signal`** on `Runner.run_async` takes an `asyncio.Event`; setting it
  cancels the invocation. Useful in a harness that enforces its own time budget.

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
with: pip install 'google-adk[gcp]'.` The plugin has shipped since 2.9.2 — what
changed in this upstream sync is only that ADK's docs now list it among the
built-ins.

## Where a guardrail belongs

| Concern | Put it |
|---|---|
| this agent only | a callback on the agent |
| every agent in the app | a plugin on the `App` |
| inside one tool's logic | in the tool, returning a structured refusal |
| model-level harm | `before_model_callback` or a plugin, plus `safety_v1` in the suite |

Evaluation and guardrails are not alternatives. A guardrail stops the bad thing
happening; the eval proves the guardrail works and catches what it misses.

## Callback discipline

- One purpose per callback. They run on every invocation, so cost shows up.
- Handle your own errors: an exception in a callback takes the run with it.
- Make anything with an external side effect idempotent; retries and replays
  happen.
- Never catch `BaseException` — it traps the interrupt that drives
  human-in-the-loop.
- Test them with a mock context rather than a live run.

## Workflows

For orchestration, ADK 2.x uses `google.adk.workflow.Workflow`: a graph of nodes
with explicit edges, routing, fan-out with `JoinNode` fan-in, retries and
human-in-the-loop pauses. `SequentialAgent`, `ParallelAgent` and `LoopAgent` are
superseded, and remain only for the case where an `LlmAgent` must be able to
transfer into the orchestrated block.

Two rules that bite when evaluating a graph, and both raise rather than degrade
quietly:

- **At most one output per node execution.** The second assignment to
  `ctx.output` raises `ValueError("Output already set. A node can produce at most
  one output.")`. Output and interrupt are mutually exclusive too.
- **At most one terminal output per workflow.** If several terminal nodes (nodes
  with no outgoing edge) produce output, finalization raises
  `WorkflowConfigurationError("Workflow <name>: multiple terminal nodes produced
  output (N). A workflow must have at most one terminal output.")`. Route to a
  single terminal node, or join first.

Plus: everything in `event.output` must be JSON-serializable.

**Confirmation on a function node (2.12).** `@node(require_confirmation=True)`
(or a predicate `callable(...) -> bool`), `node(...)`, `build_node(...)` and
`FunctionNode(require_confirmation=...)` pause the node for a yes/no before the
function runs, the same contract `FunctionTool(require_confirmation=...)` has:
approval runs it once; rejection skips it and emits `{"error": "This tool call
is rejected."}`. `@node` defaults `rerun_on_resume=True` when confirmation is
set; `FunctionNode` raises `WorkflowConfigurationError` if you pass
`rerun_on_resume=False` with it. On 2.9–2.11 the keyword does not exist. Eval
both branches: one case approves, one rejects and expects no side effect.

## Skills and context

`SkillToolset` (progressive disclosure, lifecycles, and 2.12's
`save_output_artifacts=True`, which saves files a skill script writes as
artifacts) is in [skills-and-progressive-disclosure.md](skills-and-progressive-disclosure.md).
Event compaction (`App(events_compaction_config=...)`), `include_contents`, and
keeping large payloads out of history are in
[context-engineering.md](context-engineering.md).

---

Sources: `adk-python: apps/app.py`, `agents/llm_agent.py`, `plugins/__init__.py`
(`__all__` and the lazy `__getattr__`), `plugins/`,
`plugins/_tool_call_integrity_plugin.py` (2.11.0, commit `b7efc4e`),
`tools/model_consult/_model_consult_tool.py` (`DEFAULT_ADVISOR_MODEL`),
`runners.py` (`abort_signal`), the three read on the installed 2.11.0 wheel and
confirmed absent from 2.9.2,
`integrations/model_armor/` (`_plugin.py`, `_config.py`, `__init__.py`'s
`__all__` and its import error), `agents/context.py`
(the output setter), `workflow/_workflow.py` (terminal-output finalization),
`workflow/_errors.py`, `workflow/_function_node.py` and `workflow/_node.py`
(`require_confirmation`, commit `988fd1a`, in `v2.12.0`),
`integrations/model_armor/_config.py` (`tool_output_template_name`, commit
`fdda17c`, in `v2.12.0`; both read on the installed 2.12.0 wheel and absent from
2.11.0); `adk-docs: docs/apps/index.md`, `docs/callbacks/*`,
`docs/plugins/index.md`, `docs/2.0/index.md`, `docs/graphs/index.md`,
`docs/safety/index.md`.
