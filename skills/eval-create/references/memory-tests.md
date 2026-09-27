# Testing context retention

## The failure being tested

A user states a constraint once — which cost centre, which currency, which
account. Several turns later it matters again, and they do not repeat it. The
agent either remembers, asks, or invents.

The third is the one that hurts, and it is invisible in single-turn tests. It is
also quiet in multi-turn ones unless you look at arguments: the agent produces a
confident answer with a plausible value that nobody supplied.

## The shape of the test

Three parts:

1. **The needle.** A constraint stated plainly in an early turn.
2. **The haystack.** Turns in between that do real work, pushing the constraint
   further back in the context.
3. **The recall.** A request that cannot be fulfilled correctly without the
   constraint, phrased so it is not repeated.

The assertion is in the arguments of the final turn: the remembered value appears
there, and the user never said it twice.

```json
"conversation": [
  {"invocation_id": "inv_1",
   "user_content": {"parts": [{"text": "I work in the engineering cost centre."}], "role": "user"},
   "final_response": {"parts": [{"text": ""}], "role": "model"},
   "intermediate_data": {"intermediate_responses": [], "tool_uses": []}},

  {"invocation_id": "inv_2",
   "user_content": {"parts": [{"text": "What is the travel limit?"}], "role": "user"},
   "final_response": {"parts": [{"text": ""}], "role": "model"},
   "intermediate_data": {"intermediate_responses": [],
     "tool_uses": [{"name": "lookup_policy", "args": {"req": {"category": "travel"}}}]}},

  {"invocation_id": "inv_3",
   "user_content": {"parts": [{"text": "Submit CLM-000901 for 900 dollars of travel."}], "role": "user"},
   "final_response": {"parts": [{"text": ""}], "role": "model"},
   "intermediate_data": {"intermediate_responses": [], "tool_uses": [
     {"name": "check_cost_centre_funds",
      "args": {"req": {"cost_centre": "engineering", "amount_usd": 900.0}}},
     {"name": "submit_claim",
      "args": {"req": {"claim_id": "CLM-000901", "cost_centre": "engineering", "amount_usd": 900.0}}}
   ]}}
]
```

## Vary three dimensions

A single memory case tells you almost nothing. The interesting result is where it
stops working:

| Dimension | Vary it by |
|---|---|
| **distance** | stating the constraint in turn 1 versus turn 3 |
| **noise depth** | two intervening tool calls versus six |
| **load** | one constraint versus three (cost centre, currency, approver) |

The useful finding is the boundary: passes at depth 2, fails at depth 5. That is
a specific, reproducible limit you can design around — by writing the constraint
into session state, or by having the agent confirm it — rather than a vague sense
that the agent is forgetful.

## Three behaviours, three verdicts

At the recall turn, exactly one of these happens:

- **Remembers** — the constraint appears in the arguments. Pass.
- **Asks** — the agent requests it again. Acceptable, sometimes correct, and the
  case should say which it expects.
- **Invents** — a plausible value nobody supplied. Failure, and the most
  dangerous of the three because the answer looks fine.

An instruction that says "never guess the cost centre; if the user has not said
which one, ask" turns the third into the second. Test that it does.

## State instead of memory

If a constraint is important, do not rely on attention: put it in session state
when it is first stated, and read it from there. An eval case can pre-load state
via `session_input.state`, which lets a case start from a condition instead of
talking the agent into one.

**`final_session_state` is not the other half.** It is declared on `EvalCase` and
evaluated by nothing through ADK 2.10, so a case that asserts through it passes
whatever the agent did. Assert the read instead: expect the later tool call whose
arguments could only have come from the stored value, or add a rubric that says
the agent used the remembered constraint. That tests the mechanism *and* is
actually scored.

## With simulated users

A scenario case ("state your cost centre early, then ask about two claims without
repeating it") produces this shape without scripting the turns, and varies the
phrasing each run. It cannot use reference-based metrics, so pair it with a
rubric such as "the agent uses a value the user already supplied instead of asking
again". See `/adk:eval-simulation`.

---

Sources: `adk-python: evaluation/eval_case.py` (`session_input`, and
`final_session_state`, which no evaluator reads); `adk-docs: docs/sessions/state.md`,
`docs/sessions/memory.md`. The needle-and-haystack framing follows the LinkedIn
Learning course *Evaluating AI Agents with Google ADK*, recommended as background
reading.
