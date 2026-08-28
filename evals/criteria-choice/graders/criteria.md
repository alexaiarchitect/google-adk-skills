---
type: llm
---

PASS if the response:

- recommends `tool_trajectory_avg_score` as the gate, and explains it costs no
  model calls;
- addresses the absence of expected wording, either by setting
  `response_match_score` to 0 or by leaving it out;
- keeps judge metrics out of the per-change tier, or says why they belong
  elsewhere.

FAIL if it:

- recommends a judge metric for every pull request without mentioning cost;
- ignores that there are no reference responses;
- writes a config file without asking first.
