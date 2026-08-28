# When the agent will not load

The static review reads the source; the load check imports it the same way
`AgentLoader` does: try `app` (an `App`), then `root_agent`. That is the whole
list for `adk web`, `adk run` and the dev UI. `get_agent_async` is an extra
fallback accepted only by `adk eval` and `AgentEvaluator`, so a module exporting
nothing else fails this check and still evaluates — which is worth saying out
loud rather than reporting as broken.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/load_agent.py <agent-dir>
```

It runs through an interpreter that has ADK — which is usually not the one
running the script — and imports from the agent's **parent** directory, because
that is what ADK treats as the agents directory.

## Reading the failure

| Error | Usually |
|---|---|
| `ModuleNotFoundError` | a missing dependency in that interpreter, or the package is not importable from the parent directory |
| `ImportError` | a symbol that moved in ADK 2.x; check the migration reference |
| `ValidationError` | a field rejected at construction: a reserved name, a sampling kwarg passed to the agent, or `generate_content_config` carrying tools, system_instruction or response_schema |
| `AttributeError` | the module imports but exposes nothing ADK recognises |
| `TypeError` | a constructor argument that no longer exists |

## "adk web does not list my agent"

Checked in this order:

1. Is the directory one of the four shapes the loader accepts — `agent.py`, a
   loadable `__init__.py`, `<name>.py`, or `root_agent.yaml`?
2. Does it export `app` or `root_agent`? A package that re-exports
   (`from .agent import app`) counts. `get_agent_async` alone satisfies
   `adk eval` but not the loader.
3. Is `adk web` pointed at the **parent** of the agent directory? Pointing it at
   the agent itself runs that one agent instead of listing a collection.
4. Does the module import at all? A module-level exception makes the agent vanish
   from the list with no other symptom.

The load check answers 2 and 4 directly.

## Import side effects

A module that does real work at import time — opening a connection, reading a
file, calling an API — makes the agent fragile in ways that only appear at load.
Build those inside a factory or a callback, not at module scope.

This is also why the static review never imports: a review has to work on a
project whose imports are the problem.

## After a successful load

The check reports the export it used, the app name and plugins, the agent name
and type, the resolved model, the tools and any sub-agents. Compare that with
what the static review read from the source: a difference means something is
being built dynamically, which is worth knowing before you write eval cases
against it.

---

Sources: `adk-python: cli/utils/agent_loader.py`, `cli/cli_eval.py`,
`apps/app.py`, `agents/llm_agent.py`.
