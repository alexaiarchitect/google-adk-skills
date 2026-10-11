# Writing an instruction you can test

The instruction is the agent's specification. Everything an eval case can assert
about *decisions* traces back to something stated here, so a vague instruction
produces an untestable agent no matter how good the tools are.

## Shape

```
You approve or reject employee expense claims.

Follow these steps in order:
1. POLICY: Always call `lookup_policy` first for the claim's category.
2. FUNDS: Only if the category is not restricted and the amount is within the
   limit, call `check_cost_centre_funds`.
3. DECIDE: Only if funds are sufficient and not frozen, call `submit_claim`.
   Otherwise call `reject_claim` with the specific rule that failed.

Do NOT call `check_cost_centre_funds` if the category is restricted.
Do NOT call `submit_claim` if the cost centre is frozen or has insufficient funds.
Do NOT guess the cost centre; if the user has not said which one, ask.

When you refuse, name the constraint you hit and the value that broke it.

If a tool returns a schema validation error, read the rule in the message,
correct the argument, and retry once. Do not report a validation error to the
user as a refusal.
```

Five parts, each doing a job:

1. **Role**, one line.
2. **Numbered steps** with explicit ordering. Ordering is what `EXACT` and
   `IN_ORDER` trajectory matching check.
3. **Prohibitions**, stated as "Do NOT call X when Y". Each one becomes a
   restraint rubric almost verbatim.
4. **Refusal quality** — what a refusal must contain. Without this the agent
   refuses correctly and unhelpfully, and the user overrides it.
5. **Auto-correction** — what to do with a returned validation error, so a dirty
   input is retried rather than refused.

## Why prohibitions get their own lines

"Only if the category is not restricted, call `check_cost_centre_funds`" and "Do
NOT call `check_cost_centre_funds` if the category is restricted" say the same
thing, and you want both.

The positive form tells the model what to do. The negative form is the one a
coverage matrix counts as a branch, and the one that becomes a rubric:

```
The agent does NOT call `check_cost_centre_funds` when `lookup_policy` reported
the category as restricted.
```

Positive-only evaluation is blind to an agent that does **too much**, and the
prohibition is where the test for that comes from.

## Stop the pipeline, not just the last step

There is a difference between "does not submit the claim" and "does not even
check the budget". An agent that checks funds for a category it already knows is
restricted has demonstrated it was still considering the purchase. State the
short-circuit explicitly, and test it.

## "Ask, don't guess"

```
Do NOT guess the cost centre; if the user has not said which one, ask.
```

Without a line like this, the most likely failure is that the agent supplies a
plausible value and everything downstream looks perfect. It is invisible unless
you test for it, and the instruction has to give the model a legitimate
alternative to inventing.

## Writing for a coverage matrix

The extractor that builds the matrix reads:

- **mandatory** — "always", "must", "first", "then", "before", "after"
- **prohibition** — "never", "do not", "must not", "under no circumstances"
- **conditional** — "if", "when", "unless", "otherwise"

Each conditional and prohibition is a branch, and *N* branches need at least
*N + 1* eval cases. So writing rules on their own lines, in these terms, is not
a style preference: it is what makes the instruction countable.

Keep each rule to one sentence on one logical line. A rule split across two
sentences reads as two rules and gets counted twice.

## What to keep out

- **Tool descriptions.** Those belong in the tool docstrings, where the model
  reads them in context. Duplicating them drifts.
- **Sampling settings.** Temperature goes in `generate_content_config`, not into
  prose like "be deterministic".
- **`global_instruction`.** Deprecated; use `GlobalInstructionPlugin` on the App
  (`from google.adk.plugins.global_instruction_plugin import GlobalInstructionPlugin`
  — it is not re-exported by `google.adk.plugins`).
- **Vague quality adjectives.** "Be helpful and professional" cannot be checked;
  "name the constraint you hit and the value that broke it" can.

## Instruction templates

`{var}` in an instruction is substituted from **session state**, and `{var?}`
makes it optional. A missing key without the `?` raises. Node input is not
available this way.

**2.10 and later leave two forms alone**, which 2.9 did not: `${var}` and a
backslash-escaped `\{var}` are passed through literally (the backslash stays
in the text), so an instruction that has to show a shell variable or a JSON
example no longer needs the value in state. Only a bare `{var}` is filled.
Measured on both wheels: `"Cost ${name}"` renders as `Cost ${name}` on 2.10+ and
as `Cost $Ada` on 2.9.2. `{{var}}` is **not** an escape — it is still
substituted, on both.

## Static and dynamic parts

A long instruction that is resent every turn is a candidate for context caching,
and caching only works if the cached part never changes. So split it:

- **`static_instruction`** — the role, the numbered steps, the prohibitions, the
  refusal and auto-correction paragraphs. A plain literal; ADK sends it verbatim
  and does **not** fill `{var}` there.
- **`instruction`** — only what varies per session or turn: `{cost_centre?}`,
  today's date, an `InstructionProvider`. With `static_instruction` set it moves
  into the user content, after the static prefix.

Dynamic text at the front of the prompt invalidates the cache on every request;
at the end it costs nothing. The coverage-matrix extractor reads both fields, so
the split does not hide a rule from it. When the split is worth making, and how
to prove it saved anything: [context-caching.md](context-caching.md).

## Iterating

When an eval fails on ordering or on a missing refusal, the fix usually belongs
here rather than in the tools: add the explicit ordering constraint, or
differentiate two tool descriptions that the model is confusing. See
`/adk:agent-debug` for deciding which layer a failure belongs to.

---

Sources: `adk-python: agents/llm_agent.py` (instruction, state templating),
`flows/llm_flows/prompt/_instructions_utils.py` (`_TEMPLATE_VAR_PATTERN`; 2.10
adds the `(?<![\$\{\\])` lookbehind, and `utils/instructions_utils.py` is now a
shim re-exporting it), verified by rendering the forms above on installed 2.9.2
and 2.10.0 wheels, with the 2.11.0 pattern re-read unchanged;
`adk-docs: docs/agents/llm-agents.md`, `docs/context/caching.md`
(the static/dynamic split; `agents/llm_agent.py`'s `static_instruction` docstring). The SOP framing and the
prohibition-as-rubric practice follow the LinkedIn Learning course *Evaluating AI
Agents with Google ADK*, recommended as background reading.
