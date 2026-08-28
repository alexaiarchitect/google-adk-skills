# Triage

## Step 0 — rule out the evaluation

Before debugging the agent, check the test.

- Is the golden trace still correct, or did the behaviour change deliberately and
  nobody updated the case?
- Are the thresholds right? A case failing on `response_match_score` may be a
  correct paraphrase failing a word-overlap metric.
- Did the metric actually score? A `NOT_EVALUATED` is a configuration problem —
  an unreachable judge, a missing reference, an empty rubric list — and no amount
  of agent debugging will fix it.

If the eval is wrong, you are debugging a false positive, and any change you make
to the agent is unmotivated.

## Step 1 — read what already exists

Before spending anything: an existing result file or trace usually names the
shape on its own.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/analyze_trace.py --result <agent-dir>/.adk/eval_history --eval-id <id>
```

Report that, and the question it leaves open, before going further.

## Step 1b — establish deterministic or stochastic

**Five suite runs cost five suites, and a fresh `adk run` costs a model call per
turn.** Offer both with their cost and wait for the user's answer; the skill's
flow puts report → options → confirm ahead of this step for exactly that reason.

```bash
python3 ${CLAUDE_SKILL_DIR}/../eval-run/scripts/run_evals.py <agent-dir> --runs 5
python3 ${CLAUDE_SKILL_DIR}/../eval-analyze/scripts/pass_at_k.py --dir <agent-dir>/.adk/eval_history
```

Same failure every run → **deterministic**, go to Step 2.
Some runs pass → **stochastic**, go to Step 3.

This split matters more than anything else here: the two branches have no fixes
in common, and guessing wrong costs a round trip.

## Step 2 — deterministic: tool or prompt?

Work down this list; the first match is the layer.

**Right tool, wrong arguments → tool layer.**
Tighten the schema (a `Literal`, a `pattern`, a bound), or improve the
description of that argument in the docstring. The model fills arguments from the
docstring, so vagueness there shows up as bad values.

**Right arguments, wrong sequence → prompt layer.**
Add the explicit ordering rule *and* the prohibition. Both: the prohibition is
what a restraint rubric tests.

**A tool that should have been called never is → prompt layer.**
The descriptions do not distinguish the tools. Rewrite them around *when to call*
rather than what they do.

**The trajectory looks right and the outcome is wrong → cascading error.**
Work backwards to the first divergence. The fix belongs there, however far it is
from the symptom.

## Step 3 — stochastic: variance or capability?

**Does `temperature=0.0` fix it?** Then it was sampling variance. Lower the
temperature in `generate_content_config`, or add a structural constraint so there
is less room to vary.

**Does an explicit "do not skip steps" fix it?** Then the instruction was
underspecified: the model was choosing between readings that the text left
equally open. Make the requirement explicit — not louder, more specific.

**Neither?** A capability limit. In order of cost: add pipeline constraints so
there is less to get right in one step; split the work across agents or a
workflow; then consider a stronger model **for that step only**.

## Step 4 — close the loop

Four things, and skipping the last two is how the same bug comes back:

1. **Add a regression case.** The repro that found it becomes the guard.
2. **Re-run pass@k.** Five runs, so you know the fix holds rather than that it
   held once.
3. **Run the full suite.** A fix at the prompt layer routinely changes behaviour
   in a case nobody was looking at.
4. **Write down the root cause.** Next time the shape appears, the note is worth
   more than the fix was.

## Quick reference

| Symptom | Layer | First fix |
|---|---|---|
| wrong argument format | tool | tighten the schema |
| endless retries | tool | make the error message say what to change |
| wrong sequence | prompt | add the ordering rule |
| wrong tool chosen | prompt | differentiate the descriptions |
| no call where one was required | prompt | define the default action |
| correct trace, wrong answer | cascade | trace back to the first divergence |
| invented tool or data | model | constrain the tool set; add groundedness |
| inconsistent across runs | model | lower the temperature, then be explicit |

---

Sources: this plugin. The three-layer triage and the decision tree follow the
LinkedIn Learning course *Evaluating AI Agents with Google ADK*, recommended as
background reading.
