---
type: llm
---

PASS if the response reports at least four of these, each with its consequence:

- `SequentialAgent` is superseded by `Workflow`;
- `gemini-2.0-flash` is deprecated;
- `global_instruction` is deprecated;
- `temperature` passed to the agent constructor raises;
- `generate_content_config` must not carry `system_instruction`;
- the agent name `user` is reserved;
- a direct `session.state` write never persists;
- a tool that raises instead of returning an error.

It should distinguish what stops the agent loading from what merely ages badly.

FAIL if it reports fewer than four, or describes the file as fine. Whether it
edited anything is checked separately by `no-edit-of-the-agent.md`; here, judge
whether the report itself offers to fix rather than silently fixing.
