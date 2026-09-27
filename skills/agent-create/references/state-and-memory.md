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
  ADK 2.10 reads it, so a case that relies on it asserts nothing. Do not use it as a
  gate.

So pre-loading state turns "does the agent remember?" into a deterministic
setup, which is far more stable than relying on the model's attention. Asserting
the result needs something the run can observe: a later tool call whose arguments
could only have come from the stored value, or a rubric over the transcript. If a
constraint matters, write it into state when it is first stated, read it from
there, and let the tool call that uses it be the assertion.

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
another reason to export one.

---

Sources: `adk-python: apps/_configs.py`, `sessions/`, `memory/`,
`tools/tool_context.py`, `evaluation/eval_case.py`;
`adk-docs: docs/sessions/state.md`, `docs/sessions/memory.md`,
`docs/artifacts/index.md`.
