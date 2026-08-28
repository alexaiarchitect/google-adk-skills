# Diagnosing a failure

## Start with the quadrant

Two scores, four outcomes:

| | Response good | Response weak |
|---|---|---|
| **Trajectory good** | Q1 | Q2 |
| **Trajectory weak** | **Q3** | Q4 |

### Q1 — both good

Nothing to fix. The case earns its place as a regression guard.

### Q2 — good trajectory, weak response

The agent did the right work and described it in unexpected words. Usually the
metric rather than the agent: `response_match_score` is word overlap, so a
correct paraphrase fails it.

Ask first whether the wording is part of the contract. If it is, fix the
instruction's output format. If it is not, either set the threshold to 0 and gate
on trajectory, or move to `final_response_match_v2`, which judges meaning.

### Q3 — weak trajectory, good response

**The one that matters.** The answer reads well; the agent skipped or reordered
work to produce it. Every text-only metric passes this, which is exactly why a
suite without trajectory matching can be green while the agent quietly stops
validating anything.

Two questions:

1. **Was the answer grounded at all?** If the agent skipped a lookup and still
   produced a number, where did the number come from? Add `hallucinations_v1`.
2. **Why was skipping allowed?** Usually the instruction states the step but not
   its necessity. Add the explicit ordering rule and the matching prohibition,
   then add a restraint rubric so the absence is asserted in words too.

### Q4 — both weak

The honest failure, and the easiest. Go to the divergence point.

## Then the divergence point

The earliest call where actual and expected part company. Everything after it is
consequence.

| Kind | What it means | Where the fix usually is |
|---|---|---|
| `missing` | a required call never happened | the instruction: the step is not stated as required |
| `extra` | a call that should not have happened | the instruction: the prohibition is missing |
| `wrong_tool` | a different tool at that position | tool descriptions that overlap |
| `arg_mismatch` | right tool, wrong values | the schema, or the docstring describing that argument |

## Cascading errors

An `arg_mismatch` at call 1 followed by several correct-looking calls. The
trajectory looks almost right and the outcome is wrong, because step one
normalized an input incorrectly and nothing revalidated it downstream.

This is the hardest failure to read, and the most commonly misfixed: the symptom
is at the end, so that is where people look. Two fixes, both at the start:
tighten the schema or the docstring for that argument, and assert it explicitly
in the eval case so the failure surfaces at call 1 next time.

## Rubric failures

A rubric metric reports per-rubric scores with the judge's rationale. Read the
rationale before changing anything: it separates two different problems.

- **The agent failed the property.** Fix the agent.
- **The judge misread the rubric.** Fix the rubric: it is probably compound,
  subjective, or asking about something the transcript cannot settle. See
  `/adk:eval-criteria`.

A rubric that fails intermittently across otherwise identical runs is usually the
second kind, and `num_samples: 1` makes it worse.

## Hallucination labels

`hallucinations_v1` labels each sentence:

| Label | Reading |
|---|---|
| unsupported | the agent filled a gap from training data |
| contradictory | the context said otherwise and the agent overrode it |
| disputed | the sources disagree; fix the data before the agent |

A **true** statement can be unsupported, and it should still fail: this time it
was right by luck, next time the real value will be different.

## Deciding what to do next

| Reading | Next |
|---|---|
| a deterministic failure with a clear divergence | `/adk:agent-debug` to pick the layer |
| passes sometimes | `/adk:eval-run --runs 5`, then `pass_at_k.py` |
| the metric is measuring the wrong thing | `/adk:eval-criteria` |
| the case describes behaviour nobody wants | `/adk:eval-create`, deliberately |
| fixed | add a regression case before moving on |

---

Sources: this plugin's `lib/adk_skills/trajectory.py`;
`adk-python: evaluation/trajectory_evaluator.py`, `evaluation/hallucinations_v1.py`.
The quadrant and divergence-point framing follows the LinkedIn Learning course
*Evaluating AI Agents with Google ADK*, recommended as background reading.
