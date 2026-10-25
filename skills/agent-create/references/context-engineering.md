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
| Plugins | the caller's, unless `include_plugins=False` | the app's |
| Eval shape | one tool call in the caller's trajectory | a `transfer_to_agent` call, then the sub-agent's calls |

So an agent-as-tool is already scoped: `include_contents` changes nothing for
its first request, because the fresh session has no prior turns. The scoping
question for an `AgentTool` is what goes into `request`: write the tool's
description so the caller passes a complete, self-contained question.

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
`adk-python: apps/_configs.py` (`EventsCompactionConfig`, its bounds and
pairing validator), `apps/compaction.py` (`_ensure_compaction_summarizer`),
`agents/llm_agent.py` (`include_contents`), `flows/llm_flows/context/_contents.py`
(the `'none'` branch), `tools/agent_tool.py` (the fresh session and state copy),
`plugins/context_filter_plugin.py`, `plugins/save_files_as_artifacts_plugin.py`,
`tools/load_artifacts_tool.py`, `flows/llm_flows/single_flow.py`
(`_create_request_processors`); bounds,
pairing and imports measured on the installed 2.9.2 and 2.12.0 wheels.
`adk-docs: docs/context/index.md`, `docs/agents/llm-agents.md`.
