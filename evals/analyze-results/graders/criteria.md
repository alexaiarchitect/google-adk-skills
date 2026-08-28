---
type: llm
---

PASS if the response:

- says the agent skipped `check_cost_centre_funds`, naming the tool;
- identifies that the trajectory failed while the response metric passed, and
  explains why that combination is the dangerous one;
- points the fix at the first divergence rather than at the final answer.

FAIL if it only repeats the scores, blames the response wording, or cannot say
which call was missing.
