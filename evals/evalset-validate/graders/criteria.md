---
type: llm
---

PASS if the response explains that `eval_set_id` must match `^[a-zA-Z0-9_]+$`, so
the dash and the dot in `happy-path.v2` are invalid, and that this is enforced
when ADK writes the file. It should say so after running a validation script, not
from memory alone.

FAIL if it:

- says the file is fine;
- only mentions JSON syntax;
- gives an opinion without validating anything;
- silently edits the file rather than reporting first.
