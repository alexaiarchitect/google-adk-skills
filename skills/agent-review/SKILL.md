---
name: agent-review
description: >-
  Report-only review of an existing Google ADK Python agent, read statically so
  it works on a project that does not yet run. Checks loader and naming rules,
  model policy (deprecated, preview and hardcoded ids), the constructor kwargs
  that raise, tool contracts (docstrings, type hints, return shapes, raising
  versus returning errors), state writes that never persist, App wiring, ADK 2.x
  drift such as the deprecated workflow shells and global_instruction, backend
  configuration, and whether the instruction has branches an eval case could
  cover. Optionally imports the agent the way ADK does, to prove it loads. Use
  when asked to "review / lint / check my ADK agent", "is this ADK 2
  compatible", "why won't adk find my agent", "why does my agent fail at
  startup", or before trusting an agent you did not write. Don't use for evalset
  quality (use `/adk:eval-review`), runtime misbehaviour (use
  `/adk:agent-debug`), or building a new agent (use `/adk:agent-create`).
argument-hint: "[agent-dir]"
allowed-tools: Read, Grep, Glob, AskUserQuestion, Skill, Bash(python3 *), Bash(uv run *)
---

# Review an ADK agent

Static first, because a review that needs the project to run is useless exactly
when you need it most. Nothing here imports the agent unless you ask for the load
check.

**This skill reports. It does not edit.** Fixes happen after the user has seen
the findings and chosen.

`${CLAUDE_SKILL_DIR}` is set by the skill runtime to this skill's own
directory. Use it verbatim in every command below; a sibling skill's scripts
are at `${CLAUDE_SKILL_DIR}/../<skill>/scripts/`.

## Flow

1. **Review.**

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/lint_agent.py <agent-dir>
   ```

   Optionally prove it loads, which needs an interpreter with ADK:

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/load_agent.py <agent-dir>
   ```

2. **Report** in severity order, leading with the count: "2 errors, 5 warnings".
   An error means ADK will reject it or the behaviour cannot work; a warning
   means it runs, but not as intended.

3. **Options.** Typically: fix the errors, address the model policy, add an App
   export, migrate the deprecated workflow shells, or move on to eval coverage.

4. **Confirm before editing anything.** If the user wants fixes applied, make the
   smallest change that addresses each finding, and re-run the review.

## The fourteen dimensions

| # | Dimension | What goes wrong |
|---|---|---|
| 1 | loader and naming | nothing loadable exported; a name that is not an identifier, or is `user`; two agents in the tree with one name (`AGT015`) |
| 2 | model policy | retiring `gemini-2.0-flash` / `gemini-2.5-flash` (a plugin policy, so a warning), preview ids, hardcoded dated ids |
| 3 | agent configuration | sampling kwargs passed directly; `generate_content_config` carrying tools, system_instruction or response_schema |
| 4 | tool contracts | no docstring, no `Args:`, untyped parameters, raising instead of returning, no `status` |
| 5 | state | `session.state[...] = ...` on a service-fetched session; an instruction `{key}` nothing writes or seeds (`AGT080`) |
| 6 | App wiring | no `App`, so plugins, caching and resumability cannot reach a run |
| 7 | 2.x drift | `SequentialAgent` / `ParallelAgent` / `LoopAgent`, `global_instruction`, YAML config agents |
| 8 | environment | no backend configured; missing project or location |
| 9 | eval readiness | an instruction with no branch, so there is nothing to test |
| 10 | blocking calls | `time.sleep`, `requests`, `subprocess` in a synchronous tool |
| 11 | context caching | a cached prefix that changes per request, a long instruction nothing caches, an out-of-range `ContextCacheConfig`, caching no eval case can exercise (`AGT060`–`AGT063`), and plugin imports that need 2.11 or a public path (`AGT065`, `AGT066`), and a prefix too small for the cache floor (`AGT067`) |
| 12 | skills | a `SKILL.md` ADK will reject, instructions too long to disclose progressively, an agent instruction that repeats a skill's body, a description too vague to trigger it, lifecycle settings out of range (`AGT070`–`AGT075`) |
| 13 | context engineering | an `EventsCompactionConfig` ADK rejects, long conversations with no compaction, a sub-agent scoped wider than its job, a tool returning a large inline blob instead of an artifact (`AGT076`–`AGT079`) |
| 14 | streaming tools | an async-generator tool on a text model, or on a live model whose evals lack `live_model_config` (`AGT081`) |

## Decision rules

- **Errors are things ADK rejects or behaviour that cannot work.** A reserved
  name, a forbidden `generate_content_config` field, a state write that never
  persists. Fix these first: some stop the agent loading at all.
- **Warnings run but mislead.** A deprecated workflow shell still works today,
  and so does a retiring model id. Say when the clock is ticking rather than
  implying it is broken — and where the clock is this plugin's policy rather
  than ADK's, say whose policy it is.
- **A hardcoded model id is a warning, not a style note.** Dated ids get retired,
  and the agent stops working on a date nobody wrote down.
- **A raising tool is worth explaining, not just flagging.** An exception ends the
  invocation; a returned error is an observation the model can act on. That is
  the difference between an agent that self-corrects and one that gives up.
- **A streaming tool belongs to a live agent.** `AGT081` as a warning means a
  text agent has an async-generator tool: on 2.12+ its second `yield` aborts a
  `run_async` run, on 2.9 its body never runs. As an info it means the agent is
  live but no eval config sets `live_model_config`, so evals never exercise the
  streaming. Point at `../agent-create/references/live-agents.md`.
- **The load check is optional and it costs an import.** Offer it when the static
  review is clean but the agent is reported not to work, or when the user is
  mid-migration.
- **Report only.** Do not edit as part of reviewing, even for something obvious.

## References

| Question | File |
|---|---|
| Every finding id, what triggers it, and its ADK source | [review-checklist.md](references/review-checklist.md) |
| The model policy and why it exists | [model-policy.md](references/model-policy.md) |
| Decoding a failed load | [load-check.md](references/load-check.md) |
| Context caching: the decision, the prefix rules, measuring it | [`../agent-create/references/context-caching.md`](../agent-create/references/context-caching.md) |
| Skills: frontmatter limits, progressive disclosure, lifecycles | [`../agent-create/references/skills-and-progressive-disclosure.md`](../agent-create/references/skills-and-progressive-disclosure.md) |
| Context engineering: compaction, `include_contents`, artifacts | [`../agent-create/references/context-engineering.md`](../agent-create/references/context-engineering.md) |
| Live agents: streaming tools, `stop_streaming`, `run_live` vs `run_async` | [`../agent-create/references/live-agents.md`](../agent-create/references/live-agents.md) |

The 1.x-to-2.x drift table lives in the router skill:
`../adk/references/adk-1x-to-2x-migration.md`.

## Scripts

| Command | Does | Exit codes |
|---|---|---|
| `scripts/lint_agent.py <agent-dir> [--strict] [--no-backend] [--adk-version V] [--json]` | the static review, fourteen dimensions (streaming tools: `AGT081`, warn on a text model, info on a live one whose evals lack `live_model_config`); `--adk-version` stands in for the resolved runner in version-gated checks (`AGT066`, `AGT074`) | 0 clean, 1 findings, 2 no agent |
| `scripts/load_agent.py <agent-dir> [--json]` | imports it the way ADK does | 0 loaded, 1 did not load, 2 no runner |

## Completion checklist

- [ ] The review ran and its counts were reported.
- [ ] Every error was explained with its consequence, not just its rule.
- [ ] Model policy findings name the replacement.
- [ ] Deprecated APIs were linked to their 2.x replacement.
- [ ] Nothing was edited without the user asking.
- [ ] If the agent has no evalset, the user was pointed at `/adk:eval-create`.
