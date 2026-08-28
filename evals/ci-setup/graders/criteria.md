---
type: llm
---

PASS if the response explains that `adk eval` always exits 0, so the workflow
gates on a wrapper script or on pytest instead. It should keep judge metrics out
of the per-change tier, and say which repository variables or secrets are needed.

FAIL if the workflow it proposes runs bare `adk eval` as the gate, or if it never
mentions the exit-code problem.
