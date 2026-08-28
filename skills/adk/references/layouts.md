# Layouts: what ADK accepts, and where eval files belong

## The four shapes the loader accepts

`adk web`, `adk run` and `adk eval` all resolve an agent the same way. Under an
*agents directory*, any of these is loadable, and `{name}` becomes the
`app_name` the CLI uses:

```
{agents_dir}/{name}/agent.py          module {name}.agent
{agents_dir}/{name}.py                module {name}
{agents_dir}/{name}/__init__.py       package {name}
{agents_dir}/{name}/root_agent.yaml   YAML config agent
```

Inside the module, `AgentLoader` — which is what `adk web`, `adk run` and the
dev UI use — looks for exactly two names, in order:

1. **`app`** — an `App` instance. Preferred: an `App` is how plugins, context
   caching, event compaction and resumability reach a run, including an eval run.
2. **`root_agent`** — a `BaseAgent` or a `BaseNode`, so a bare `Workflow` counts.

**`get_agent_async` is not one of them.** The async-factory fallback exists only
in `cli/cli_eval.py` and `AgentEvaluator`, so a module that exports nothing but
`get_agent_async` evaluates fine and then does not load in `adk web`, `adk run`
or the dev UI's agent list. If you use the factory, also export `app` or
`root_agent` — or accept that the agent is evaluable but not runnable.

`adk eval` takes the **agent directory**, not a file:

```bash
adk eval path/to/my_agent path/to/my_agent/cases.test.json
```

`.env` is read from beside the agent package, so credentials are per-agent.

## The two project conventions

### Recipes layout (what this plugin scaffolds by default)

```
my_project/
  app/
    __init__.py            from .agent import app, root_agent
    agent.py               root_agent = ...;  app = App(name="app", root_agent=root_agent)
    tools.py  schemas.py
  tests/
    eval/
      eval_config.json
      evalsets/happy.evalset.json
      test_eval.py         pytest wrapper around AgentEvaluator
  .env  .env.example  pyproject.toml
```

Run it with `adk eval my_project/app <evalset> --config_file_path my_project/tests/eval/eval_config.json`.

**The config must be passed explicitly here.** `adk eval` only auto-discovers a
config when it is named `test_config.json` *and* sits next to the single evalset
file you passed. A config under `tests/eval/` is never found by itself, so a
command that omits `--config_file_path` silently falls back to the default
criteria — `tool_trajectory_avg_score 1.0` and `response_match_score 0.8` — and
the wording metric starts failing cases you never meant to score.

### Flat layout (what `adk create` produces)

```
my_agent/
  __init__.py              from . import agent
  agent.py                 root_agent = Agent(...)
  cases.test.json
  test_config.json
  .env
```

Run it with `adk eval . my_agent/cases.test.json` from the parent directory; the
config beside the evalset is picked up automatically.

## Where eval files must not go

`tests/*.json` and `tests/**/spec.yaml` are already claimed:

- **`adk test`** collects `tests/*.json` as recorded-interaction replays. An
  evalset dropped directly into `tests/` is collected, found to have no `events`
  key, and **skipped** — `pytest.skip`, not a failure. That is the worse
  outcome: the run is green and the file never executed, so nobody notices for
  months.
- **`adk conformance test`** collects `tests/**/spec.yaml`.

So: evalsets in `tests/eval/evalsets/`, conformance specs in
`tests/conformance/<case>/spec.yaml`, and nothing loose in `tests/`.

## File naming

| Pattern | What it is |
|---|---|
| `*.evalset.json` | an eval set, usually multi-turn; what the dev UI writes |
| `*.test.json` | an eval set too, and the only pattern `AgentEvaluator.evaluate(<dir>)` scans when given a directory |
| `test_config.json` | criteria, auto-discovered beside a single evalset |
| `eval_config.json` | criteria, must be passed with `--config_file_path` |

`eval_set_id` must match `^[a-zA-Z0-9_]+$` when ADK writes the file — no dashes,
no dots. Use the filename stem.

## The `app_name` rule

`session_input.app_name` in each eval case names the session the eval runs in.
The eval runner takes its own `app_name` from that session, so a value that
differs from the agent's directory **does not break the run**. What it does
affect:

- `app:`-prefixed state, memory and artifacts are scoped by that name, so a
  mismatch means the run reads and writes a different scope than production;
- ADK compares the runner's `app_name` with the directory the agent was loaded
  from and appends a mismatch hint to error messages, which makes debugging noisier.

With no `session_input` at all, the case runs as app `EvaluationGenerator` and
user `test_user_id`.

Recommended: set `session_input.app_name` to the agent directory name, which is
also what the results file is named after. When the module exports
`App(name=...)`, keep the two the same and the question disappears.

## YAML config agents

`root_agent.yaml` is still loadable and still documented as experimental, but the
config classes behind it are marked deprecated in the library. Treat an existing
one as something to migrate to code; do not start a new agent that way.

---

Sources: `adk-python: cli/utils/agent_loader.py` (`app` then `root_agent` only),
`cli/cli_eval.py` (the `get_agent_async` fallback),
`cli/cli_tools_click.py` (eval command and `_resolve_eval_config_file_path`),
`cli/agent_test_runner.py`, `evaluation/evaluation_generator.py`, `runners.py`,
`evaluation/local_eval_sets_manager.py`, `apps/app.py`;
`adk-docs: docs/evaluate/index.md`, `docs/agents/config.md`.
