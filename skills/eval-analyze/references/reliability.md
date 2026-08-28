# Reliability: pass@k and fragility

## Why one run proves little

An LLM agent samples. A different token leads to a different tool call, a
different trajectory, and a different verdict. So a green run is one observation,
and "it works" is not a property you can read off it.

The question worth answering is **how often** it works, and **where** it is
fragile.

## pass@k

Given *n* runs of which *c* passed, the unbiased estimate that at least one of
*k* attempts passes:

```
pass@k = 1 - C(n - c, k) / C(n, k)
```

Read two numbers together:

- **pass@1** is production behaviour: one attempt per user request.
- **pass@k** is what retrying buys.

The gap between them is **fragility**. A case at `pass@1 = 0.40` and
`pass@5 = 1.00` is not reliable; it is a case that nearly always passes *if you
run it five times*, which no user does.

## Verdicts

| pass@1 | Verdict | Reading |
|---|---|---|
| ≥ 0.95 | RELIABLE | fails about 1 request in 20 or better |
| ≥ 0.70 | MARGINAL | visible to users, and it will be reported |
| below | UNRELIABLE | not working, whatever the best run showed |

## How many runs

| Runs | What it can tell you |
|---|---|
| 1 | that the agent can pass once |
| 5 | "usually works" versus "often fails" — not 95% versus 90% |
| 10-20 | a usable pre-release estimate |
| 50-100 | enough to qualify something that spends money or talks to a person |

Each run costs whatever the config costs, multiplied. This is why the
deterministic tier exists: 20 runs of a free suite is free.

## Suite level

Alongside per-case numbers, the **perfect-run count** — runs where every case
passed — is worth watching. Ten cases each at 0.95 give a suite pass@1 near 0.60:
independently rare failures combine into a frequently broken suite.

Per-case numbers tell you what to fix. The suite number tells you what a user
experiences.

## Producing the runs

```bash
python3 ${CLAUDE_SKILL_DIR}/../eval-run/scripts/run_evals.py <agent-dir> --runs 5
python3 ${CLAUDE_SKILL_DIR}/scripts/pass_at_k.py --dir <agent-dir>/.adk/eval_history --evalset <id>
```

Not `AgentEvaluator(num_runs=5)`: that averages each metric across runs, so a
case that passes three times and fails twice becomes one averaged score, and
whether it "passes" depends on where the average lands. The variance is the
signal, and averaging deletes it.

## Reading a flaky case

Flakiness is diagnostic, not just noise. A case that passes sometimes has a
different cause from one that never passes:

- **Sampling variance** — lower the temperature in `generate_content_config`, or
  add a structural constraint so there is less to vary.
- **Instruction underspecification** — the model is choosing between readings
  that the instruction leaves equally open. Make the requirement explicit.
- **A genuine capability limit** — if neither of the above moves it, add pipeline
  constraints, split the work across agents, or use a stronger model **for that
  step**.

`/adk:agent-debug` walks this.

## Using it in CI

Put a reliability floor in the nightly tier, not the per-change one: repeated
runs are too slow and too expensive to gate a push. A suggested shape:

- per change: deterministic suite, must be green;
- nightly: judge metrics plus 5 runs, alert when any case drops below 0.95;
- release: 20-50 runs, with an explicit floor per case.

## After a fix

Re-run the same number of times and compare. One green run after a change proves
nothing that one green run before it did not.

---

Sources: this plugin's `lib/adk_skills/passk.py`;
`adk-python: evaluation/agent_evaluator.py` (num_runs averaging). The pass@k
framing and the run-count guidance follow the LinkedIn Learning course
*Evaluating AI Agents with Google ADK*, recommended as background reading.
