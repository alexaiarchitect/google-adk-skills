---
name: adk
description: >-
  Entry point for Google ADK (Python, 2.x) agent and evaluation work. Discovers
  the project layout, ADK version, LLM backend, existing evalsets, criteria,
  results and coverage, shows what exists and what is missing, then routes to
  one focused skill. Use when the user says "adk", "evaluate my agent",
  "evalset", "golden trace", "trajectory", "eval criteria", "rubric", "pass@k",
  "LLM judge", "groundedness", "hallucination check", "safety eval", "user
  simulation", "conformance", "eval CI gate", "sub-agent", "multi-agent",
  "workflow agent", "eval history", "rename/delete/dedupe a case", "dev UI",
  asks what this plugin can do, or when the intent spans several of those.
  Don't use when the request already names one task - go straight to
  `/adk:eval-create`, `/adk:eval-criteria`, `/adk:eval-run`, `/adk:eval-analyze`,
  `/adk:eval-review`, `/adk:eval-ci`,
  `/adk:eval-simulation`, `/adk:agent-create`, `/adk:agent-review` or
  `/adk:agent-debug`.
argument-hint: "[agent-dir]"
allowed-tools: Read, Grep, Glob, AskUserQuestion, Skill, Bash(python3 *)
---

# ADK agents and evaluations — start here

Everything this plugin does rests on one move: turning behaviour that currently
exists only as an instruction — and as whatever the author remembers testing —
into cases a machine replays on demand. Nothing else here is worth much until
that exists, and once it does, most questions about the agent have an answer you
can measure. So the first step is never advice: it is finding out what this
project already has.

`${CLAUDE_SKILL_DIR}` is set by the skill runtime to this skill's own
directory. Use it verbatim in every command below; a sibling skill's scripts
are at `${CLAUDE_SKILL_DIR}/../<skill>/scripts/`.

## Flow

1. **Discover.** Run it before saying anything about the project:

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/discover.py <path> --format md
   ```

   Add `--no-probe` to skip the interpreter check when you only need file facts.
   `--focus layout,evalsets` narrows the work. For the environment alone:
   `python3 ${CLAUDE_SKILL_DIR}/scripts/check_env.py <agent-dir>`.

2. **Report.** Show the user the `Found` / `Missing` / `Warnings` blocks the
   script produced. Do not paraphrase the numbers; they are the evidence for
   every option you are about to offer.

3. **Options.** Offer the skills from the report's `Options` section, each with
   its cost tier (`free` deterministic, `judge` model calls, `vertex` Vertex AI
   evaluation service). Exactly one is marked recommended.

4. **Confirm.** Ask with `AskUserQuestion`, then invoke the chosen skill with the
   `Skill` tool. Nothing is written and no model call is paid for before that
   answer. Full protocol: [interaction-protocol.md](references/interaction-protocol.md).

## Route by intent

| The user wants | Skill |
|---|---|
| a new agent, or eval scaffolding for one | `/adk:agent-create` |
| a review of an existing agent, ADK 2.x compatibility, "why won't adk find it" | `/adk:agent-review` |
| a fix for looping, stalling, wrong tool, flaky eval | `/adk:agent-debug` |
| eval cases written, a golden trace captured, a session turned into a test | `/adk:eval-create` |
| to choose metrics, set thresholds, write rubrics, add a judge | `/adk:eval-criteria` |
| evalsets and configs validated, coverage measured | `/adk:eval-review` |
| the suite run, repeated for pass@k, or conformance recorded | `/adk:eval-run` |
| results explained, reliability measured | `/adk:eval-analyze` |
| simulated users, personas, mocked tool backends | `/adk:eval-simulation` |
| a CI gate, or a read on evaluation maturity | `/adk:eval-ci` |

A request that names none of these is usually "is my agent any good?", which
starts with `/adk:eval-review` when cases exist and `/adk:eval-create` when they
do not.

## Several agents in one repository

When discovery reports a collection, or the request is about sub-agents or a
monorepo, inventory them before choosing one:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/inventory.py <repo-root>
```

One row per loadable agent: layout, app name, tools, cases by category, config
cost tier, last result, maturity level. Read down the maturity column and the
next piece of work names itself. A sub-agent can only be evaluated on its own
through the pytest path (`/adk:eval-run --agent-name`); `adk eval` has no such
option. `run_registry.py --init` turns the inventory into a batch manifest.

## What to tell the user when they ask what this can do

Six things, in the order they usually matter:

1. **Build** an eval-ready agent, or add eval scaffolding to one that exists.
2. **Review** the agent statically against ADK 2.x rules and a model policy.
3. **Write** eval cases: happy path, edge, adversarial, memory, regression.
4. **Choose** criteria from the 13 configurable ADK metrics, with thresholds and
   rubrics.
5. **Run** them, locally or in CI, and read the results honestly.
6. **Measure** reliability across repeated runs, and triage what fails.

## Ground rules

- **Never guess the layout, version or backend.** Run the discovery script.
- **An existing golden trace is a specification.** Never edit one to make a
  failing test pass without saying so; add a sibling case instead.
- **Say what a run costs before running it.** Judge and Vertex metrics spend
  money per case per sample.
- **Cite the source** when you explain an ADK behaviour, so the user can check.

## References

| Question | File |
|---|---|
| How every skill in this plugin behaves | [interaction-protocol.md](references/interaction-protocol.md) |
| What each skill does, what it needs, what it costs | [capability-map.md](references/capability-map.md) |
| The report format and finding-id scheme | [gap-report-format.md](references/gap-report-format.md) |
| Backends, `.env`, Vertex, picking a Python runner | [environment.md](references/environment.md) |
| The layouts ADK accepts, and where eval files belong | [layouts.md](references/layouts.md) |
| ADK 1.x patterns and their 2.x replacements | [adk-1x-to-2x-migration.md](references/adk-1x-to-2x-migration.md) |
| What a word means here (trajectory, golden trace, quadrant) | [glossary.md](references/glossary.md) |

## Scripts

| Command | Output | Exit codes |
|---|---|---|
| `scripts/discover.py <path> [--format json\|md] [--focus ...] [--no-probe]` | full gap report | 0 clean, 1 errors, 2 bad target |
| `scripts/check_env.py <agent-dir> [--json] [--config path]` | runner, backend, criteria readiness | 0 ready, 1 blocked, 2 bad target |
| `scripts/inventory.py [repo-root] [--json]` | every agent in a repository with its evaluation standing | 0, 2 bad target |

## Completion checklist

- [ ] Discovery was run, and its output shown to the user rather than summarized from memory.
- [ ] The ADK major version is known, or the user was told it could not be verified.
- [ ] Options were offered with cost tiers, and the user chose.
- [ ] The chosen skill was invoked, not re-implemented here.
