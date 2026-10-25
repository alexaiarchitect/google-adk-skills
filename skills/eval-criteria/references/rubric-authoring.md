# Writing rubrics

A rubric is one sentence a judge answers **yes or no**, scoring 1.0 or 0.0. Every
rule here follows from that.

## Binary, never graded

```
Bad:  Rate the helpfulness of the response from 1 to 5.
Good: The response names the specific limit the claim exceeded.
```

A scale invites the judge to hedge, and scores collect in the middle where they
decide nothing. A yes/no claim gives you a verdict you can act on and a number
you can threshold.

## Observable, never subjective

```
Bad:  The response is professional and appropriate.
Good: The response states the claim id and the decision in the first sentence.
```

"Appropriate" means whatever the judge decides today, which is why the same
response scores differently across runs. Ask for something a person reading the
transcript could settle.

## One property per rubric

```
Bad:  The agent cites the policy, names the cost centre, and offers an alternative.
Good: three rubrics, one per clause.
```

A compound rubric collapses three facts into one bit, so a failure tells you
something broke without telling you what. Rubrics are cheap; ambiguity is not.

It also makes the judge inconsistent. Google's Agent Skills team found that an
expectation bundling several criteria got different verdicts on the same
output when only one criterion was missed, and that splitting it fixed the
scores; their suite validator now refuses compound expectations outright. In
the expense-claim domain:

```
Compound: The agent submits CLM-000412 for 650 dollars to cost centre ENG-7 and
          confirms the new amount to the user.
Atomic:   The agent calls `submit_claim` with `amount_usd` 650.
          The agent calls `submit_claim` with `cost_centre` "ENG-7".
          The final response states the amount as 650 dollars.
```

`validate_config.py` and `gen_rubrics.py --lint` warn on a compound sentence
(`RUB006`): one connector (" and ", "; ", " and then ", " as well as ") joining
two clauses that each carry an expectation verb (names, states, calls,
confirms, ...). A noun pair ("every number and identifier") or an adjective
pair is one property and stays clean.

## Rubrics that ask about actions need a trajectory metric

A judge that sees only the final reply grades the agent's account of what it
did, not what it did: an agent that ran `check_cost_centre_funds` first but did
not mention it fails "the agent checks the funds before submitting", and one
that claims a check it never ran passes. That is true of judges that really
see only the reply — `final_response_match_v2`, `response_match_score`, a custom
or hosted judge whose prompt you built from the response.

ADK's `rubric_based_final_response_quality_v1` is not blind: on 2.9.2 and
2.13.0 its prompt includes the tool calls and responses (`<response_steps>`),
but asks whether the **final answer** holds up against them, so an action
property is graded through the answer. Put a rubric that asks whether the
agent called, ran, checked or used something under
`rubric_based_tool_use_quality_v1` (or
`rubric_based_multi_turn_trajectory_quality_v1` for the whole conversation), or
beside `tool_trajectory_avg_score`. `validate_config.py` reports it as info
(`CFG075`) when an action rubric sits under the final-response metric and no
trajectory-aware metric is in the config.

**Built-in tools and the multi-turn judge.** On ADK through 2.13.0,
`rubric_based_multi_turn_trajectory_quality_v1` crashes with `TypeError:
'NoneType' object is not iterable` when the agent carries a built-in tool
(`google_search`, code execution), because the judge prompt iterates function
declarations those tools do not have; main after 2.13.0 lists them as built-in.
`validate_config.py --agent-dir` warns (`CFG076`; info on a runner newer than
2.13.0) when the metric is configured and the agent binds a built-in tool; use `rubric_based_tool_use_quality_v1` there until a
release carries the fix.

## About the transcript, never the agent's mind

```
Bad:  The agent understands that the user is in a hurry.
Good: The response answers the question asked without requesting more information.
```

Nothing in the transcript can settle a claim about understanding.

## Name tools exactly

Use the tool name as the agent defines it, in backticks. A rubric that says
"checks the budget" makes the judge guess which call counted.

## Restraint rubrics

The mirror image, and the only way to test that an agent held back:

```
The agent does NOT call `submit_claim` when `check_cost_centre_funds` reported
the cost centre as frozen.
```

Why this needs its own metric: trajectory matching describes what should happen,
rubric response quality describes what should be said, and an agent that does
**too much** can satisfy both. Excessive agency is invisible to positive-only
evaluation.

Rules for them:

- State the tool and the condition. "The agent should refuse" is not checkable;
  "does not call X when Y" is.
- Put them on `rubric_based_tool_use_quality_v1` with **threshold 1.0**. A
  restraint guarantee that holds four times in five is not a guarantee.
- Test the short-circuit too. If the policy says a restricted category must stop
  the pipeline, write a rubric for the *first* call that should not happen, not
  just the last one. An agent that checks funds for a restricted category has
  already leaked the fact that it considered it.

## Sampling

`num_samples` runs the judge that many times per invocation and takes the
majority. 1 means a single unlucky sample decides; 3 is a stable vote at
moderate cost; 5 is the library default and five times the price. Same principle
as running the agent several times, applied to the evaluator.

## Ids

`rubric_id` appears in per-rubric scores, so it is what you read when one fails.
`no_submit_on_frozen_cost_centre` beats `rubric_3`.

## Where they live

- **On the criterion** in the config: applied to every case, not filtered.
- **On an eval case**: merged in on top, filtered by `type`
  (`FINAL_RESPONSE_QUALITY`, `TOOL_USE_QUALITY`, `TRAJECTORY_QUALITY`). A per-case
  rubric with no `type` is read by nothing.

The effective list must be non-empty or evaluation raises, so a criterion with no
rubrics is only valid when every case supplies its own.

## `text_property`: required before 2.12, optional from 2.12

Each rubric's sentence lives in `rubric_content.text_property`. On 2.9.2 through
2.11.0 the key must be **present**: `{"rubric_id": "r1", "rubric_content": {}}`
fails validation with `textProperty Field required`, while an explicit
`{"text_property": null}` validates. 2.12.0 gives the field a `None` default (so
the dev UI can save a rubric in progress), and the empty `rubric_content` loads.

Either way, a rubric with no sentence asks the judge nothing, so write the
sentence. This plugin's generators always emit `text_property` so a file stays
portable to 2.9. The validators (`EVS082` for an evalset, `CFG044` for a
config) make a missing `text_property` an error only when the runner version is
known and below 2.12, and a warning otherwise.

## Checking your work

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/gen_rubrics.py --lint rubrics.json --agent-dir app
```

Flags graded scales, subjective words, compound sentences (`RUB006`), claims
about the agent's mind, and tool names the agent does not have.

---

Sources: `adk-docs: docs/evaluate/criteria.md`;
`adk-python: evaluation/rubric_based_evaluator.py`, `evaluation/eval_rubrics.py`
(`RubricContent`, validated on installed 2.9.2, 2.11.0 and 2.12.0; the `None`
default is commit `7d8c950`, in `v2.12.0`), `evaluation/llm_as_judge.py`,
`evaluation/rubric_based_multi_turn_trajectory_evaluator.py` (the `TypeError`
measured on 2.13.0; fix `047815043` on main `dfd534a88`). Atomic expectations:
Samborski, "Behind the scenes, two months later: How we ship Google Agent Skills
like products", Google Cloud Blog, 2026-10-29,
https://cloud.google.com/blog/topics/developers-practitioners/behind-the-scenes-two-months-later-how-we-ship-google-agent-skills-like-products.
Action rubrics and reply-only judges: West, "Four evaluation engineering
lessons learned from building agent plugins", Google Cloud Blog, 2026-10-17,
https://cloud.google.com/blog/topics/developers-practitioners/four-eval-engineering-lessons.
Both paraphrased. The rubric-design practices follow the LinkedIn
Learning course *Evaluating AI Agents with Google ADK*, recommended as background
reading.
