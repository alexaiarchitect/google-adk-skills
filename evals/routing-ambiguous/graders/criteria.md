---
type: llm
---

PASS if the response:

- reports what it actually found in this project (the layout, the existing eval
  cases and their count, the configured criteria) rather than giving generic
  advice about testing agents;
- names specific gaps in this project, not gaps in general;
- offers a small number of concrete next steps, each naming a skill.

FAIL if it:

- gives advice before inspecting the project;
- describes ADK evaluation in the abstract without reference to these files;
- starts writing eval cases or configs without being asked to.
