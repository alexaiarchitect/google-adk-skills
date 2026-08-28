---
type: llm
---

PASS if the response:

- identifies a loop: the same call with identical arguments, repeatedly;
- notices that the tool was returning an error each time, and that a tool error
  is returned rather than raised so the run continued;
- attributes it to the tool layer, and proposes making the error message say what
  to change, plus a retry limit.

FAIL if it blames the model first, suggests changing the model, or does not
mention the repeated tool error.
