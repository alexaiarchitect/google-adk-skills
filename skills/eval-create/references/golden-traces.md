# Golden traces

A golden trace is a recorded run — the user turns, the tool calls with their
arguments, the final answers — saved as an eval case and treated as the reference
for correct behaviour.

Its value is not what it proves today. You already watched the agent do this.
Its value is that tomorrow, after an instruction is reworded or a model is
upgraded, the same run either still matches or it does not, and you find out in
seconds rather than in production.

## Capturing one

### From the command line

```bash
adk run <agent-dir> --jsonl "Submit claim CLM-000412 for 900 dollars of travel on engineering." > /tmp/run.jsonl
python3 ${CLAUDE_SKILL_DIR}/scripts/session_to_evalset.py \
    --trace /tmp/run.jsonl \
    --eval-id happy_travel_within_policy \
    --evalset tests/eval/evalsets/happy.evalset.json
```

**`--jsonl` is not optional.** Without it `adk run` prints text parts only, so
tool calls and tool errors never appear, and there is nothing to capture.

### From the dev UI

`adk web`, run the request, then the Eval tab → add the current session to an
eval set. Same conversion, and useful when you want to see the trace first.

### From a saved session

`adk run --save_session` writes the session as JSON; `session_to_evalset.py`
reads that too.

## Read it before you keep it

The capture step is mechanical, and that is the danger: a trace of the agent
doing the wrong thing, saved as a golden trace, makes the wrong thing the
specification, and every future run will defend it.

Before committing, check:

- Is the **sequence** what the instruction says it should be?
- Are the **arguments** right — the actual claim id, the actual cost centre, not
  a plausible-looking near miss?
- Did anything get called that should not have been?
- Is the final answer grounded in what the tools returned?

## Evaluation-driven development

The capture flow works in the other direction too, and it is usually better:
write the expected trajectory **first**, run the eval, and iterate the agent
until it passes. The evalset stops being a record of what the agent does and
becomes a statement of what it is supposed to do — which is what lets you tell a
regression from a change.

## Keeping them honest

- **Commit them with the agent.** The implementation and the specification move
  together or they tell two different stories.
- **Never edit one to make a failing run pass.** Understand why the behaviour
  changed first. If the change was intended, update the case deliberately and say
  so in the commit; if it was not, you have found a regression. The scripts here
  refuse to overwrite an existing `eval_id` precisely so that this is a decision
  rather than a reflex.
- **Add one when you fix a bug.** The repro becomes a permanent guard.
- **Re-capture after a deliberate behaviour change**, rather than hand-editing
  the expected calls one by one.

## Trajectory only, or response too?

Capturing gives you both. Drop the expected answer with `--drop-final-response`
when the wording is not part of the contract, and set `response_match_score` to
0 in the config. Keep it when the answer's content is what the case is about, and
score it with `final_response_match_v2` so a paraphrase does not fail.

---

Sources: `adk-python: cli/cli.py` (`--jsonl`), `cli/dev_server.py` (add-session
route), `evaluation/evaluation_generator.py` (event conversion),
`cli/utils/evals.py`; `adk-docs: docs/evaluate/index.md`.
