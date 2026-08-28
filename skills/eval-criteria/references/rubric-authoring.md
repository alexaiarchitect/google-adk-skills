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

## Checking your work

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/gen_rubrics.py --lint rubrics.json --agent-dir app
```

Flags graded scales, subjective words, compound sentences, claims about the
agent's mind, and tool names the agent does not have.

---

Sources: `adk-docs: docs/evaluate/criteria.md`;
`adk-python: evaluation/rubric_based_evaluator.py`, `evaluation/eval_rubrics.py`,
`evaluation/llm_as_judge.py`. The rubric-design practices follow the LinkedIn
Learning course *Evaluating AI Agents with Google ADK*, recommended as background
reading.
