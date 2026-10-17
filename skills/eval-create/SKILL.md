---
name: eval-create
description: >-
  Designs, writes and manages Google ADK eval cases. Covers the case taxonomy
  (happy path, edge and refusal, adversarial, memory, regression), how many
  cases a suite needs and of what shape, naming, capturing a golden trace from
  a real run or a saved session, converting `adk run --jsonl` output into an
  eval case, migrating legacy eval files (both legacy shapes) to the current
  schema, multi-turn context retention tests, closing coverage gaps branch by
  branch, and curating an existing suite: list, rename, move, split, remove and
  dedupe cases, rebase the app name, and the dev-UI and `adk eval_set` flows.
  Use when asked to "write/add/generate eval cases", "capture a golden trace",
  "turn this session into a test", "add a regression case", "test that it
  refuses", "test whether it remembers", "migrate my old .test.json", "rename /
  delete / dedupe eval cases", or "clean up my evalset". Don't use to pick
  metrics or rubrics (use `/adk:eval-criteria`), to validate files without
  changing them (use `/adk:eval-review`), to run evals (use `/adk:eval-run`), or
  to set up simulated users (use `/adk:eval-simulation`).
argument-hint: "[agent-dir] [--evalset file]"
allowed-tools: Read, Grep, Glob, Write, Edit, AskUserQuestion, Skill, Bash(python3 *)
---

# Write and manage ADK eval cases

An eval case says: given this input, the agent should do **these things, in this
order, with these values**. Writing them well is mostly about scope — one
behaviour per case — and about covering the branches the instruction actually
has, rather than the one path someone demonstrated. Keeping a suite good over
time is the same discipline applied to renames, splits and deletions.

`${CLAUDE_SKILL_DIR}` is this skill's directory.

## Flow

1. **Discover.** What the agent can do, what is already covered, where the gaps
   are:

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/../adk/scripts/discover.py <agent-dir> --focus layout,agent,evalsets,configs,coverage
   python3 ${CLAUDE_SKILL_DIR}/../eval-review/scripts/coverage_matrix.py <agent-dir>
   python3 ${CLAUDE_SKILL_DIR}/scripts/manage_cases.py list <agent-dir> --by-category
   ```

   The coverage matrix names the specific uncovered branches, which is what the
   new cases should be. The list shows what a suite already asserts.

2. **Report.** Tools and their argument contracts, existing cases by category,
   the branch count against the case count, the uncovered rules by name, and
   anything the suite carries that it should not (duplicates, vague ids, a stale
   `app_name`).

3. **Options.** Usually: close the branch gaps, capture a golden trace from a
   real run, add the missing adversarial or memory layer, migrate a legacy file,
   add a regression case for a bug just fixed, or tidy the suite (rename, split,
   dedupe). All free.

4. **Confirm**, then generate with `--dry-run`, show the write plan, write, and
   validate with `/adk:eval-review`'s scripts. An explicit prior grant from the
   user ("proceed without asking") satisfies this step; the plan is still shown.

## The categories

| Category | Proves | Id prefix |
|---|---|---|
| **happy** | the unobstructed path works end to end | `happy_` |
| **edge** | each branch stops where it should, including "ask, don't guess" | `edge_` |
| **adversarial** | pressure, claimed authority and tempting-but-valid data do not change the decision | `adversarial_` |
| **memory** | a constraint stated once survives intervening turns | `memory_` |
| **regression** | a fixed bug stays fixed | `regression_` |
| **activation** | a skill-equipped agent loads the right skill, and none for an unrelated request | `activation_` |

Roughly 70% narrow single-turn cases, 20% multi-turn, 10% adversarial. Weight
adversarial higher for an agent that spends money or writes to a system of
record. A baseline case with no tools bound proves the agent degrades honestly
instead of inventing capabilities.

## Decision rules

- **One behavioural concern per case.** If naming what it proves needs an "and",
  it is two cases; `manage_cases.py split` turns one into several.
- **N branches need N+1 cases.** One per conditional or prohibition in the
  instruction, plus the happy path. The coverage matrix counts them for you.
- **At least one case must be a refusal.** A suite of happy paths cannot detect
  an agent that approves everything.
- **A forbidden call needs no special syntax.** Write the trajectory that should
  happen — which stops before the forbidden call — and let exact matching do the
  work. Add a restraint rubric via `/adk:eval-criteria` to say it in words too.
- **Assert on arguments, not just names.** The right tools in the right order
  with the wrong claim id is a broken agent that a name-only check passes.
- **Name the behaviour in the id.** The id is what a person reads first when a
  run goes red; `edge_frozen_cost_centre_no_submit` tells them what broke
  before they open the file, `test_3` does not.
- **Leave `final_response` empty when only the trajectory matters.** With
  `response_match_score` at 0, an empty expected answer is deliberate.
- **Never edit an existing case to make a run pass.** Every writer here refuses
  to overwrite an `eval_id`. Change a case deliberately, with the reason
  recorded, through `manage_cases.py`, and re-capture rather than hand-edit.
- **Capture only runs you have read.** A golden trace of the agent doing the
  wrong thing locks in the wrong thing.
- **Legacy files come in two shapes.** The wrapped shape still runs under
  `adk eval`; the flat-turn shape crashes it and only `AgentEvaluator` reads it.
  Migrate both for a stable id and a readable diff.

## Capturing a golden trace

```bash
adk run <agent-dir> --jsonl "the request to record" > /tmp/run.jsonl
python3 ${CLAUDE_SKILL_DIR}/scripts/session_to_evalset.py --trace /tmp/run.jsonl \
    --eval-id happy_travel_claim --evalset tests/eval/evalsets/happy.evalset.json
```

Without `--jsonl`, `adk run` prints text only and every tool call is invisible.
The dev UI does the same conversion: `adk web`, run the request, then Eval → add
the current session. That tab also edits and deletes cases and runs them with
threshold sliders; see [managing-evalsets.md](references/managing-evalsets.md).

## References

| Question | File |
|---|---|
| Choosing categories, sizing a suite, the branch rule | [case-taxonomy.md](references/case-taxonomy.md) |
| Every field of an eval case, with worked examples | [evalset-schema.md](references/evalset-schema.md) |
| Capturing, converting and trusting golden traces | [golden-traces.md](references/golden-traces.md) |
| Designing context-retention tests | [memory-tests.md](references/memory-tests.md) |
| Adversarial cases and trap fixtures | [adversarial-cases.md](references/adversarial-cases.md), [trap-mocks.md](references/trap-mocks.md) |
| Curating a suite: dev UI, `adk eval_set`, GCS, frozen suites, versioning, recipes `EVAL.yaml` | [managing-evalsets.md](references/managing-evalsets.md) |
| Behavioural assertions in pytest, one failure mode at a time | [behavioral-tests.md](references/behavioral-tests.md) |
| Activation cases for an agent with ADK skills | [skills-and-progressive-disclosure.md](../agent-create/references/skills-and-progressive-disclosure.md) |

Templates: `assets/templates/evalsets/` — one per category (including
`activation.evalset.json`, a positive and a negative skill-load case), a baseline
no-tools case, and a scenario case.

## Scripts

| Command | Does | Exit codes |
|---|---|---|
| `scripts/gen_cases.py <agent-dir> --spec JSON (--evalset F \| --new ID) [--dry-run]` | adds cases, refuses to touch existing ids; a `{"skill": "name"}` entry in a turn's `tools`, or `"skill": "name"` on the turn (loaded first), becomes `load_skill` with `{"skill_name": "name"}`, prefixed when the toolset sets `tool_name_prefix`; `activation_*` ids land in the activation category | 0, 1 findings or clash, 2 usage |
| `scripts/session_to_evalset.py --trace F --eval-id ID --evalset F [--drop-final-response]` | turns a run into a case | 0, 1 findings, 2 usage |
| `scripts/migrate_legacy.py <old.test.json> [--out F] [--eval-set-id ID]` | either legacy shape to the EvalSet schema | 0, 1 findings, 2 usage |
| `scripts/manage_cases.py list\|show\|rename\|move\|remove\|dedupe\|split\|rebase-app-name ... [--evalset F] [--dry-run] [--registry F] [--unfreeze]` | curates an existing suite through a write plan; refuses to remove, rename, move (either end), split, `dedupe --apply` or rebase a case in a `"frozen": true` suite (`EVS120`, read from `--registry` or the nearest `eval_registry.json`) unless `--unfreeze`; adding cases to a frozen suite is allowed | 0, 1 refused, 2 usage or frozen |

## Completion checklist

- [ ] Every new case has one concern and a descriptive `eval_id`.
- [ ] Each uncovered branch named by the coverage matrix now has a case, or was
      consciously left.
- [ ] At least one case in the suite is a refusal.
- [ ] Arguments are asserted, not just tool names.
- [ ] `session_input.app_name` matches what the agent resolves to.
- [ ] No existing case was modified without the user deciding it.
- [ ] `validate_evalset.py` and `crosscheck_tools.py` exit 0.
