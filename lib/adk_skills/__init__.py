"""Stdlib-only helpers shared by the `adk` plugin's skill scripts.

Nothing in this package imports `google.adk`. Agent code is read with `ast`,
never executed, so every validator runs in any Python 3.10+ interpreter even
when the target project's virtualenv is missing or broken. Scripts that truly
need ADK shell out through :mod:`adk_skills.adk_subprocess`.
"""

__version__ = "0.1.0"

# The ADK release this plugin's rules were checked against.
ADK_TARGET = "2.10"

# The oldest release still expected to work. Everything the plugin does runs on
# 2.9 as well; where behaviour differs (the efficiency metrics, the
# `AgentEvaluator` zero-case error) the findings say which release they mean.
ADK_MIN_SUPPORTED = "2.9"

# Model policy (see skills/agent-review/references/model-policy.md).
DEFAULT_MODEL = "gemini-flash-latest"
DEPRECATED_MODELS = ("gemini-2.0-flash", "gemini-2.5-flash")
