---
name: agent-debug
description: >-
  Triages a misbehaving or flaky Google ADK agent to a layer and a fix. Names
  the failure shape from a trace or an eval result - loops, oscillation, stalls,
  invented tools, cascading argument errors - then works out whether the cause
  is the tool layer, the prompt layer, the model layer, or the evaluation
  itself, and closes with a regression case and a reliability re-check. Use when
  told "my agent loops / hangs / repeats itself", "it calls the wrong tool",
  "it skips a step sometimes", "it made up a tool", "the eval passes sometimes",
  "the trace looks right but the answer is wrong", or "why is this flaky".
  Don't use for static code problems (use `/adk:agent-review`), for reading
  scores (use `/adk:eval-analyze`), or for writing the cases themselves (use
  `/adk:eval-create`).
argument-hint: "[agent-dir] [--trace file | --result file]"
allowed-tools: Read, Grep, Glob, AskUserQuestion, Skill, Bash(python3 *), Bash(uv run *), Bash(adk *)
---

# Debug a misbehaving ADK agent

The expensive mistake is a correct fix applied at the wrong layer: tightening a
prompt when the tool's error message was the problem, or changing a model when
the instruction was ambiguous. So this works out **which layer** before
suggesting anything.

`${CLAUDE_SKILL_DIR}` is set by the skill runtime to this skill's own
directory. Use it verbatim in every command below; a sibling skill's scripts
are at `${CLAUDE_SKILL_DIR}/../<skill>/scripts/`.

## Flow

Two of the steps below spend money — a fresh `adk run` costs a model call per
turn, and five suite runs cost five suites. Neither happens before the user has
seen the evidence and chosen.

1. **Rule out the evaluation first.** Before debugging the agent, check the test
   is right:
   - Is the golden trace still correct, or did the behaviour change deliberately?
   - Are the thresholds sensible — is anything scored on wording that should not
     be?
   - Did the run actually score, or is this a `NOT_EVALUATED` in disguise?

   A wrong eval means you are debugging a false positive.

2. **Read the evidence that already exists.** Free, and usually enough to name
   the shape:

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/analyze_trace.py --result <agent-dir>/.adk/eval_history --eval-id <id>
   python3 ${CLAUDE_SKILL_DIR}/scripts/analyze_trace.py --trace <existing.jsonl> --agent-dir <agent-dir>
   ```

3. **Report.** The shape, the layer it points at, the first divergence, and what
   the existing evidence cannot settle.

4. **Options, with their cost, then confirm.** Typically:

   | Option | Cost | Answers |
   |---|---|---|
   | capture a fresh trace | one model call per turn | what the agent does *now*, with tool calls visible |
   | five runs, then pass@k | five full suite runs | deterministic or stochastic |
   | fix at the named layer | free to propose | — |

   Ask with `AskUserQuestion` and wait. Nothing below runs before the answer.

5. **Run what was chosen.**

   ```bash
   adk run <agent-dir> --jsonl "the request that fails" > /tmp/run.jsonl
   python3 ${CLAUDE_SKILL_DIR}/scripts/analyze_trace.py --trace /tmp/run.jsonl --agent-dir <agent-dir>
   ```

   Without `--jsonl`, `adk run` prints text only and every tool call is invisible.

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/../eval-run/scripts/run_evals.py <agent-dir> --runs 5
   python3 ${CLAUDE_SKILL_DIR}/../eval-analyze/scripts/pass_at_k.py --dir <agent-dir>/.adk/eval_history
   ```

   Same failure every time is a different diagnosis from three times in five, and
   the fixes have nothing in common.

6. **Name the fix, confirm, close the loop.** One change at the named layer, a
   regression case, re-run several times, then the full suite.

## The three layers

| Layer | What lives there | How it fails |
|---|---|---|
| **tool** | Python functions, schemas, the messages tools return | right tool, wrong arguments; endless retries after an unhelpful error |
| **prompt** | the instruction: ordering, prohibitions, tool descriptions | right tools, wrong order; a tool never called; two tools confused |
| **model** | sampling, capability, what the model invents | different result each run; invented tools; steps skipped sometimes |

Plus the **evaluation** itself, which is step 1 for a reason.

## Failure shapes

Three worth memorising, because they cover most first reports:

| Shape | Signature | Layer | First fix |
|---|---|---|---|
| **loop** | the same call with the same arguments, three or more times | tool | nothing new is coming back: make the error response say what to change, and cap retries in the instruction |
| **stall** | model text where a call was required | prompt | no safe path for this input; define the default action for an ambiguous request |
| **varies per run** | passes sometimes | model | lower the temperature, then make the requirement explicit, then reconsider the model |

The other five — oscillation, invented tool, cascade, wrong order, wrong tool —
plus what each one rules out, are in
[failure-shapes.md](references/failure-shapes.md). `analyze_trace.py` names them
all, so read the script's output rather than matching by eye.

## Deciding between layers

**Deterministic** — same failure every run:

- right tool, wrong arguments → **tool layer**: tighten the schema, or improve
  the argument's description in the docstring.
- right arguments, wrong sequence → **prompt layer**: add the ordering rule.
- a tool that should have been called never is → **prompt layer**: the
  descriptions do not distinguish it.
- everything looks right and the outcome is wrong → **cascade**: work backwards
  to the first divergence.

**Stochastic** — passes sometimes:

- `temperature=0.0` fixes it → sampling variance. Lower it, or add a structural
  constraint so there is less to vary.
- an explicit "do not skip steps" fixes it → the instruction was
  underspecified. Make the requirement explicit rather than louder.
- neither → a capability limit. In order: add pipeline constraints, split the
  work across agents, then consider a stronger model for that step.

## Decision rules

- **Fix the first divergence, not the last symptom.** Everything after it is
  consequence.
- **One change at a time, then re-measure.** Two simultaneous fixes on a
  non-deterministic system teach you nothing.
- **A tool failure does not raise.** ADK returns it as a result with an `error`
  key and the run continues, so "the tool silently did nothing" means reading the
  tool responses.
- **Five runs before and after.** One green run after a change proves nothing
  that one green run before it did not. Both sets cost model calls, so say so
  and get the go-ahead before either.
- **Every fix earns a regression case.** The repro that found it is the case that
  keeps it fixed.

## References

| Question | File |
|---|---|
| The shapes in detail, with what each rules out | [failure-shapes.md](references/failure-shapes.md) |
| The decision tree, step by step | [triage.md](references/triage.md) |
| Where evidence comes from: jsonl, traces, logs, spans | [evidence.md](references/evidence.md) |

## Scripts

| Command | Does | Exit codes |
|---|---|---|
| `scripts/analyze_trace.py (--trace F \| --result F [--eval-id ID]) [--agent-dir D] [--json]` | names shapes, layers and divergences | 0 nothing found, 1 findings, 2 nothing to read |
| `scripts/run_jsonl.py <agent-dir> "<prompt>" [--runs N] [--out F] [--state-file F] [--dry-run] [--json]` | reproduces one interaction with `adk run --jsonl` and keeps the trace under `.adk/skills/traces/`; `--state-file` seeds the session state (ADK 2.12+, exit 2 on older); spends model calls, so confirm first | 0 captured, 1 no events, 2 env |

## Completion checklist

- [ ] The evaluation was ruled out before the agent was blamed.
- [ ] Existing evidence was read, and reported, before anything billable ran.
- [ ] Every billable option was offered with its cost, and the user chose.
- [ ] The failure was reproduced, and shown to be deterministic or stochastic.
- [ ] A shape and a layer were named, with the evidence for them.
- [ ] One change was made at that layer.
- [ ] A regression case was added.
- [ ] The fix was confirmed across several runs, not one.
