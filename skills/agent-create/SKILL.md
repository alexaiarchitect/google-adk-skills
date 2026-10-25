---
name: agent-create
description: >-
  Scaffolds an eval-ready Google ADK 2.x Python agent, adds one tool to an
  existing agent, or adds evaluation scaffolding to one that exists. Produces an
  agent package with an App export, tools whose arguments have Pydantic
  contracts, tools that return validation errors instead of raising, an ordered
  instruction with explicit refusals, an environment-driven model id, plus a
  starter evalset, an eval config and a pytest runner - in the recipes layout
  (app/ package with tests/eval/), the flat layout `adk create` produces, or as
  a Workflow graph. Use when asked to "create / scaffold / bootstrap an ADK
  agent", "start a new agent with these tools", "add a tool to my agent", "make
  this a workflow agent", "add tests to my agent", or "make this agent
  testable". Don't use to review an agent that exists (use
  `/adk:agent-review`), to write eval cases for one (use `/adk:eval-create`), or
  to fix one that misbehaves (use `/adk:agent-debug`).
argument-hint: "<name> [--layout recipes|flat] [--workflow] [--dir path]"
allowed-tools: Read, Grep, Glob, Write, Edit, AskUserQuestion, Skill, Bash(python3 *)
---

# Scaffold an eval-ready ADK agent

Most agents become hard to evaluate for structural reasons, decided on the first
day: arguments with no contract, tools that raise, an instruction that describes
a vibe rather than an order of operations. This scaffold makes the opposite
choices, so that the first eval case has something exact to assert.

`${CLAUDE_SKILL_DIR}` is this skill's directory.

## Flow

1. **Discover.** Is there already an agent here, and what do the neighbours look
   like?

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/../adk/scripts/discover.py <dir> --focus layout,env --no-probe
   ```

2. **Options.** All free; pick one before the interview:

   | Option | When |
   |---|---|
   | recipes layout (recommended) | a project of its own, with `tests/eval/` beside the package |
   | flat layout | matches `adk create`; config auto-discovered beside a single evalset |
   | workflow graph (`--workflow`, with either layout) | deterministic steps around one LLM node |
   | add a tool to an existing agent (`render_tool.py`) | the agent exists; one more contract is needed |
   | eval scaffolding only (`/adk:eval-run gen_test_eval.py`) | the agent exists; only the gate is missing |
   | context caching (`--with-cache`, not with `--workflow`) | a long, fixed procedure reused over multi-turn sessions; pointless for one- or two-turn use |

3. **Interview.** Only the questions the request has not already answered:

   | Ask | Why it matters |
   |---|---|
   | name, and where it goes | the directory name becomes the app name |
   | the tools, with their arguments and value constraints | contracts are what eval cases assert against |
   | the order of operations, and what must be refused | each branch becomes an eval case |

   Push for **value constraints**: which values are legal for each argument,
   what shape identifiers have, what ranges numbers fall in.

4. **Confirm**, scaffold with `--dry-run`, show the write plan, write.

5. **Verify.** Run discovery again and `crosscheck_tools.py` on the starter
   evalset. Then hand off to `/adk:eval-create`.

## What gets generated, and why

| Choice | Reason |
|---|---|
| each tool takes a Pydantic model | the model cannot invent a value; an eval case can assert exact arguments |
| `Literal`, `pattern`, `ge`/`le` on fields | wrong values are caught at the boundary instead of three calls later |
| tools return `{"status": "error", ...}` on a schema failure | the model reads the error and corrects itself; an exception would end the invocation |
| every tool returns a dict with `status` | gives the model something to branch on, and rubric and groundedness metrics something to read |
| docstrings with an `Args:` section | the docstring *is* the interface; it is all the model sees |
| numbered instruction with explicit "Do NOT" rules | each one is a branch a coverage matrix can count and a case can cover |
| an auto-correction paragraph in the instruction | pairs with the returned validation errors, so a dirty input is retried rather than refused |
| `MODEL = os.getenv("MODEL_NAME", "gemini-flash-latest")` | survives a model retirement; alias id, not a dated one |
| `app = App(name=..., root_agent=...)` | the loader prefers it, and plugins and caching only reach an eval run through one |
| starter evalset, config and pytest runner | the project can be evaluated the moment it exists |

## Layouts

**recipes** (default): `<project>/app/{__init__,agent,tools,schemas}.py`,
`<project>/tests/eval/{eval_config.json,test_eval.py,evalsets/happy.evalset.json}`.

**flat**, what `adk create` produces:
`<project>/<name>/{__init__.py,agent.py,.env.example,test_config.json,happy.test.json}`.

The difference that matters: in the flat layout `test_config.json` sits beside
the evalset, so `adk eval` finds it when a single evalset is passed. In the
recipes layout the config must always be passed with `--config_file_path`.

**workflow** (`--workflow`): a `Workflow` with two function nodes and one
`LlmAgent` node, exported as `app = App(root_agent=workflow)`. Eval cases for a
graph read `nodeInfo.path` in traces; a node may set its output once.

## Decision rules

- **No tools, no evaluation.** An agent with no tools has no trajectory, so the
  scaffold requires at least one.
- **The agent name is a Python identifier and never `user`.** ADK rejects both.
- **Sampling settings go in `generate_content_config`.** Passing `temperature=`
  to the agent raises at construction, and that config must not carry `tools`,
  `system_instruction` or `response_schema`.
- **Prefer `Workflow` over `SequentialAgent` / `ParallelAgent` / `LoopAgent`**,
  which are deprecated, unless an `LlmAgent` has to transfer into the block.
- **Never overwrite by default.** Existing files, functions and eval ids are left
  alone unless the user asks for `--force`.
- **Long procedures go in skills, not the instruction.** A procedure needed for
  one kind of request belongs in a `SkillToolset` skill the agent loads on
  demand; a large tool result belongs in an artifact, and a long conversation
  wants event compaction. The instruction stays short and always relevant.
- **A live (voice) agent: read [live-agents.md](references/live-agents.md)
  first.** A slow tool there is silence; make it a streaming (`yield`) or
  non-blocking tool, add `stop_streaming`, and give its evals
  `live_model_config`. A streaming tool on a text model never streams
  (`AGT081`: on 2.12+ a second `yield` aborts the run).
- **A remote step is a state, not a crash.** A `RemoteA2aAgent` that cannot be
  reached yields an error event and the pipeline runs on; write the
  manual-review hold yourself ([workflow-graphs.md](references/workflow-graphs.md)).
- **The generated TODOs are not decoration.** A tool docstring that still says
  "TODO" is a tool the model has to guess about; say so before the user runs
  anything.

## References

| Question | File |
|---|---|
| Why each structural choice makes an agent evaluable | [eval-ready-architecture.md](references/eval-ready-architecture.md) |
| Writing the instruction so its branches are testable | [instruction-design.md](references/instruction-design.md) |
| App, plugins, callbacks and where guardrails belong | [app-and-plugins.md](references/app-and-plugins.md) |
| State, memory and what persists | [state-and-memory.md](references/state-and-memory.md) |
| Workflow graphs: nodes, edges, joins, human input, remote A2A steps, testing them | [workflow-graphs.md](references/workflow-graphs.md) |
| Live and voice agents: streaming and non-blocking tools, `stop_streaming`, barge-in, scheduling | [live-agents.md](references/live-agents.md) |
| Context caching: when it pays, what breaks the prefix, proving the gain | [context-caching.md](references/context-caching.md) |
| ADK skills: `SKILL.md`, `SkillToolset`, discovery and lifecycles, activation evals | [skills-and-progressive-disclosure.md](references/skills-and-progressive-disclosure.md) |
| What the model sees: compaction, `include_contents`, agents as tools, artifacts | [context-engineering.md](references/context-engineering.md) |

## Scripts

| Command | Does | Exit codes |
|---|---|---|
| `scripts/scaffold_agent.py --name N --dir D [--layout recipes\|flat] [--workflow \| --with-cache] --tools SPEC [--description T] [--sop-file F] [--dry-run] [--force]` | writes the project | 0 written, 1 refused, 2 usage |
| `scripts/render_tool.py [agent-dir] --tool SPEC [--no-bind] [--dry-run] [--json]` | adds one tool and its Pydantic model to an existing agent, refusing existing names | 0 written, 1 refused, 2 usage |

Tool spec: `name:field=type,field=type;name:...` where a type is `str`, `int`,
`float`, `bool`, `literal(a|b|c)` or `pattern(^RE$)`.

## Completion checklist

- [ ] The agent loads: discovery reports the expected layout and export.
- [ ] Every tool has a docstring with an `Args:` section, and no TODOs left.
- [ ] Every tool argument has a contract, not just a type.
- [ ] The instruction states the order and the refusals explicitly.
- [ ] The model id comes from the environment.
- [ ] The starter evalset cross-checks clean against the tools.
- [ ] The user knows the next step is `/adk:eval-create`.
