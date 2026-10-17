# The maturity ladder

Five levels, each defined by something checkable rather than by intent.

## L0 — Vibe checks

Nothing replayable states what the agent should do. Quality is someone running it
and reading the output.

The tell: "it looks good" appears in a review. Also L0 — a repository with a
`tests/` directory that only starts the agent and asserts it did not crash.

**To leave.** One happy-path case, then one refusal case. Two cases is the whole
difference between L0 and L1, and the second matters more than the first: a suite
that cannot fail is not measuring anything.

## L1 — Golden traces

Cases exist, replay deterministically, and pass or fail without a human deciding.
A change can now be *checked* rather than inspected.

Still missing: anything that runs them automatically. At L1 the suite gets run
when someone remembers, which in practice means after the bug.

**To leave.** A deterministic config and a PR-tier workflow: `/adk:eval-criteria
--tier pr`, then `/adk:eval-ci --tier pr`. Confirm it gates — a workflow that runs
bare `adk eval` is still L1, because it always passes.

## L2 — Automated gates

The suite runs on every change and a failure blocks it. This is the step with the
best return: it moves testing from "before launch" to "on every commit", and it
turns the evalset from documentation into a contract.

Still missing: any notion of how *often* the agent works. At L2 a case that
passes four times in five looks identical to one that always passes.

**To leave.** Repeated runs and a reliability read: `/adk:eval-run --runs 5`, then
`pass_at_k.py`. Add a nightly tier for the judge metrics.

## L3 — Statistical reliability

Reliability is measured. `pass@1` has a number, fragility is visible, and a flaky
case is distinguishable from a broken one — which matters because they need
different fixes.

At this level thresholds become a decision: 95% for something customer-facing,
higher for anything that moves money.

Still missing: any connection to production. The suite tests what someone thought
to test.

**To leave.** Feed real failures back: capture the session, add it as a
`regression_*` case, and let the suite grow from what actually goes wrong.

## L4 — Continuous observability

Production behaviour is traced, scored, and fed back. Every production failure
becomes an eval case, so the suite converges on reality instead of on
imagination.

What to keep doing here: calibrate. Sample passing cases and check you still
agree they should have passed. Criteria that are too lenient produce a green suite
and a worsening agent, and nothing in the system notices.

## Discovery and defense

The ladder can also be read as when a project moves from **discovery** mode
(a few inputs, a person judging) to **defense** mode (a suite, thresholds, a gate
that fails the build). L0 is discovery mode that never left: however careful the
reading, nothing learned is replayable. L1 writes the findings down as cases.
**L2 and above are defense**: the suite protects the floor on every change, and
L3–L4 make the floor statistical and grounded in production. Discovery never
stops — at L4 it is where the new cases come from. The frame, and how each
level of metric fits it: `/adk:adk`'s `eval-engineering.md`.

## Day 0, Day 1, Day 2

Google's "moving past the vibe check" post names three stages that map onto the
ladder: Day 0 (journeys, scenarios, reading traces) is L0 until something is
written down; Day 1 (automated layers and a frozen golden set) is L1 to L2; Day 2
(CI merge rules, traces in a warehouse, dashboards) is L2 to L4. The mapping,
the two merge rules and the four anti-patterns: [merge-gates.md](merge-gates.md).

## Honest self-assessment

Most projects are between L0 and L1 and believe they are at L2, usually because a
workflow exists. Two checks settle it:

1. Is there a case whose expected behaviour is a **refusal**? Without one, the
   suite cannot detect an agent that says yes to everything.
2. Does the CI step gate on something other than bare `adk eval`? If not, it has
   never failed and never will.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/assess_maturity.py <repo-root>
```

reports the level with its evidence and the next concrete step.

---

Sources: this plugin's `assess_maturity.py`. The maturity ladder follows the
LinkedIn Learning course *Evaluating AI Agents with Google ADK*, recommended as
background reading. The discovery/defense reading is from Vlad Kolesnikov,
"From 'vibe checks' to continuous evaluation: engineering reliable AI agents",
Google Cloud blog, 2026-02-28,
https://cloud.google.com/blog/topics/developers-practitioners/from-vibe-checks-to-continuous-evaluation-engineering-reliable-ai-agents.
The Day 0/1/2 mapping is from Selbie, Zamora, Carah and DeMartino, "Evaluating AI
agents: moving past the vibe check", Google Developers Blog, 2026-10-15,
https://developers.googleblog.com/evaluating-ai-agents-moving-past-the-vibe-check/.
