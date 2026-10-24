# State, memory and what actually persists

## Four scopes

| Prefix | Scope | Persists |
|---|---|---|
| *(none)* | this session | only with a persistent session service |
| `user:` | this user, across sessions of the same app | yes |
| `app:` | the whole app, all users | yes |
| `temp:` | this invocation only, shared with sub-agents | never |

`app:` and `user:` are keyed on the app name, which is why an eval case whose
`session_input.app_name` differs from production reads and writes a different
scope.

## Write through the framework

```python
# right
tool_context.state["last_claim_id"] = claim_id     # inside a tool
ctx.state["last_claim_id"] = claim_id              # inside a callback or node

# wrong
session.state["last_claim_id"] = claim_id          # on a session from a service
```

The direct write is not recorded as an event, so it is not persisted, not part of
the history, and not replayed. It works in a demo and disappears in production —
which is why the review rules flag it as an error rather than a style note.

## State in eval cases

Two hooks make state testable:

- `session_input.state` pre-loads state, so a case can start from a condition
  instead of talking the agent into one.
- `final_session_state` *looks* like it asserts what the state should be at the
  end. It does not: the field is declared on `EvalCase` and no evaluator through
  ADK 2.13 reads it, so a case that relies on it asserts nothing. Do not use it as a
  gate.

So pre-loading state turns "does the agent remember?" into a deterministic
setup, which is far more stable than relying on the model's attention. Asserting
the result needs something the run can observe: a later tool call whose arguments
could only have come from the stored value, or a rubric over the transcript. If a
constraint matters, write it into state when it is first stated, read it from
there, and let the tool call that uses it be the assertion.

## Long-running agents: the step lives in state, not in the chat

An agent that pauses for days — waiting for a receipt, a manager's approval —
cannot rely on re-reading its history to know where it is: the history grows,
costs tokens on every resume, and after an idle gap the model may "remember" a
step that never happened. Keep the workflow's position as **explicit state**:

```python
class ClaimStep:                      # plain strings survive every session service
    START = "start"
    RECEIPT_REQUESTED = "receipt_requested"
    RECEIPT_VALIDATED = "receipt_validated"
    SUBMITTED = "submitted"

def init_claim_state(callback_context):            # before_agent_callback
    callback_context.state.setdefault("claim_step", ClaimStep.START)
    callback_context.state.setdefault("claim", {})
    callback_context.state.setdefault("pending_signals", [])

def request_receipt(claim_id: str, amount_usd: float, tool_context) -> dict:
    tool_context.state["claim"] = {"claim_id": claim_id, "amount_usd": amount_usd}
    tool_context.state["claim_step"] = ClaimStep.RECEIPT_REQUESTED
    tool_context.state["pending_signals"] = ["receipt_uploaded"]
    return {"status": "waiting", "claim_id": claim_id}

claims_agent = LlmAgent(
    name="claims_agent",
    instruction=("Current step: {claim_step}. Claim: {claim}. "
                 "Waiting for: {pending_signals}. Never skip a step whose signal "
                 "is still pending. Reviewer note: {reviewer_note?}"),
    tools=[request_receipt, submit_claim],
    before_agent_callback=init_claim_state,
)
```

- **Placeholders are filled from state on every turn.** `{claim_step}` raises
  `KeyError: Context variable not found` when the key is absent; `{key?}` renders
  an empty string instead. Placeholders work in a string `instruction`, not in
  one built by a function. `/adk:agent-review` flags a required placeholder that
  nothing writes or seeds (`AGT080`).
- **Initialise in `before_agent_callback`.** Writes through `callback_context.state`
  become the event's `state_delta`, so the first turn never hits a missing key.
- **Tools are the checkpoints.** Each tool that completes a step writes the next
  step through `tool_context.state`; the event that records the call records the
  state change with it.
- **Resume from outside with a `state_delta`.** A webhook handler (the receipt was
  uploaded) calls `runner.run_async(user_id=..., session_id=..., new_message=...,
  state_delta={"claim_step": "receipt_validated", "pending_signals": []})`; the
  delta is applied before the model sees the turn.
- **Persist the session.** `DatabaseSessionService("sqlite+aiosqlite:///sessions.db")`
  locally, a managed database or the Vertex AI session service in production;
  with `InMemorySessionService` the step dies with the process.
- **Seed it by hand.** On 2.13+ `adk web` seeds every explicitly created session
  from `<agents_dir>/<app>/initial_state.json` (the caller's keys win);
  `adk run --state_file` (2.12+) does the same in the terminal.

Testing it needs no waiting: seed `session_input.state` at the gate or at a later
step and assert what the agent does next — `/adk:eval-create`
`case-taxonomy.md`, "Simulating time with state".

## Memory versus state

State is the current conversation's data. **Memory** is recall across sessions,
reached through `load_memory` / `preload_memory` tools, `ctx.search_memory()`, or
`add_session_to_memory()` in a callback. Services: `InMemoryMemoryService` for
development, `VertexAiMemoryBankService` or `VertexAiRagMemoryService` for
production.

Evaluating memory needs a persistent service and a stable `user_id` across the
cases that share it, or nothing is there to recall.

## Artifacts

`save_artifact` / `load_artifact`, versioned per session, scoped by app name like
everything else. `load_artifacts` brings them into context.

## Context growth

Long conversations grow the context until quality degrades. `App` takes an
`EventsCompactionConfig` (compaction interval, token threshold, retention and
overlap) and a `ContextCacheConfig`. Both only apply when an `App` is exported —
another reason to export one. The config's bounds and pairing rules, filtering
instead of summarising, `include_contents`, and keeping large payloads in
artifacts rather than history: [context-engineering.md](context-engineering.md).

---

Sources: `adk-python: apps/_configs.py`, `sessions/`, `memory/`,
`sessions/state.py` (`setdefault`), `sessions/database_session_service.py`,
`runners.py` (`run_async(..., state_delta=...)`),
`flows/llm_flows/prompt/_instructions_utils.py` (the `{key}` / `{key?}` rule and
the `KeyError`), `cli/dev_server.py` (`initial_state.json`, `3c8c63e`, in
`v2.13.0`), `tools/tool_context.py`, `evaluation/eval_case.py`, at `12c7bed76`;
`adk-docs: docs/sessions/state.md`, `docs/sessions/memory.md`,
`docs/artifacts/index.md`. The compiled-view framing behind the context section
is from Hangfei Lin, "Architecting efficient context-aware multi-agent framework
for production", Google Developers Blog, 2025-12-04,
https://developers.googleblog.com/architecting-efficient-context-aware-multi-agent-framework-for-production/.
The long-running pattern — the step machine in state, placeholders, tools as
checkpoints, callback initialisation, webhook resume with a `state_delta`,
`DatabaseSessionService` — is paraphrased from Shubham Saboo and Eric Dong,
"Build Long-running AI agents that pause, resume, and never lose context with
ADK", Google Developers Blog, 2026-05-12,
https://developers.googleblog.com/build-long-running-ai-agents-that-pause-resume-and-never-lose-context-with-adk/
(its onboarding example retold as an expense claim).
