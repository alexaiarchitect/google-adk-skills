---
type: llm
---

PASS if the response:

- explains that a case uses `conversation_scenario` with a starting prompt and a
  plan, instead of fixed turns;
- mentions personas, naming at least one pre-built one;
- states that reference-based metrics such as `tool_trajectory_avg_score` cannot
  score a simulated conversation, and names what to use instead;
- notes that this costs model calls for the simulated user as well as the agent.

FAIL if it proposes keeping trajectory matching on the simulated cases, or does
not mention the metric constraint at all.
