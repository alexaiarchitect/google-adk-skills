"""Environment simulation: fake the tool backends, not the user.

Wire this onto the App while you are testing failure paths. Every injection here
is written with `injection_probability=0.0` on purpose — the field's own default
is **1.0**, so an injection you forget to switch off fires on every call. Turn one
on only for the case that is about it: a suite where a third of tool calls
randomly fail teaches you nothing except that your evals are flaky.

Environment simulation prints `[EXPERIMENTAL]` warnings through ADK 2.13.0
(`@experimental(FeatureName.ENVIRONMENT_SIMULATION)`); main after 2.13.0
graduated user simulation and environment simulation (the semantic argument
matcher there stays experimental). `ADK_SUPPRESS_EXPERIMENTAL_FEATURE_WARNINGS=1`
silences them.

Docs: https://google.github.io/adk-docs/evaluate/environment_simulation/
"""

import os

from google.adk.apps import App

# The `environment_simulation` package exports only the factory. Every config
# class lives in the `environment_simulation_config` submodule.
from google.adk.tools.environment_simulation import EnvironmentSimulationFactory
from google.adk.tools.environment_simulation.environment_simulation_config import (
    EnvironmentSimulationConfig,
    InjectedError,
    InjectionConfig,
    MockStrategy,
    ToolSimulationConfig,
)

from .agent import root_agent

SIMULATION = EnvironmentSimulationConfig(
    # ADK's own default is `gemini-2.5-flash`, which this plugin's model policy
    # treats as retiring. Set it explicitly.
    simulation_model=os.getenv("MODEL_NAME", "gemini-flash-latest"),
    # Must not be empty, and tool names must be unique.
    tool_simulation_configs=[
        ToolSimulationConfig(
            tool_name="check_cost_centre_funds",
            # UNSPECIFIED is legal here only because `injection_configs` is not
            # empty. When no injection fires, ADK logs a warning and returns
            # None, so the real tool runs.
            #
            # MOCK_STRATEGY_TOOL_SPEC instead makes an LLM invent the response:
            # one tool-connection analysis call on the first simulated call,
            # then a model call per mocked call. MOCK_STRATEGY_TRACING is
            # deprecated in ADK; use TOOL_SPEC with `tracing=` input.
            mock_strategy_type=MockStrategy.MOCK_STRATEGY_UNSPECIFIED,
            injection_configs=[
                # Each entry must carry exactly one of injected_error or
                # injected_response. Both, or neither, is a validation error.
                InjectionConfig(
                    injection_probability=0.0,  # the default is 1.0
                    injected_error=InjectedError(
                        injected_http_error_code=503,
                        error_message="the funds service is temporarily unavailable",
                    ),
                ),
                # Latency attaches to an error or response entry; a
                # latency-only InjectionConfig is rejected. Capped at 120s.
                InjectionConfig(
                    injection_probability=0.0,
                    injected_latency_seconds=5.0,
                    injected_response={"status": "ok", "available_usd": 5000.0},
                ),
            ],
        ),
    ],
)

# Two supported attachments, both built by the factory. Keep the simulated App
# separate from the real one so production can never pick it up.
simulated_app = App(
    name="app",
    root_agent=root_agent,
    plugins=[EnvironmentSimulationFactory.create_plugin(SIMULATION)],
)

# Per agent instead of per app:
#   root_agent.before_tool_callback = (
#       EnvironmentSimulationFactory.create_callback(SIMULATION)
#   )
