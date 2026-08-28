"""Fixture agents are data, not tests.

Some fixture projects carry a real ADK pytest suite
(`agents/*/tests/eval/test_eval.py`), because the wrapper scripts under test
have to discover and run one. Those files import `google.adk`, which the
interpreter running this plugin's own tests deliberately does not have - the
whole point of the plugin is that it works before the project's environment
does. So pytest must treat everything under `fixtures/` as data and never
collect it.
"""

collect_ignore_glob = ["*"]
