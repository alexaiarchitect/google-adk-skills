# Judging case quality

What a validator cannot check, and a reviewer must.

## One behavioural concern per case

A case that asserts the agent normalizes a sloppy claim id **and** explains why
it refused is a bundle. When the bundle goes red, the report tells you a bundle
went red — you still have to open the file, read the trajectory and work out
which half moved. The case has cost you the diagnosis it existed to give you.

Worse, the two halves have different lifetimes. Reword the refusal message and
the case fails on a change that had nothing to do with id normalization, so
someone loosens the assertion, and now neither half is really guarded.

The test is the name. Write what the case proves as a single clause; if the
sentence reaches for an "and", you have two cases and should write both.

## The `eval_id` is usually the only thing anyone reads

A failing case reaches most people as one line: a row in a CI summary, a
filename in `.adk/eval_history/`, a diff header in a review. Whatever the id
says is the entire message, because nobody opens the file until the id has
convinced them it matters.

`test_1` says nothing, so the reader has to go and look — and often does not.
`edge_frozen_cost_centre_no_submit` has already told them the category, the
condition and the behaviour that was supposed to follow from it. The reader can
decide whether this is their problem without reading anything else.

Convention this plugin uses: `<category>_<condition>_<expected>`, where category
is one of `happy`, `edge`, `adversarial`, `memory`, `regression`.

## Assert on arguments, not only on tool names

A trajectory that calls the right tools in the right order with the wrong claim
id is a broken agent that passes a name-only check. This is the difference
between counting function calls and evaluating behaviour, and it is why
`ignore_args` should stay off in the suite that gates releases.

## At least one case must be designed to fail

A suite of happy paths cannot detect an agent that approves everything. Every
suite needs at least one case where the correct behaviour is refusal, and the
assertion is what is **absent** from the trajectory.

There is no special syntax for that: you write the trajectory that should happen,
which stops before the forbidden call, and let exact matching do the rest. To say
it in words as well, add a restraint rubric ("the agent does NOT call X when Y")
via `/adk:eval-criteria`.

## Boundary values

Exactly at the limit, one cent over, an empty string, a value at the format's
maximum length. Boundaries are where off-by-one logic and sloppy comparisons
live, and they cost one line each to test.

## Missing information

What does the agent do when the user leaves out something required? The right
answer is almost always "ask", and the wrong answer — inventing a plausible
value — is invisible unless a case checks for it.

## Redundancy

Two cases proving the same thing cost time and money on every run and tell you
nothing extra. When two cases fail together every time, one of them is a copy.

## Never quietly edit a golden trace

When a case starts failing, the instinct is to update the expectation. Sometimes
that is right — the behaviour changed deliberately. Often it is not, and editing
the specification to match a regression is how a suite stops being worth running.

Rule: understand *why* the behaviour changed, say so, and change the case as a
decision with a reason attached. A skill in this plugin never does it silently.

## Every fixed bug earns a case

The repro that found it becomes the case that stops it coming back. This is the
cheapest coverage you will ever add, and the category exists for it:
`regression_<what>`.

## Coupling

Eval cases state the behaviour the agent is supposed to have, so a change to
either one is a change to the same thing. Keep them in the same repository, the
same commit and the same review: a pull request that alters a refusal rule and
its cases together can be judged, and one that alters only the code cannot.

Split them across repositories, or let the cases lag a release behind, and you
end up with two disagreeing accounts of how the agent behaves — and no way to
tell which one is out of date except by running it.

---

Sources: this plugin. These practices follow the LinkedIn Learning course
*Evaluating AI Agents with Google ADK* (Jigyasa Grover), recommended as
background reading.
