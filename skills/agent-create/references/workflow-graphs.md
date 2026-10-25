# Graph workflows, and what they change about evaluation

A prompted agent is asked to follow an order of operations. A graph workflow
*is* the order of operations. That difference is the reason to reach for one,
and it changes what the eval suite has to prove: a step enforced by an edge
needs no eval case to defend it, because the model was never given the chance to
skip it.

The trade is real, though. A graph is harder to change than a paragraph, and an
`LlmAgent` elsewhere in the tree cannot `transfer_to_agent` into a workflow
node. Use a graph where the sequence is a rule (policy check before spend,
validation before write, approval before submission) and a prompted agent where
the sequence is a preference.

## The API surface

`google.adk.workflow` exports exactly these:

| Name | What it is |
|---|---|
| `Workflow` | the graph itself; takes `name` and `edges` |
| `Node` | a graph node's base configuration |
| `BaseNode` | what `START` is an instance of, and what edges connect |
| `FunctionNode` | wraps a plain function as a node |
| `JoinNode` | waits for several branches before continuing |
| `Edge` | an explicit `from_node` / `to_node` / `route` triple |
| `START` | the entry node |
| `DEFAULT_ROUTE` | the route taken when no other matches (`"__DEFAULT__"`) |
| `RetryConfig` | per-node retry policy |
| `node` | decorator form of `FunctionNode` |
| `NodeTimeoutError` | raised when a node exceeds its `timeout` |

A minimal graph, which is what `scaffold_agent.py --workflow` writes:

```python
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.workflow import START, FunctionNode, Workflow

def normalise_request(node_input: str) -> dict:
    return {"request": (node_input or "").strip()}

def apply_policy(node_input: dict) -> dict:
    return {**node_input, "allowed": True}

reporter = Agent(name="reporter", model=MODEL, instruction=INSTRUCTION, tools=[...])

workflow = Workflow(
    name="claims_agent",
    edges=[(START, FunctionNode(func=normalise_request),
                   FunctionNode(func=apply_policy), reporter)],
)

app = App(name="claims_agent", root_agent=workflow)
```

Each row of `edges` is a chain run in order. A node's return value becomes the
next node's input, so nothing has to be passed through session state — which is
also why a graph is easier to reason about than a set of agents writing to
shared keys. The `@node` decorator is the same thing with the wrapping implicit,
and it takes the per-node settings (`retry_config`, `timeout`,
`rerun_on_resume`, `parameter_binding`).

## Three facts that will bite

**`Workflow` is not a `BaseAgent`.** It is its own Pydantic type.
`App.root_agent` is annotated loosely enough to accept it, which is how the
loader finds it through the `app` export — but anything that expects a
`BaseAgent` (an `AgentTool`, a `sub_agents` list, `transfer_to_agent`) will not
take one. Keep `root_agent = workflow` as an alias if a package `__init__`
re-exports it, but do not assume agent APIs apply.

**One output per node.** A node produces its successor's input once. Emitting a
second `Event.output` from the same node is a runtime error, not a merge, so
build the whole result and return it in one go. `lint_agent.py` checks for a
node body that assigns its output twice.

**One terminal output per workflow.** If several terminal nodes (no outgoing
edge) produce output, finalization raises `WorkflowConfigurationError("Workflow
<name>: multiple terminal nodes produced output (N). ...")`. Route to a single
terminal node, or join first. Everything in `event.output` must be
JSON-serializable.

**`mode='chat'` agents belong after `START`.** An agent node that expects to talk
to a user needs the user's turn as its input. Placed deeper in a chain, its
input is whatever the previous node returned, which is not a conversation.

**Confirmation on a function node (2.12).** `@node(require_confirmation=True)`
(or a predicate), `build_node(...)` and `FunctionNode(require_confirmation=...)`
pause for a yes/no before the function runs, like
`FunctionTool(require_confirmation=...)`: approval runs it once; rejection skips
it and emits `{"error": "This tool call is rejected."}`. `@node` then defaults
`rerun_on_resume=True`; `FunctionNode` raises `WorkflowConfigurationError` with
`rerun_on_resume=False`. Absent on 2.9–2.11. Eval both branches: one case
approves, one rejects and expects no side effect.

## Evaluating a graph

Nothing about the evalset schema changes — the cases still assert a trajectory
of tool calls with their arguments, because the tools bound to an `Agent` node
are called the same way. What changes is *which* cases you need:

- **Drop the ordering cases the graph now guarantees.** A case whose only
  purpose was proving that `lookup_policy` runs before `submit_claim` is testing
  the framework once an edge enforces it. Delete it deliberately, and say why in
  the commit.
- **Keep every case about the agent node's judgement.** Refusals, phrasing,
  argument values and anything the model still decides are exactly as untrusted
  as before.
- **Add cases for the function nodes' branches.** They are ordinary Python, so
  they are better covered by ordinary unit tests than by an eval case — an eval
  case that exists only to exercise deterministic code is slow and expensive for
  no extra information.

When a graph run misbehaves, `adk run --jsonl` tells you which node produced
each event: the key is `nodeInfo.path`. That is the first thing to read, because
"the model did the wrong thing" and "the wrong node ran" look identical in the
text output. `/adk:agent-debug`'s `run_jsonl.py` captures the trace and
`analyze_trace.py` reads it.

## Remote steps (A2A) and the fail-safe state

A step owned by another team or another language can run as a remote service
over the Agent2Agent protocol. `RemoteA2aAgent(name=..., description=...,
agent_card=<card object, URL or file>)` (`google.adk.agents.remote_a2a_agent`,
`pip install "google-adk[a2a]"`, marked experimental) wraps it as a local
sub-agent; the card is served at `/.well-known/agent.json`, and `timeout`
defaults to 600 s. Google's cross-language contract-review example puts one
between a Gemini extraction agent and a report agent, with a Go validator that
uses no model at all. Four habits from it make such a pipeline evaluable:

- **An explicit step enum in state** (`INGESTED`, `EXTRACTED`,
  `COMPLIANCE_PENDING`, ..., `MANUAL_REVIEW`, `APPROVED`), written by each step,
  so a case can assert where the run stopped, not only what it said.
- **An unreachable remote is a state, not a crash.** On 2.13.0 a failed call
  does not raise: `RemoteA2aAgent` yields an event whose `error_message` starts
  `A2A request failed:` (metadata `a2a:error`), and a sequential pipeline runs
  the next step anyway. Write the hold yourself — a node or callback that sees
  the error or the missing result, checkpoints state and sets `MANUAL_REVIEW` —
  and keep the remote's timeout below your own run budget.
- **Deterministic validators are audited by repetition.** Identical inputs must
  give identical verdicts; test that with unit tests, not judged cases.
- **Narrow agents are easier to evaluate** than one agent holding every tool:
  each gets its own suite, and the remote is replaced by a fixture in the
  others. Fault-injection cases (remote delayed, remote crashed → expected
  hold) are in `/adk:eval-create` `case-taxonomy.md`.

## Choosing between a graph and the deprecated workflow agents

`SequentialAgent`, `ParallelAgent` and `LoopAgent` still exist and are
deprecated. A graph does everything they do, with explicit routing, per-node
retries and typed hand-offs. The one reason to keep a prebuilt workflow agent is
that it *is* a `BaseAgent`, so an `LlmAgent` can transfer into it — which a
`Workflow` cannot offer. If that transfer is not needed, prefer the graph.

---

Sources: `adk-python/src/google/adk/workflow/__init__.py` (the exported names);
`adk-python/src/google/adk/workflow/_workflow.py` and `_graph.py` (`Workflow`
fields, `Edge`); `adk-python/src/google/adk/workflow/_function_node.py`
(`FunctionNode`, `node`); `adk-python/src/google/adk/apps/app.py`
(`App.root_agent`); `adk-python/contributing/samples/workflows/use_as_output/
agent.py` (the `edges=[(START, ...)]` form); `adk-docs/docs/graphs/index.md` and
`adk-docs/docs/graphs/data-handling.md` (node output, the single-output rule,
routing); `workflow/_workflow.py` (terminal-output finalization),
`workflow/_errors.py`, `workflow/_function_node.py`, `workflow/_node.py`
(`require_confirmation`, `988fd1a`, in `v2.12.0`); `a2a/agent/_remote_a2a_agent.py`
(`AGENT_CARD_WELL_KNOWN_PATH`, `DEFAULT_TIMEOUT`, the `A2A request failed`
error events) and `agents/sequential_agent.py`, read in the 2.13.0 wheel.
Remote steps: Saboo and Dong, "Build Cross-Language Multi-Agent Team with
Google's Agent Development Kit and A2A", Google Developers Blog, 2026-06-22,
https://developers.googleblog.com/en/build-cross-language-multi-agent-team-with-google-agent-development-kit-and-a2a/.
Background reading: the LinkedIn Learning course "Evaluating AI Agents
with Google ADK", for the evaluation methodology these cases follow.
