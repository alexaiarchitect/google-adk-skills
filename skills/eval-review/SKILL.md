---
name: eval-review
description: >-
  Validates and reviews Google ADK evalsets and eval configs. Checks schema
  (eval_set_id format, conversation versus conversation_scenario, required
  fields), catches the keys ADK silently ignores, cross-checks every expected
  tool call and argument against the agent's real tools and Pydantic
  constraints, lints rubric wording, checks metric and backend compatibility,
  and measures coverage against the agent's instruction. Use when asked to
  "validate/lint/check my evalset or eval config", "why does adk eval reject
  this file", "are my eval cases any good", "what is my eval coverage", or
  before trusting a suite you did not write. Report only - it never edits.
  Don't use to author new cases (use `/adk:eval-create`), to choose metrics and
  thresholds (use `/adk:eval-criteria`), to run evals (use `/adk:eval-run`), or
  to interpret results (use `/adk:eval-analyze`).
argument-hint: "[agent-dir | evalset.json]"
allowed-tools: Read, Grep, Glob, AskUserQuestion, Skill, Bash(python3 *)
---

# Review ADK evalsets and configs

An evalset is a specification. This skill checks that it says what its author
meant, that ADK will read it that way, and that it covers what the agent
promises. **It reports; it does not edit.** Fixing is `/adk:eval-create` or
`/adk:eval-criteria`, after the user has seen the findings.

`${CLAUDE_SKILL_DIR}` is set by the skill runtime to this skill's own
directory. Use it verbatim in every command below; a sibling skill's scripts
are at `${CLAUDE_SKILL_DIR}/../<skill>/scripts/`.

## Flow

1. **Discover and validate.** Three scripts, cheap and deterministic:

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/validate_evalset.py <agent-dir-or-file>
   python3 ${CLAUDE_SKILL_DIR}/scripts/crosscheck_tools.py <agent-dir-or-file> --agent-dir <agent>
   python3 ${CLAUDE_SKILL_DIR}/scripts/coverage_matrix.py <agent-dir>
   ```

   For the config, call the sibling skill's validator:

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/../eval-criteria/scripts/validate_config.py <config> --agent-dir <agent>
   ```

2. **Report.** Show `Found`, then errors, warnings and notes in that order, then
   the coverage gaps. Lead with the count: "3 errors, 6 warnings, 7 cases short
   of branch coverage."

3. **Options.** Typically: fix the schema errors, close the coverage gaps, adjust
   the criteria, or migrate a legacy file. All are free; the work happens in
   another skill.

4. **Confirm, then hand off** with the `Skill` tool. Do not start editing here.

## What the checks are looking for

Three classes, and the middle one is why this skill exists.

**ADK will reject it.** `eval_set_id` outside `^[a-zA-Z0-9_]+$`, a case with both
`conversation` and `conversation_scenario`, a missing `user_content`.

**ADK accepts it and ignores it.** Eval cases allow unknown keys, and criterion
objects do too, so a misspelled field raises nothing and silently changes
nothing. Also here: a per-case rubric with no `type` (no criterion will read it),
and an expected call to a tool that does not exist (it can never match, so the
case fails forever without telling you anything).

**It runs, but it proves less than you think.** A threshold of 0, `ignore_args`
turned on, a rubric asking for a 1-5 rating, a suite with no case designed to
fail, a branch in the instruction with no case at all.

## Decision rules

- **An expected call to a nonexistent tool is an error, not a warning.** The case
  can never pass, and it will be read as an agent regression.
- **A reference-based metric with threshold 0 needs no reference response.**
  Disabling `response_match_score` is the normal way to run a trajectory-first
  suite; an empty `final_response` is then a note, not a fault.
- **`session_input.app_name` mismatches are warnings.** The run still works, since
  the eval runner takes its `app_name` from the session the case creates. What
  breaks is scoping: `app:` state, memory and artifacts live under that name.
- **Coverage is evidence, not a verdict.** Rule-to-case matching is heuristic.
  Present uncovered branches as questions for the user, not as failures.
- **Never edit a golden trace to make a check pass.** If a case looks wrong, say
  why and let the user decide; a changed specification is a decision, not a fix.

## References

| Question | File |
|---|---|
| Every finding id, what triggers it, and the ADK source behind it | [validation-rules.md](references/validation-rules.md) |
| How arguments are matched, and why nesting matters | [tool-crosscheck.md](references/tool-crosscheck.md) |
| Reading the coverage matrix, the branch rule and suite shape | [coverage-matrix.md](references/coverage-matrix.md) |
| Judging case quality beyond what a script can check | [quality-review.md](references/quality-review.md) |

## Scripts

| Command | Checks | Exit codes |
|---|---|---|
| `scripts/validate_evalset.py <path> [--agent-dir D] [--config F] [--adk-version V] [--strict] [--json]` | schema, ids, structure, app_name, rubrics (`EVS082`: a missing `text_property` is an error below 2.12, a warning otherwise; `--adk-version` overrides the detected runner), `load_skill` for an unregistered skill (`EVS074`) | 0 clean, 1 findings, 2 bad target |
| `scripts/crosscheck_tools.py <path> --agent-dir D [--json]` | tool names, argument nesting, Literal/regex/bounds | 0 clean, 1 findings, 2 bad target |
| `scripts/coverage_matrix.py <agent-dir> [--evalset F] [--json]` | branch coverage, suite shape, tool coverage, skills no case activates (`EVS073`) | 0 no gaps, 1 gaps, 2 bad target |
| `scripts/validate_eval_yaml.py [path] [--strict] [--json]` | every `EVAL.yaml` under an `adk-recipes` plugin skill, plugin or repository: `cases[].id/prompt/rubric/assertions`, known assertion types and their fields, duplicate ids, empty rubrics (`EVY001`–`EVY010`); never runs the cases | 0 no errors, 1 errors (or warnings with `--strict`), 2 nothing to check (no `EVAL.yaml` under the path) |

## Completion checklist

- [ ] All three validators were run, and their counts reported (plus
      `validate_eval_yaml.py` when the target is an `EVAL.yaml`).
- [ ] Every error was explained with its fix, not just its id.
- [ ] Coverage gaps were named as specific uncovered branches.
- [ ] Nothing was edited; the user chose what happens next.
