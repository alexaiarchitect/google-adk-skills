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

**Two more files the CLI picks up by convention.** `services.yaml` (or
`services.yml`, entries of `scheme`, `type` — `session`, `artifact`, `memory`
or `task_store` — and `class`) and `services.py` (factories registered through
`google.adk.cli.service_registry.get_service_registry()`) register custom
services under a URI scheme, so `--session_service_uri mysession://...` reaches
your class; `adk run` looks in the agent directory, `adk web` and
`adk api_server` in the agents root, and the docs name no other command. And on
2.13+ `adk web` seeds every explicitly created session from
`<agents_dir>/<app>/initial_state.json`.

**Evals live in Python whatever ships.** ADK also exists for Go, Java,
TypeScript and Kotlin, but Python is the reference implementation and the one
with the evaluation tooling, so a project whose production service is in another
language still keeps its evalsets, configs and this plugin's gates beside a
Python build of the agent.

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

## The `adk-recipes` repository: three layers

`google/adk-recipes` (Google's sample repo, and the source of the recipes layout
above) is organised in three layers, and each layer evaluates differently:

| Layer | Holds | Evaluation |
|---|---|---|
| `core/` | reference patterns, one project per recipe (`core/python/<recipe>/`) | ADK evalsets under `tests/eval/evalsets/`, run with `adk eval` |
| `plugins/` | installable vertical skills: `plugins/<plugin>/plugin.json` plus `skills/<skill>/{SKILL.md, README.md, EVAL.yaml, scripts/, references/, assets/}` (Agent Plugins v1.0.0) | one `EVAL.yaml` per skill: judge rubrics plus deterministic assertions |
| `contrib/` | deployable services in several languages | runnability tests; evals vary by recipe |

Every recipe also ships a `manifest.yaml`, an `AGENTS.md`, a `README.md` and a
runnability test. Plugin skills are installed into a coding agent with
`npx skills add google/adk-recipes --skill <name>`; they are **not** ADK
`SkillToolset` skills. Their `SKILL.md` names carry a prefix the directory does
not (`retail-product-search` in `skills/product-search/`), so ADK's
`load_skill_from_dir` raises `Skill name ... does not match directory name` on
them (measured on 2.12.0).

**What this plugin does with each.** `discover.py` and `inventory.py` treat a
`core/` recipe as a recipes-layout project. A `plugins/*` skill layer is not an agent,
so discovery reports `ENV014` (info: an Agent-Plugins layer with `plugin.json`,
`skills/*/SKILL.md` and `EVAL.yaml`) instead of the `ENV011` "no agent" error,
and records the layer as the `plugin_layer` fact (skills and their `EVAL.yaml`
files); `inventory.py` lists it after the agents with cases, rubric lines and
assertion types per skill.
`/adk:eval-review`'s `validate_eval_yaml.py` checks an `EVAL.yaml`; nothing in
this plugin runs one, because the recipes repository defines no runner for them
(`/adk:eval-create` `managing-evalsets.md` has the shape).

**Horizon as the worked example.** `core/python/long-horizon-harness` is the
recipes' long-horizon harness and its most complete eval suite: eighteen
evalsets under `tests/eval/evalsets/` (78 cases at `05811bba`), each a behaviour
(clarify before acting, halt on a guardrail, recall memory, route to the right
delegate, resist exfiltration), graded by one metric,
`rubric_based_final_response_quality_v1` at 0.8 with per-case rubrics and no
trajectory grader. Its `AGENTS.md` keeps the line this plugin draws too: pytest
for code correctness, evalsets for model behaviour. It runs them with
`uv run adk eval tests/eval/horizon_eval tests/eval/evalsets/<name>.evalset.json
--config_file_path tests/eval/eval_config.json`, where `horizon_eval/` is a
two-line shim package because `horizon/__init__.py` deliberately does not import
`agent`. (Google's tour of the repository describes running the suite with
`uv run pytest tests/eval`; at `05811bba` there is no pytest module under
`tests/eval/`, so use the `adk eval` command.)

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

`root_agent.yaml` is still loadable. 2.13.0 promoted the `AGENT_CONFIG` feature
flag to stable, so its "feature is enabled" warning is gone — but the loader
(`AgentLoader._load_from_yaml_config`) still prints `[EXPERIMENTAL]`, the docs
still label Agent Config experimental, and the config classes behind it
(`AgentConfig`, `LlmAgentConfig`, ...) are marked `@deprecated` in the library.
`ADK_SUPPRESS_EXPERIMENTAL_FEATURE_WARNINGS=1` silences the loader warning. Treat
an existing one as something to migrate to code (`AGT050`); do not start a new
agent that way.

---

Sources: `adk-python: cli/utils/agent_loader.py` (`app` then `root_agent` only),
`cli/cli_eval.py` (the `get_agent_async` fallback),
`cli/cli_tools_click.py` (eval command and `_resolve_eval_config_file_path`),
`cli/agent_test_runner.py`, `evaluation/evaluation_generator.py`, `runners.py`,
`evaluation/local_eval_sets_manager.py`, `apps/app.py`;
`adk-docs: docs/evaluate/index.md`, `docs/agents/config.md`,
`docs/agents/service-registration.md` (new at `cc53b6c14`); `cli/dev_server.py`
(`initial_state.json`, `3c8c63e`, in `v2.13.0`); the YAML-loader warning and the
`AGENT_CONFIG` stage measured on 2.9.2, 2.12.0 and 2.13.0. Python as the home of
evaluation: Karl Weinmeister, "One Agent Development Kit, five languages", Google
Cloud blog, 2026-10-13,
https://cloud.google.com/blog/topics/developers-practitioners/one-agent-development-kit-five-languages.
The three layers:
Ippolito and Saadati, "Three layers, one repo: a tour of google/adk-recipes",
Google Developers Blog, 2026-10-15,
https://developers.googleblog.com/three-layers-one-repo-a-tour-of-googleadk-recipes/
(paraphrased); checked against `adk-recipes` `05811bba`
(`docs/recipe-handbook/plugins.md`, `plugins/retail/`,
`core/python/long-horizon-harness/{AGENTS.md,tests/eval/}`).
