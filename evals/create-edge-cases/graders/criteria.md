---
type: llm
---

PASS if the response measures coverage before answering, and names specific
uncovered behaviours from this agent's own instruction, with numbers (how many
branches, how many cases). It should identify the missing adversarial and
multi-turn layers.

FAIL if it:

- lists generic testing categories without looking at this agent;
- gives no count of branches or cases;
- rewrites an existing eval case instead of adding new ones
  (`no-edit-of-existing-cases.md` checks the tool calls; this one checks the
  reasoning).
