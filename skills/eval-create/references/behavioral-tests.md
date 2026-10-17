# Behavioural tests: assert what the agent did, one failure mode at a time

A behavioural test checks one discrete, observable action at an intermediate
step: the agent asked a clarifying question, ran the validator before submitting,
called the search tool rather than answering from memory. It does not compare a
final string. Google's harness-engineering post makes the case for a fast local
suite of these next to the slower end-to-end benchmarks; this page shows where
they fit beside ADK evalsets and how this plugin generates them.

## Three kinds of check, three jobs

| | Behavioural assertion | Evalset case | Benchmark |
|---|---|---|---|
| Asks | did this one action happen (or not) | did the run match the expected trajectory and response | how good is the agent overall |
| Shape | a pytest function over a recorded turn | an `EvalCase` with expected `tool_uses` and a `final_response` | hundreds of tasks with a score |
| Scoring | a plain `assert`, or one LLM-judge question for a complex path | ADK metrics against thresholds | aggregate |
| Speed | seconds for the suite, one model call per turn | one run per case, plus judge calls | minutes to hours |
| Strictness | as loose as the behaviour allows: "a call to `lookup_policy` happened before `submit_claim`" | as strict as the metric: `EXACT` order and arguments by default | n/a |
| Lives in | `tests/eval/test_behaviors.py` | `tests/eval/evalsets/*.evalset.json` | outside the repo, usually |

They complement each other. An evalset case pins a whole path, which is right for
a golden journey and brittle for a behaviour that can legitimately happen in
several orders. A behavioural assertion pins only the part you care about, so it
survives a reworded instruction or an extra harmless lookup. A benchmark tells
you whether the agent got better in general and nothing about why.

## The loop: one failure mode at a time

1. **Pick one failure you have actually seen.** A trace from `/adk:agent-debug`,
   a bug report, a red evalset case. "Submitted the claim without checking the
   cost centre was open" is a failure mode; "is unreliable" is not.
2. **Write the most flexible assertion that still catches it.** Prefer a strict
   single-turn check on the tool calls (`"check_cost_centre" in turn.tool_names`,
   or the order of two names). Reach for an LLM-judge question only when the
   behaviour is a property of the text, such as "asked which trip the receipt
   belongs to", and keep that question yes/no.
3. **Run it in a batch, not once.** One run of a model-driven turn is a sample.
   Read the pass rate over N runs and watch it over time; a test that passes 7 in
   10 is information, not noise, and blocking on one red run teaches people to
   re-run until green.
4. **Fix the agent, keep the test.** The assertion becomes a guard. When it
   stabilises at the bar you set, it can graduate into an evalset case for the
   CI gate.

That is the post's central point: treat the harness (instruction, tools,
callbacks) like software, with its own unit and integration tests, and grow those
tests from real failures rather than from imagination.

## Generating the file

```bash
python3 ${CLAUDE_SKILL_DIR}/../eval-run/scripts/gen_test_eval.py <project> --behavioral
python3 ${CLAUDE_SKILL_DIR}/../eval-run/scripts/run_pytest_eval.py <project> --behavioral
```

`gen_test_eval.py --behavioral` writes `tests/eval/test_behaviors.py` from the
template `eval-run/assets/templates/test_behaviors.py.tmpl`, beside (not instead
of) the `test_eval.py` evalset gate, filling the prompts and tool names from the
agent's tools and evalsets. `run_pytest_eval.py --behavioral` runs that file
through the same resolved ADK interpreter as the rest of the plugin.

### What is in it

- **`run_turn(prompt, state=None) -> Turn`.** It builds a `Runner` over the
  exported `app` (or `root_agent`) with an `InMemorySessionService`, opens a
  fresh session seeded with `state`, sends one user message and collects what
  happened. A `Turn` has `tool_calls` (name and arguments, in order),
  `tool_names`, `called(tool)`, `args_of(tool)`, the final `text`,
  `asked_a_question` (the reply contains a question mark), the event count and
  any error events.
- **Three checks**, each a function over a `Turn`:
  - `check_calls_expected_tool`: a request that needs a lookup must call
    `EXPECTED_TOOL`;
  - `check_never_calls_forbidden_tool`: a request that must be refused or
    deferred must not call `FORBIDDEN_TOOL` (the side-effecting one);
  - `check_asks_instead_of_guessing`: a request missing a required detail must
    not call `MISSING_FIELD_TOOL` and must ask.
  Each is wired to a prompt in the `BEHAVIORS` list as a
  `Behavior(id, prompt, check, state=None)`. A prompt still starting with
  `TODO` is skipped until you write it. Rename the behaviours after the
  failure each one guards, and delete the ones that do not fit.
- **Batch runs.** `test_behavior` is parametrised over the behaviours and over
  `ADK_EVAL_RUNS` (default 1). A failing run is recorded and reported as
  `xfail` with its reason, because one run is one sample. `test_pass_rates`
  is the gate: it fails when any behaviour's pass rate over the batch is below
  `ADK_EVAL_MIN_PASS_RATE` (default 1.0, every run must pass).
- **A skip, not a crash, without a backend.** With neither `GOOGLE_API_KEY` nor
  `GOOGLE_GENAI_USE_VERTEXAI` plus `GOOGLE_CLOUD_PROJECT` (in the environment
  or the agent's `.env`) the whole module is skipped. It imports and collects
  on ADK 2.9.2 and 2.12.0 alike.

```bash
ADK_EVAL_RUNS=10 ADK_EVAL_MIN_PASS_RATE=0.8 \
  python3 ${CLAUDE_SKILL_DIR}/../eval-run/scripts/run_pytest_eval.py . --behavioral
```

### Writing your own

Add a check and a `Behavior` entry; nothing else changes:

```python
def check_cost_centre_before_submit(turn: Turn) -> None:
    """Seen in a trace: submitted without checking the cost centre was open."""
    assert turn.called("check_cost_centre"), turn.describe()
    if turn.called("submit_claim"):
        names = turn.tool_names
        assert names.index("check_cost_centre") < names.index("submit_claim"), turn.describe()

BEHAVIORS.append(Behavior("cost_centre_before_submit",
                          "Submit my 240 EUR taxi receipt to CC-1042.",
                          check_cost_centre_before_submit))
```

Rules of thumb:

- **Assert on tool calls before text.** A tool call is structured and cheap to
  check; text needs a judge or a pattern, and both drift. `asked_a_question` is
  deliberately crude: tighten it for your agent if it matters.
- **One behaviour per check**, named after the failure it guards.
- **Mock the side effects.** A behavioural test runs the real model, so the tools
  must be fixtures that record their call and return a canned result
  ([trap-mocks.md](trap-mocks.md)); never let one submit a real claim.
- **Keep the suite in seconds per run.** If a check needs a five-turn
  conversation, it is an evalset case or a simulated scenario, not a behavioural
  test.

## Where it sits in the pipeline

| Tier | Runs |
|---|---|
| local, while iterating | behavioural tests, `ADK_EVAL_RUNS=5` |
| per change (CI) | the deterministic evalset gate; behavioural tests with a pass-rate floor if they are stable |
| nightly | behavioural tests at `ADK_EVAL_RUNS=20`, judge metrics, pass@k |

Behavioural tests call the model, so they are not free; they are cheap compared
with a judge-scored suite because they need one call per turn and no judge for
the tool-call assertions. Whether they gate a merge is the same decision as any
other gate: [merge-gates.md](../../eval-ci/references/merge-gates.md).

---

Sources: Mullen and Gunderman, "The anatomy of harness engineering: how to
evaluate, iterate, and guard AI coding agents", Google Developers Blog,
2026-09-09,
https://developers.googleblog.com/the-anatomy-of-harness-engineering-how-to-evaluate-iterate-and-guard-ai-coding-agents/
(behavioural evals beside benchmarks, the one-failure-mode loop, batch pass
rates; paraphrased). This plugin's `gen_test_eval.py --behavioral`,
`run_pytest_eval.py --behavioral` and `test_behaviors.py.tmpl`;
`adk-python: runners.py` (`Runner`), `sessions/in_memory_session_service.py`.
