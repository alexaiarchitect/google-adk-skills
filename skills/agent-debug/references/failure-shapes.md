# Failure shapes

Most agent misbehaviour is one of a handful of shapes. Naming the shape matters
because each one rules out different causes, and because "the agent is broken" is
not a diagnosis anyone can act on. "The agent oscillates between the policy
lookup and the funds check because the instruction's rules are circular" is.

## Loop

**Signature.** The same tool, with the same arguments, three or more times in a
row.

**What it means.** Nothing new is arriving. The agent is retrying something whose
result has not changed, which means the tool's response does not tell it what to
do differently — or the instruction tells it to keep trying until something
succeeds, with no limit.

**Layer: tool.** The usual cause is an error message the model cannot act on:
`{"status": "error"}` with no explanation gives it nothing to change.

**Fix.** Make the error say what was wrong and what would be valid. Add an
explicit retry limit to the instruction. If the loop is on a legitimate wait, use
a long-running tool rather than polling.

**Note.** A trajectory-length check catches this instantly; a final-answer check
scores a looping run as a pass, because the agent usually says something
reasonable at the end.

## Oscillation

**Signature.** Two or more tools alternating — A, B, A, B — without ever moving
to the next step.

**What it means.** The instruction's rules are circular: each step's condition
sends the agent back to a previous step. Different from a loop, which repeats one
call; here the agent is making progress-shaped moves that go nowhere.

**Layer: prompt.**

**Fix.** State the progression explicitly: once B has answered, move on rather
than revisiting A. Circular phrasing usually comes from rules written
independently, each sensible alone.

## Stall

**Signature.** Model text where a tool call was required. Often several messages
in a row, asking for clarification or explaining an inability.

**What it means.** The instruction gives no safe path for this input. The agent
is not wrong to stop; it has nothing legitimate to do.

**Layer: prompt.**

**Fix.** Define the default action for an ambiguous or unmatched request — ask a
specific question, or call a refusal tool. "Ask, do not guess" is usually the
right rule, and it needs to be in the instruction.

**Note.** A stall is often the *correct* behaviour badly reported. Check whether
the eval case expected an answer that should not exist.

## Invented tool

**Signature.** A call to a name that is not in the registered tool set.

**What it means.** The model produced a capability from training data rather than
from context. It usually looks plausible — `escalate_to_finance` — which is what
makes it dangerous.

**Layer: model.**

**Fix.** Name the available tools in the instruction, and say that anything else
must be reported rather than attempted. Add `hallucinations_v1` to catch the
related failure where the agent invents *data* instead of a tool.

## Cascading error

**Signature.** An argument mismatch at an early call, then several correct-looking
calls built on it. The trajectory looks nearly right; the outcome is wrong.

**What it means.** Step one normalized or resolved something incorrectly and
nothing revalidated it. The symptom appears at the end, which is why this is the
most commonly misfixed shape.

**Layer: tool**, at the *first* divergence.

**Fix.** Tighten the schema or the docstring for that argument, and assert it
explicitly in the eval case so it fails at call 1 next time. Adding a validation
gate between stages catches the class, not just the instance.

## Wrong order

**Signature.** The right tools, in the wrong sequence. Every call valid, every
argument fine.

**Layer: prompt.** The ordering is implied rather than stated.

**Fix.** An explicit rule and its prohibition: "Always call X first", and "Do NOT
call Y before X." Both, because the second is what a restraint rubric tests.

## Wrong tool

**Signature.** A different tool at a position where another was expected.

**Layer: prompt**, specifically the tool docstrings. Two descriptions overlap
enough that the model picks by coin flip.

**Fix.** Differentiate the descriptions on *when to call*, not on what they do.
"Check whether funds are available" and "Check the spending policy" are
distinguishable; "Look up budget information" and "Get policy data" are not.

## Varies between runs

**Signature.** The same input passes sometimes. Three runs in five is the classic
report.

**Layer: model**, or an underspecified prompt.

**Fix, in order.** Lower the temperature in `generate_content_config`. If that
fixes it, it was sampling variance. If not, make the requirement explicit rather
than louder — the model is choosing between readings the instruction leaves
equally open. If neither works, it is a capability limit: add pipeline
constraints, split the work, then consider a stronger model for that step.

Measure it rather than guessing: `/adk:eval-run --runs 5`, then `pass_at_k.py`.

---

Sources: this plugin's `lib/adk_skills/trajectory.py`;
`adk-python: flows/llm_flows/functions.py`, `tools/function_tool.py`. The failure
taxonomy follows the LinkedIn Learning course *Evaluating AI Agents with Google
ADK*, recommended as background reading.
