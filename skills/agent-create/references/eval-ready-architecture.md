# What makes an agent evaluable

Evaluability is decided by structure, not by test effort. Five choices account
for most of it.

## 1. Arguments have contracts

```python
class ClaimRequest(BaseModel):
    claim_id: str = Field(pattern=r"^CLM-[0-9]{6}$")
    cost_centre: Literal["engineering", "sales", "marketing", "operations"]
    amount_usd: float = Field(ge=0.0)

def submit_claim(req: ClaimRequest) -> dict[str, Any]: ...
```

Without this, an eval case can only assert that `submit_claim` was called. With
it, the case asserts *which claim*, *which cost centre*, *how much* — and a
second evaluation axis appears: **argument correctness**. An agent can call
exactly the right tools in exactly the right order with the wrong id, and only
argument-level assertions catch it.

The constraints also do real work at runtime. `Literal` means the model cannot
invent a cost centre. `pattern` means a malformed id fails at the boundary rather
than three calls later, when the failure is much harder to trace.

## 2. Tools return errors; they do not raise

```python
try:
    req = ClaimRequest.model_validate(req)
except ValidationError as exc:
    return {"status": "error", "error": f"schema validation failed: {exc}"}
```

An exception ends the invocation. A returned error is an observation: the model
reads what was wrong and can correct itself. Pair it with a line in the
instruction —

> If a tool returns a schema validation error, read the rule in the message,
> correct the argument, and retry once.

— and a dirty input becomes a self-correction rather than a failed request. In a
trajectory that shows up as a retry, which is exactly why `IN_ORDER` matching
exists.

This is also why ADK itself converts missing arguments and rejected confirmations
into function responses with an `error` key rather than exceptions.

## 3. The docstring is the interface

What the model sees of a tool is its **name, its docstring and its type hints**.
Nothing else. So a docstring is not documentation:

```python
def check_cost_centre_funds(req: FundsRequest) -> dict[str, Any]:
    """Check whether a cost centre can absorb a claim.

    Only call this after `lookup_policy` has confirmed the category is not
    restricted and the amount is within the limit.

    Args:
        req: A FundsRequest naming the cost centre and the claim amount.

    Returns:
        A dict with `status`, `available_usd`, `frozen` and `sufficient`.
    """
```

Most trajectory failures trace back to an ambiguous tool description rather than
to broken logic. Two tools whose descriptions overlap will be confused, and no
amount of instruction tuning fixes that reliably.

## 4. Returns are dicts with a status

A `status` key gives the model something unambiguous to branch on, and gives
rubric and groundedness metrics something to check the response against. A bare
string forces both to infer.

## 5. An `App` is exported

```python
root_agent = Agent(...)
app = App(name="app", root_agent=root_agent)
```

The loader looks for `app` first. More importantly, plugins, context caching,
event compaction and resumability only reach a run — including an eval run —
through an `App`. An agent exporting only `root_agent` cannot use any of them in
evaluation.

## And one thing not to rely on

If an evaluation depends on behaviour the model *chose* rather than behaviour it
was *required* to produce, the evaluation rests on nothing. A model that tidies
"Engineering Dept." into `engineering` today may not tomorrow, and the eval case
that relied on it will fail for a reason nobody wrote down. Encode the
requirement in a schema, or state it in the instruction; do not discover it in a
passing test and assume it will hold.

## Also worth doing

- **A baseline behavioural test.** Before the tools exist, assert that the agent
  with no tools produces an empty trajectory. It proves the agent degrades
  gracefully rather than fabricating capabilities, and it is the simplest eval
  case you will ever write.
- **Deterministic fixture data** behind the tools while you are building, so a
  trajectory is reproducible and a failing case means something.

---

Sources: `adk-python: tools/function_tool.py`, `agents/llm_agent.py`,
`apps/app.py`, `flows/llm_flows/functions.py`;
`adk-docs: docs/tools-custom/function-tools.md`, `docs/agents/llm-agents.md`,
`docs/apps/index.md`. The eval-ready framing follows the LinkedIn Learning course
*Evaluating AI Agents with Google ADK*, recommended as background reading.
