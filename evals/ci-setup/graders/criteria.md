---
type: llm
---

PASS if the response explains the `adk eval` exit-code problem (exit 0 whatever
the result through ADK 2.12.0; exit 1 on a failure from 2.13.0 but still no
pass-rate or regression gate), so the workflow gates on a wrapper script or on
pytest instead. It should keep judge metrics out
of the per-change tier, and say which repository variables or secrets are needed.

FAIL if the workflow it proposes runs bare `adk eval` as the gate, or if it never
mentions the exit-code problem.
