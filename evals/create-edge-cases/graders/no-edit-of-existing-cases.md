---
type: regex
target: trace
pattern: "\"(?:Edit|Write)\"[^\\n]*happy\\.evalset\\.json"
match: not_contains
---

Adding new cases is the point of this case. Rewriting an existing golden trace
without being asked is not: an evalset under version control is a specification,
and a change to it is a decision someone has to make deliberately.

This matches the execution trace and fails if any `Edit` or `Write` call names
`happy.evalset.json`. New cases belong in a new file, or appended under a new
`eval_id` — both of which leave this grader green.
