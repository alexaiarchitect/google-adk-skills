# Groundedness and safety

## Groundedness is not correctness

Correctness asks *is this claim true?* Groundedness asks *is this claim supported
by what the agent was actually told?*

They come apart in the case that matters. An agent asked about delivery times
answers "usually three to five working days". That may well be true — it is the
kind of thing a model learned in training. But if no tool returned it, the agent
did not know it in this conversation; it produced it. Next time the real answer
is different, the same machinery will produce the same confident sentence, and
now it is wrong.

So a true statement can, and should, fail a groundedness check.

## `hallucinations_v1`

Two stages. A segmenter splits the response into sentences; a validator labels
each one against the full context the agent saw — tool declarations, tool
results, the system instruction, the user's messages:

| Label | Meaning |
|---|---|
| supported | the context backs it |
| unsupported | nothing in the context backs it |
| contradictory | the context says otherwise |
| disputed | the context is inconsistent about it |
| not applicable | not a factual claim (a greeting, a question) |

The score is the supported fraction, so a threshold of 0.8 with one unsupported
sentence in five is a failure.

```json
"hallucinations_v1": {
  "threshold": 0.8,
  "judge_model_options": {"judge_model": "gemini-flash-latest", "num_samples": 3},
  "evaluate_intermediate_nl_responses": true
}
```

**`evaluate_intermediate_nl_responses` is the setting people miss.** Left false,
only the final message is checked. An agent that invents a figure in an
intermediate message and then quietly drops it still passes, having demonstrated
exactly the behaviour you were testing for.

## Where it fits

Three independent axes, and a suite wants all three eventually:

| Axis | Metric | Asks |
|---|---|---|
| trajectory | `tool_trajectory_avg_score` | did it do the right things? |
| quality | rubric metrics | did it say the right things? |
| groundedness | `hallucinations_v1` | did it make anything up? |

An agent can pass any two and fail the third, which is the argument for having
all three rather than a favourite.

## What to do with a failure

Read the labels before changing anything.

- **unsupported** — the agent is filling gaps from training data. Say in the
  instruction that every factual claim must come from a tool result, and that
  missing information is reported rather than guessed.
- **contradictory** — worse: the context said otherwise and the agent overrode
  it. Usually an instruction that tells the model to be helpful more loudly than
  it tells it to be accurate.
- **disputed** — often the tools disagree. Fix the data before the agent.

## Safety

`safety_v1` scores harmfulness through the Vertex AI evaluation service. It needs
either a `GOOGLE_API_KEY` or a project and location with ADC — the facade tries
the key first — takes no judge options because the service picks its own model,
and belongs on any agent that talks to end users.

It is a backstop, not a guardrail. Evaluation tells you afterwards that something
harmful was produced; a `before_model_callback` or a plugin stops it being
produced at all. Use both: the callback in production, the metric to prove the
callback works and to catch what it misses.

## A practical order

1. `tool_trajectory_avg_score` — free, every change.
2. `hallucinations_v1` — as soon as the agent repeats tool data to users.
3. Restraint rubrics — as soon as the agent can do something it must sometimes
   refuse.
4. `safety_v1` — before real users, and nightly thereafter.

---

Sources: `adk-python: evaluation/hallucinations_v1.py`,
`evaluation/safety_evaluator.py`, `evaluation/eval_metrics.py`;
`adk-docs: docs/evaluate/criteria.md`, `docs/safety/index.md`.
