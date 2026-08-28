# Calibrating a judge against human judgement

A judge metric produces a number. Whether that number means anything is an
empirical question, and the only way to answer it is to score some cases by hand
and compare. Calibration is that comparison, done on a schedule rather than when
something already looks wrong.

The failure it prevents is specific and common: a suite that has been green for
months while the judge silently stopped discriminating — because the model
behind it changed, because the rubric was loosened to make a case pass, or
because the threshold was tuned down one afternoon and never revisited. Nothing
in the run output distinguishes "the agent is good" from "the judge says yes to
everything".

## When to calibrate

| Trigger | Why |
|---|---|
| a new judged metric is added | you do not yet know what its scores mean |
| a rubric is edited | the rubric *is* the specification the judge applies |
| the judge model id changes | a different model is a different judge |
| a threshold is moved | the number that gates has changed meaning |
| periodically — monthly, or every 50 runs | drift is gradual and nothing announces it |
| the suite has been 100% green for a long time | the most likely explanation is that it stopped testing |

The last row is the one people skip. A suite that never fails is not evidence
of quality; it is an untested instrument.

## How many cases

Sample **10 to 20 cases** per calibration round. Small enough that a person
actually does it, large enough that a systematic disagreement shows up.

Sample across outcomes, not just failures:

- **Passing cases matter more than failing ones.** A failure gets investigated
  anyway. A false pass is invisible, and it is the one that lets a regression
  through.
- **Include cases near the threshold.** A case scoring 0.81 against a threshold
  of 0.8 is where the judge's discrimination is actually being tested; one
  scoring 0.99 tells you nothing.
- **Include at least one refusal case.** Judges are systematically generous to
  fluent refusals, whether or not the refusal was correct.

`calibration_sample.py` draws such a sample from the result history, weighted
towards the margin rather than uniformly.

## How to score

Score blind, before looking at what the judge said. Reading the judge's score
first anchors the human score to it, and the comparison becomes worthless.

For each sampled case, record:

1. the human verdict (pass / fail) and one sentence of reasoning;
2. the judge's score and status;
3. whether they agree.

One person is enough to detect gross miscalibration. Two, scoring
independently, is what you need before concluding the *rubric* is the problem —
if two humans disagree with each other, the rubric is ambiguous and the judge's
inconsistency is a symptom rather than the disease.

## Reading the result

Agreement is the fraction of sampled cases where human and judge reached the
same verdict.

| Agreement | Reading |
|---|---|
| ≥ 90% | calibrated; re-check on the next trigger |
| 70–90% | look at the disagreements as a group before changing anything |
| < 70% | the metric is not measuring what you think; do not gate on it |

Then sort the disagreements, because the two directions have different fixes:

**Judge passes, human fails (false pass).** The dangerous direction. Usually the
rubric is too vague to fail anything — "the response is helpful" cannot be
failed — or it describes a quality rather than a checkable property. Fix the
rubric so it names an observable: what must be present, what must be absent.

**Judge fails, human passes (false fail).** Usually the rubric encodes a
preference about wording rather than a requirement about content, or the case's
reference response is stale. Fix the rubric, or fix the case.

## What to change, and what not to

The correct response to disagreement is almost always **to fix the rubric**,
because the rubric is the specification and the judge is only applying it.

Moving the threshold is the response people reach for first, and it is usually
wrong: it changes how many cases pass without changing what the metric measures,
so it converts a visible disagreement into an invisible one. Move a threshold
only when the scores are *well ordered* — good cases really do score higher than
bad ones — and the cut is simply in the wrong place.

Two more rules:

- **Never edit an eval case to resolve a disagreement**, unless the case itself
  turned out to be wrong and you can say why. A case adjusted to agree with the
  judge no longer specifies anything.
- **Record the round.** Date, sample size, agreement rate, what changed. The
  value of calibration is the trend: a single round says the judge is fine
  today, a sequence of rounds says whether it is drifting.

## After calibration

Re-run the suite with the corrected rubric and compare pass rates. A rubric that
was too vague will typically fail cases that used to pass — that is the fix
working, not a regression. Say so explicitly when reporting it, or the change
gets rolled back by whoever sees the suite go red.

Sources: `adk-docs/docs/evaluate/criteria.md` (rubric-based criteria, judge model
options and `num_samples`); `adk-python/src/google/adk/evaluation/
rubric_based_final_response_quality_v1.py` and `llm_as_judge.py` (how a judge
score is produced and aggregated); `adk-python/src/google/adk/evaluation/
eval_result.py` (the per-metric scores a sample is drawn from). Background
reading: the LinkedIn Learning course "Evaluating AI Agents with Google ADK",
which covers judging agent output with model-based metrics.
