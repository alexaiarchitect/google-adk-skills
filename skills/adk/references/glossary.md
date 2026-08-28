# Glossary

Terms used across this plugin, in the sense ADK gives them.

**Trajectory** — the ordered list of tool calls an agent made, with their
arguments. The thing to evaluate: an agent can produce a plausible final answer
while skipping a validation, and only the trajectory shows it.

**Golden trace** — a recorded trajectory plus the response, stored as an eval
case, treated as the reference for correct behaviour. Its value is not what it
proves today but what it catches after the next change.

**Evalset** — a file of eval cases (`*.evalset.json` or `*.test.json`). The
agent's specification in executable form.

**Eval case** — one behaviour under test, identified by `eval_id`: either a fixed
`conversation` (one or more turns with expected tool calls and response) or a
`conversation_scenario` for simulated users. Never both.

**Invocation** — one user turn and everything the agent did in response. A
multi-turn case is a list of invocations.

**Criterion / metric** — one scoring rule with a threshold, named in the eval
config's `criteria` map. Thirteen ship with ADK.

**Match type** — how strictly a trajectory is compared: `EXACT` (same calls, same
arguments, same order, nothing extra), `IN_ORDER` (expected calls in expected
order, extras tolerated), `ANY_ORDER` (all present, order irrelevant).

**Rubric** — one sentence a judge model answers yes or no about, scoring 1.0 or
0.0. Rubric-based metrics average them.

**Restraint rubric** — a rubric phrased as a negative: *the agent does NOT call X
when Y*. Trajectory matching proves what happened; this proves what must never
happen, which is the only way to test that an agent held back.

**Groundedness** — whether every claim in a response is supported by what the
tools actually returned. Distinct from correctness: a true fact the agent was
never told is still a groundedness failure, because next time the invention will
be wrong.

**Quadrant** — trajectory score against response score. High trajectory with low
response is a presentation problem. **Low trajectory with high response is the
dangerous one**: the answer reads well, the work was skipped, and every text-only
metric passes it.

**Divergence point** — the earliest call where the actual trajectory parts from
the expected one. Everything after it is downstream noise, so it is where a fix
belongs.

**Cascading error** — an argument mistake at step one that later, correct-looking
calls build on. The symptom appears at the end; the cause is at the start.

**Trap mock** — a tool fixture returning data that is structurally valid and
semantically a trap, to see whether the agent reasons about content or
pattern-matches on shape.

**Pass@k** — the probability that at least one of *k* attempts passes, estimated
from *n* observed runs. `pass@1` is production behaviour; the gap between
`pass@1` and `pass@k` is how much the agent depends on luck or retries.

**Fragility** — that gap. A case that passes 2 of 5 runs is not "mostly working";
it is unreliable with a 40% chance per request.

**NOT_EVALUATED** — a metric produced no score: judge unreachable, no reference
response, empty rubric list. Not a regression, and a different fix from a
failure — though `adk eval`'s printed summary counts it as failed.

**User simulation** — replacing fixed user turns with an LLM playing a persona
against a `conversation_plan`. Incompatible with reference-based metrics, since
there is no fixed expected answer.

**Environment simulation** — faking tool backends, with injected errors and
latency, so failure paths are testable without breaking anything real.

**Conformance test** — a recorded interaction replayed against the agent to check
that model requests and tool calls have not changed. Regression testing at the
transcript level, distinct from `adk eval`.

**SOP** — the instruction read as a specification: the ordered steps, the
conditions, and the refusals. Each branch in it is something an eval case should
cover.

---

Sources: `adk-python: evaluation/*`; `adk-docs: docs/evaluate/*`. The evaluation
practices these terms describe follow the LinkedIn Learning course *Evaluating AI
Agents with Google ADK* (Jigyasa Grover), recommended as background reading.
