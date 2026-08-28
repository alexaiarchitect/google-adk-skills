---
type: llm
---

PASS if the response:

- reports that `edge_frozen_cost_centre_no_submit` passed 2 of 5 runs;
- reads that as roughly a 40% pass rate per request, not as "mostly passing"
  because `pass@5` is high;
- says the agent is not ready, and names the flaky case as what to fix;
- mentions that five runs cannot distinguish 95% from 90%.

FAIL if it presents `pass@5` as the headline number, calls the agent reliable, or
gives no per-case figure.
