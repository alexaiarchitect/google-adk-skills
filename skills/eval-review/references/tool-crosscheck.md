# Cross-checking expected calls against the agent

Trajectory matching compares the tool **name** and the **arguments** with what
the agent actually did. So an expected call that the agent could never make is
not a strict test — it is a permanently failing one that says nothing about the
agent.

## Argument nesting

ADK builds a tool's declaration from its signature. A tool whose parameter is a
Pydantic model takes its arguments nested under that parameter's name:

```python
class ClaimRequest(BaseModel):
    claim_id: str = Field(pattern=r"^CLM-[0-9]{6}$")
    cost_centre: Literal["engineering", "sales", "marketing", "operations"]
    amount_usd: float = Field(ge=0.0)

def submit_claim(req: ClaimRequest) -> dict[str, Any]:
    ...
```

```json
{"name": "submit_claim",
 "args": {"req": {"claim_id": "CLM-000412", "cost_centre": "engineering", "amount_usd": 900.0}}}
```

Flat arguments (`{"claim_id": ...}`) will not match, and the parameter name is
part of the contract: rename `req` and every eval case has to change.

A tool with plain parameters is flat:

```python
def lookup_policy(category: str, include_history: bool = False) -> dict: ...
```

```json
{"name": "lookup_policy", "args": {"category": "travel"}}
```

## What gets checked

For each expected call, against the agent's own source:

| Check | Finding |
|---|---|
| the tool exists and is bound to an agent | `EVS036` |
| arguments are nested when the tool takes a model | `EVS039` |
| every required field is present | `EVS042` |
| no argument the tool has no parameter for | `EVS043` |
| values satisfy `Literal` | `EVS044` |
| values satisfy `pattern` | `EVS045` |
| values satisfy `ge` / `le` / `gt` / `lt` | `EVS046` |

Parameters annotated `ToolContext` (or named `tool_context`) are injected by ADK
and are not part of the declaration, so they are never expected in `args`.

## Why the schema checks matter for evaluation

A schema violation in an eval case usually means one of two things, and both are
worth knowing before you run anything:

1. **The case is wrong.** The agent would be right to reject that value, so the
   case tests nothing.
2. **The schema is wrong.** The case describes a real request the schema forbids,
   which is a bug in the agent waiting to be filed.

Either way the answer is a conversation, not a silent edit.

## A note on tools defined elsewhere

Imported tools, `AgentTool`s and MCP toolsets cannot be read statically, so their
calls are reported as unchecked (`EVS038`) rather than as errors. Review those
where they are defined.

---

Sources: `adk-python: tools/function_tool.py`, `tools/tool_context.py`,
`evaluation/trajectory_evaluator.py`, `flows/llm_flows/functions.py`;
`adk-docs: docs/tools-custom/function-tools.md`.
