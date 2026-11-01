# Context engineering: what the model sees, and why less is better

Every model call in an ADK agent gets a request **built** from stored state:
the session's events, state, memory, artifacts, the instruction, the tool
declarations. Google's post on ADK's multi-agent context design calls that
request "a compiled view" over the storage underneath. The phrase is useful
because it says what to engineer: not the storage, which can be large and
complete, but the view, which should hold only what this step needs.

## Three principles

1. **Separate storage from presentation.** The session keeps every event; the
   request does not have to. Decide what each agent sees per call, and keep the
   full record for audit and evaluation.
2. **Build the view with explicit, ordered steps.** ADK's request processors run
   in a fixed, documented order (in 2.12.0's single-agent flow: basic settings,
   auth, confirmation, instructions, identity, compaction, contents, cache,
   planning, code execution, output schema); tools add their declarations and
   then callbacks and plugins see the result. Knowing the order is what makes
   caching and filtering predictable.
3. **Scope by default.** A sub-agent or a tool gets the minimum context for its
   job. Widening it is a decision, made per agent, with a reason.

The anti-patterns are the opposites: shovelling the raw history into every
call, putting a multi-megabyte payload into chat history, and handing a
sub-agent its whole ancestry when it needed one question.

## Compaction: a sliding window plus a summary

`App(events_compaction_config=EventsCompactionConfig(...))` replaces older
invocations with an LLM-written summary once a trigger fires. Import it from
`google.adk.apps.app` (it is not re-exported by `google.adk.apps`); through
2.13.0 the class is marked experimental and warns (main after 2.13.0 graduates
it, `c54b253eb`).

```python
from google.adk.apps.app import App, EventsCompactionConfig

app = App(name="app", root_agent=root_agent,
          events_compaction_config=EventsCompactionConfig(
              compaction_interval=5, overlap_size=1))
```

| Field | Bound | Meaning |
|---|---|---|
| `compaction_interval` | `> 0` | compact after this many **new** user-initiated invocations |
| `overlap_size` | `>= 0` | how many preceding invocations each summary re-covers, so consecutive summaries overlap |
| `token_threshold` | `> 0` | also compact after an invocation whose prompt reached this many tokens |
| `event_retention_size` | `>= 0` | with the token trigger, keep this many recent raw events uncompacted |
| `summarizer` | | a `BaseEventsSummarizer`; default `LlmEventSummarizer` on the root agent's model |

All default to `None`, but the config validates as **pairs**, measured on 2.9.2
and 2.12.0: `compaction_interval` and `overlap_size` must be set together,
`token_threshold` and `event_retention_size` must be set together, and at least
one pair is required. `EventsCompactionConfig()` raises "At least one compaction
trigger must be configured"; `compaction_interval=5` alone raises "must be set
together". Unknown keys raise (`extra="forbid"`), and so does
`compaction_interval=0`.

Compaction is not free: the summary is a model call, and a summary can drop
the one constraint turn 2 stated. Test it with a case long enough to trigger it
that needs a fact from before the window (`/adk:eval-create`
`memory-tests.md`): the case fails if the summary lost it.

**Filtering instead of summarising.** `ContextFilterPlugin(num_invocations_to_keep=N)`
(`google.adk.plugins.context_filter_plugin`) trims the request to the last N
invocations, or applies your `custom_filter`; the session is untouched. Cheaper
than a summary, and lossy in a way you can predict.

## `include_contents`: what a transferred-to agent sees

`LlmAgent(include_contents=...)` takes `'default'` or `'none'`:

- `'default'`: the agent receives the relevant conversation history of the
  session (its branch, other agents' messages presented as context).
- `'none'`: only the current turn: the latest user input (or the message that
  handed over control) and the tool calls and responses of this turn. Prior
  turns are left out.

It matters for agents reached by **transfer** (`sub_agents` and
`transfer_to_agent`), which share the caller's session. A specialist that answers
a self-contained question should usually be `'none'`; one that continues the
user's conversation needs `'default'`.

## Agents as tools versus transfer

| | `AgentTool(agent)` | `sub_agents=[agent]` + transfer |
|---|---|---|
| Who keeps control | the caller; the result comes back as a tool response | the sub-agent takes over the conversation |
| What the sub-agent sees | a **fresh in-memory session** holding only the tool's `request` (or its input-schema JSON) and a copy of the caller's state (minus `_adk` keys); none of the caller's history | the shared session, filtered by its `include_contents` |
| Artifacts | forwarded to the caller's artifact service | shared |
| Memory | a **fresh, empty** in-memory store through 2.13.0 (`AGT082`); the caller's memory service on main after 2.13.0 | the app's |
| Plugins | the caller's, unless `include_plugins=False` | the app's |
| Eval shape | one tool call in the caller's trajectory | a `transfer_to_agent` call, then the sub-agent's calls |

So an agent-as-tool is already scoped: `include_contents` changes nothing for
its first request, because the fresh session has no prior turns. The scoping
question for an `AgentTool` is what goes into `request`: write the tool's
description so the caller passes a complete, self-contained question.

**Memory inside an `AgentTool`.** Through 2.13.0 the nested runner gets its own
`InMemoryMemoryService()` (`tools/agent_tool.py:275` on 2.13.0, `:269` on 2.9.2),
whatever memory service the App or Runner was given, so a `load_memory` or
`preload_memory` bound to the wrapped agent searches an empty store and finds
nothing, with no error (measured). Call the memory tool from the caller and put
what it found into `request`, or reach the specialist by transfer.
**Coming after 2.13.0 (unreleased main, `4e0ac64e3`):** the sub-agent shares the
caller's memory service under the same app and user, so the pattern works once a
release includes it. `lint_agent.py` warns on an explicit `AgentTool(agent=...)`
whose agent, or an agent below it, binds a memory tool (`AGT082`; info on a newer
runner).
Also main only (`73cfd62ed`): `AgentTool` closes the child runner in a `finally`
block. Through 2.13.0 a sub-agent run that raised or was cancelled left that
runner and its toolsets open (an MCP connection, for one), so a long eval batch
with many failing delegations can pile up open connections.

**Heavy generation in its own sub-agent.** Google's A2UI and CopilotKit
walkthrough (an ADK `LlmAgent` served to a web client through `ag_ui_adk`) makes
the same scoping argument for generated interfaces: the component catalog and
the protocol rules live in a dedicated UI sub-agent, so the main agent's context
never carries them. Two more of its habits travel to any structured output: check
what the user typed on the client before it costs a model turn, and when a
generation fails validation, send the specific error back for a correction, then,
once a small retry budget is spent, answer with a short message so "nothing broken
reaches the screen" and the conversation goes on. In ADK that is an `AgentTool`
or workflow node with its own instruction and `output_schema`, a validating
callback or node, and a retry cap; the fallback message is something an eval case
can assert.

## Large payloads belong in artifacts

A tool that returns a 5 MB document puts it into the history, where every later
call pays for it again. Save it instead and return a reference:

- `await tool_context.save_artifact(filename, types.Part(...))` in the tool,
  returning `{"artifact": filename, "summary": "..."}`;
- `load_artifacts` (`google.adk.tools.load_artifacts_tool`) lets the model pull
  one in when a step needs it;
- `SaveFilesAsArtifactsPlugin` saves files embedded in user messages as
  artifacts, so an upload is not re-sent on every call;
- a skill script's output files become artifacts with
  `SkillToolset(save_output_artifacts=True)` (2.12;
  [skills-and-progressive-disclosure.md](skills-and-progressive-disclosure.md)).

Long-term facts belong in **memory**, reached on demand with `load_memory` or
`preload_memory`, not repeated in the instruction ([state-and-memory.md](state-and-memory.md)).

## Order for the cache

Put what never changes first: the static procedure in `static_instruction`, the
tool declarations, then the per-turn instruction and the history. A context
cache can only reuse an unchanged prefix, so anything per-request near the top
(a timestamp, the user's name) defeats it. The rules, the size floor and how to
measure the hit ratio: [context-caching.md](context-caching.md).

## What the review checks

| Id | Severity | Finding |
|---|---|---|
| `AGT076` | **error** | `EventsCompactionConfig` with a broken bound (`compaction_interval` > 0, `overlap_size` ≥ 0, `token_threshold` > 0, `event_retention_size` ≥ 0), an unknown field, a trigger pair half set, or no trigger pair at all; building the App raises `ValidationError`; validators identical on 2.9.2–2.12; import from `google.adk.apps.app` |
| `AGT077` | info | no `App` sets `events_compaction_config` while some eval case has 6+ invocations or a `conversation_scenario` (discovery and lint); long sessions resend the whole history; consider compaction, and keep the long case as its test |
| `AGT078` | info | a sub-agent of an `LlmAgent` parent (a transfer target) keeps `include_contents='default'`. Children of `SequentialAgent` / `ParallelAgent` / `LoopAgent` are skipped; an `AgentTool` runs in a fresh session and is out of scope; each of its requests carries the whole conversation; set `'none'` if it only needs the handed-over turn, or call it as an `AgentTool` |
| `AGT079` | warn | a bound tool returns a string over 2,000 characters (a literal, `"..." * N`, a dict value, or a module constant); it lands in the history and is resent on every later call; save it as an artifact and return a reference |
| `AGT082` | warn | an `AgentTool(agent=x)` whose `x` (or an agent below it) binds a memory tool; through 2.13.0 it searches a fresh, empty store; info on a runner newer than 2.13.0 |

## Proving it

Context work is measurable with the efficiency metrics ADK 2.10+ reports on
every run. Take a baseline, change one thing (compaction on, a sub-agent to
`'none'`, a payload to an artifact), and compare with `compare_runs.py`: prompt
tokens per invocation should fall and the pass rate must not
(`/adk:eval-ci` `merge-gates.md` for the budget flags).

---

Sources: Hangfei Lin, "Architecting efficient context-aware multi-agent
framework for production", Google Developers Blog, 2025-12-04,
https://developers.googleblog.com/architecting-efficient-context-aware-multi-agent-framework-for-production/
(the compiled view, the three principles, the anti-patterns; paraphrased).
Google A2UI team with Atai Barkai (CopilotKit), "Build Generative UI Apps with
A2UI & CopilotKit Agents", Google Developers Blog, 2026-10-29,
https://developers.googleblog.com/build-generative-ui-apps-with-a2ui-copilotkit-agents/
(the UI sub-agent, client-side validation, retry then fallback; paraphrased, one
phrase quoted). AgentTool memory: `tools/agent_tool.py` on 2.9.2, 2.13.0 and main
`ee8720162` (`4e0ac64e3`, post-tag), the 2.13.0 sub-runner captured at runtime;
the child-runner close at main `175682204` (`73cfd62ed`, post-tag).
`adk-python: apps/_configs.py` (`EventsCompactionConfig`, its bounds and
pairing validator), `apps/compaction.py` (`_ensure_compaction_summarizer`),
`agents/llm_agent.py` (`include_contents`), `flows/llm_flows/context/_contents.py`
(the `'none'` branch), `tools/agent_tool.py` (the fresh session and state copy),
`plugins/context_filter_plugin.py`, `plugins/save_files_as_artifacts_plugin.py`,
`tools/load_artifacts_tool.py`, `flows/llm_flows/single_flow.py`
(`_create_request_processors`); bounds,
pairing and imports measured on the installed 2.9.2 and 2.12.0 wheels.
`adk-docs: docs/context/index.md`, `docs/agents/llm-agents.md`.
