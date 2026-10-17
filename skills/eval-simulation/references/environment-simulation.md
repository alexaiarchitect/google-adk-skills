# Environment simulation

## What it is

User simulation fakes the person. **Environment simulation fakes the tools**:
mocked responses, injected errors, injected latency, so you can test what the
agent does when a backend fails without arranging for a backend to fail.

It is configured on the **agent** side, not in the eval config, because it changes
what the tools return rather than how results are scored.

The whole feature is gated `@experimental(FeatureName.ENVIRONMENT_SIMULATION)`.
The shapes below were read on 2.9.2 and re-read on 2.10.0, 2.11.0 and 2.12.0 — identical, field for
field. Re-check them on an upgrade all the same.

## Where the classes live

The `google.adk.tools.environment_simulation` package exports **only**
`EnvironmentSimulationFactory`. Every config class is one level down:

```python
from google.adk.tools.environment_simulation import EnvironmentSimulationFactory
from google.adk.tools.environment_simulation.environment_simulation_config import (
    EnvironmentSimulationConfig,
    InjectedError,
    InjectionConfig,
    MockStrategy,
    ToolSimulationConfig,
)
```

Importing the config classes from the package raises `ImportError`.

## Config shape

```python
EnvironmentSimulationConfig(
    simulation_model=os.getenv("MODEL_NAME", "gemini-flash-latest"),
    tool_simulation_configs=[
        ToolSimulationConfig(
            tool_name="check_cost_centre_funds",
            mock_strategy_type=MockStrategy.MOCK_STRATEGY_UNSPECIFIED,
            injection_configs=[
                InjectionConfig(
                    injection_probability=0.0,          # the default is 1.0
                    injected_error=InjectedError(
                        injected_http_error_code=503,
                        error_message="the funds service is temporarily unavailable",
                    ),
                ),
                InjectionConfig(
                    injection_probability=0.0,
                    injected_latency_seconds=5.0,
                    injected_response={"status": "ok", "available_usd": 5000.0},
                ),
            ],
        ),
    ],
)
```

| Field | Where | Notes |
|---|---|---|
| `simulation_model` | config | analyses tools and generates mocks. ADK defaults it to `gemini-2.5-flash`, which this plugin's policy (from `adk-recipes: AGENTS.md`) treats as retiring — set it explicitly |
| `environment_data` | config | a JSON *string* of domain context (a database snapshot) fed to mock generation |
| `tracing` | config | a JSON *string* of a prior run, for mock generation with history |
| `tool_name` | tool | must match the registered tool name exactly; duplicates are rejected |
| `mock_strategy_type` | tool | what happens when no injection fires (see below) |
| `injection_probability` | injection | **defaults to 1.0**; `0.0` never fires |
| `match_args` | injection | fire only when the call's arguments contain all these pairs |
| `injected_error` | injection | becomes `{"error_code": ..., "error_message": ...}` in the tool response |
| `injected_response` | injection | a fixed result dict |
| `injected_latency_seconds` | injection | `<= 120`; attaches to an error or response entry |
| `random_seed` | injection | seeds the probability draw, so a flaky injection is reproducible |

## `mock_strategy_type` is an integer in JSON

`MockStrategy` is a plain `enum.Enum` with integer values, so a JSON config must
carry the number. The member *name* is rejected: *"Input should be 0, 1 or 2"*.

| Value | Member | Meaning |
|---|---|---|
| `0` | `MOCK_STRATEGY_UNSPECIFIED` | no fallback mock |
| `1` | `MOCK_STRATEGY_TOOL_SPEC` | an LLM invents a response from the tool's schema and the session's state store |
| `2` | `MOCK_STRATEGY_TRACING` | **deprecated in ADK**; use `TOOL_SPEC` with `tracing=` input instead |

Python code uses the member (`MockStrategy.MOCK_STRATEGY_TOOL_SPEC`); a generated
`.json` uses `1`.

## Constraints ADK enforces

A config that breaks any of these raises at validation, not at run time:

- `tool_simulation_configs` must not be empty, and tool names must be unique.
- Each `InjectionConfig` must set **exactly one** of `injected_error` /
  `injected_response` — *"not both, and not neither"*. A latency-only entry is
  therefore rejected; attach latency to an error or response entry.
- `InjectedError` requires **both** `injected_http_error_code` and
  `error_message`.
- A tool with no `injection_configs` must set a non-`UNSPECIFIED`
  `mock_strategy_type`.
- `injected_latency_seconds <= 120`.

Unknown keys are accepted and ignored, so a misspelled field silently does
nothing — the same trap as eval criteria.

## What happens on a call

The simulator sits in `before_tool_callback`, so it runs instead of the tool:

1. Tool not in the config → returns `None`, the real tool runs.
2. Injections are checked **in list order**. The first whose `match_args` match
   and whose probability draw passes sleeps for its latency and returns its
   error or response.
3. No injection fired and `mock_strategy_type` is `UNSPECIFIED` → ADK logs
   *"did not hit any injection config and has no mock strategy configured"* and
   returns `None`, so the real tool runs.
4. Otherwise the strategy generates a response with the simulation model.

**Cost note.** If *any* tool in the config has a non-`UNSPECIFIED` strategy, the
first simulated call runs a tool-connection analysis over the agent's whole tool
set with `simulation_model`, and every mocked call after that costs a model call.
An injection-only config costs nothing.

## How it attaches

`EnvironmentSimulationFactory` gives two forms, and nothing about the agent needs
to change:

```python
app = App(name="app", root_agent=root_agent,
          plugins=[EnvironmentSimulationFactory.create_plugin(SIMULATION)])

# or, for one agent only:
root_agent.before_tool_callback = EnvironmentSimulationFactory.create_callback(SIMULATION)
```

`create_plugin` returns an `EnvironmentSimulationPlugin` — a `BasePlugin` whose
`before_tool_callback` calls the engine, so it applies to every agent in the App.
Keep the simulated App separate from the real one so production cannot pick it up.

## Keep injections off unless the case is about them

A suite where a third of tool calls randomly fail is a flaky suite, not a robust
one: every case becomes non-deterministic, and you lose the ability to tell a
regression from bad luck.

Because `injection_probability` defaults to **1.0**, "off" has to be written
down. This plugin's generator writes `0.0` on every entry, and the case that
tests a failure path raises exactly one of them to `1.0`. Where a probability
between the two is genuinely the point, pin `random_seed` so the run is
reproducible. Deterministic failure injection tests error handling; random
failure injection tests your patience.

## What it is good for

- **Error paths.** Does the agent retry, report, or invent a result when a tool
  fails? This is where returning errors rather than raising pays off — and where
  an agent that ignores the error shows itself.
- **Latency.** Does a slow tool cause a timeout, a duplicate call, or a
  user-visible hang?
- **Data you cannot arrange.** A frozen cost centre, a restricted category, a
  record in a state that takes a week to reach in the real system.
- **Adversarial fixtures.** A tool result that is well-formed and should not be
  acted on.

## Alternatives, in order of weight

1. **Fixture data in the tool itself.** Simplest, fully deterministic, and enough
   for most cases. The fixture agents in this plugin do this.
2. **Environment simulation.** Configured rather than coded, and it can inject
   failures a fixture cannot easily express.
3. **A separate agent variant wired to fake tools.** Heaviest; reach for it only
   when the agent's own structure has to differ.

Start at 1 and move up only when you need something it cannot do.

## What it does not replace

Simulated tool results are not the real backend. A schema that drifted, an API
that changed its error format, a field that started arriving null — none of that
shows up here. Environment simulation tests the agent's *handling*; integration
tests against the real thing test your assumptions about what it returns.

---

Sources: `adk-python: tools/environment_simulation/environment_simulation_config.py`,
`tools/environment_simulation/environment_simulation_engine.py`,
`tools/environment_simulation/environment_simulation_factory.py`,
`tools/environment_simulation/environment_simulation_plugin.py`,
`tools/environment_simulation/__init__.py`;
`adk-docs: docs/evaluate/environment_simulation.md`; every field above compared
between the installed 2.9.2, 2.10.0, 2.11.0 and 2.12.0 wheels. The `gemini-2.5-flash`
policy is this plugin's, sourced from `adk-recipes: AGENTS.md`, not from ADK —
ADK still defaults `simulation_model` to it.
