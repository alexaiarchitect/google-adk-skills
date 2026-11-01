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

**Retries and timeouts** (adk-docs `graphs/retry.md`, Python since 2.0.0). `RetryConfig` defaults to 5 attempts counting the first (0 or 1 disables retries), a 1 s first wait, 60 s at most, doubling each time, jitter 1.0, and every error retried unless `exceptions` names classes (subclasses match). `timeout` is per attempt, not for the whole node; a timed-out attempt is retried only when the policy covers `NodeTimeoutError`, so list it whenever you list exceptions, and without a policy a timeout fails the node at once. The attempt count is not saved: after a human-in-the-loop pause or a restart the node starts again at attempt 1. Retry only transient errors, on the node that calls the flaky service, and keep that node idempotent.

**Throttling an agent worker.** `parallel_worker=True` runs a node once per item of a list input; on an `LlmAgent` node it exists since 2.0.0, and `@node(...)` / `Node(...)` accept `max_parallel_workers=N` since 2.4.0. `LlmAgent` has no `max_parallel_workers` field through 2.13.0: any value, even `None`, fails with `Extra inputs are not permitted`, so an agent worker cannot be throttled there. Main after 2.13.0 (`5e4f0e18c`) adds it (at least 1, only with `parallel_worker=True`). `lint_agent.py` reports misuse as `AGT085`.

**`mode='chat'` agents belong after `START`.** An agent node that expects to talk
to a user needs the user's turn as its input. Placed deeper in a chain, its
input is whatever the previous node returned, which is not a conversation.

**Confirmation on a function node (2.12).** `@node(require_confirmation=True)` (or a predicate), `build_node(...)` and `FunctionNode(require_confirmation=...)` pause for a yes/no before the function runs, like `FunctionTool(require_confirmation=...)`: approval runs it once; rejection skips it and emits `{"error": "This tool call is rejected."}`. `@node` then defaults `rerun_on_resume=True`; `FunctionNode` raises `WorkflowConfigurationError` with `rerun_on_resume=False`. Absent on 2.9–2.11. Eval both branches: one case approves, one rejects and expects no side effect.

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

A step owned by another team or another language can run as a remote service over the Agent2Agent protocol. `RemoteA2aAgent(name=..., description=..., agent_card=<card object, URL or file>)` (`google.adk.agents.remote_a2a_agent`, `pip install "google-adk[a2a]"`; experimental through 2.13.0, see below) wraps it as a local sub-agent; the card is served at `/.well-known/agent.json`, and `timeout` defaults to 600 s. Google's cross-language contract-review example puts one between a Gemini extraction agent and a report agent, with a Go validator that uses no model at all. Four habits from it make such a pipeline evaluable:

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

**Coming after 2.13.0 (unreleased main, checked at `ee8720162`).** Three A2A changes matter to a pipeline like this. `adk generate_agent_card` (`875e2aef7`, needs `google-adk[a2a]`) builds each app's card from the agent itself, run from the agents directory: it prints the cards as JSON with the RPC URL `http://127.0.0.1:8000/a2a/<app>` unless `--protocol`, `--host` and `--port` say otherwise, and `--create-file` writes `<app>/agent.json`, so the card you serve can be generated instead of hand-kept (`/adk:eval-run` `cli-reference.md`). Cancelling an A2A task now reaches the running invocation (`14765ab2b`, `abd8f0f69`): the remote's tools and model calls stop, dangling tool calls are sealed with `INVOCATION_ABORTED`, and the task reports `CANCELED` instead of `FAILED`; through 2.13.0 the work kept running after the cancel, so a fault case that cancels a remote step should expect `FAILED` there. And `RemoteA2aAgent` always sends the `A2A-Version` header from its own HTTP client (`e44b31675`): with a client factory that has no HTTP client of its own and a2a-sdk 1.3, released versions can send no header, which an A2A v1 server reads as protocol 0.3 and rejects.

**Checked again at `70e1cbde7` (2026-11-04), also main only.** A node that delegates its output (`ctx.run_node(..., use_as_output=True)`) now forwards the parent event's text, images, errors and approval requests with only the duplicate output cleared (`efec445a4`); through 2.13.0 that event was dropped, so a workflow could wait on an approval the caller never saw — an approval case through such a node can hang on 2.13.0 and pass on main. Two resume fixes: a slow rerun node no longer makes its replayed siblings wait out the barrier timeout (`3565ad3dd`), and resuming a session stored by ADK 2.10 or older strips a Markdown code fence from a schema reply before parsing it instead of failing with `WorkflowDataError` (`f4fa73e80`).

**Checked again at `175682204` (2026-11-05), also main only.** A2A graduates (`95d4a5660`): through 2.13.0 constructing `RemoteA2aAgent`, `A2aAgentExecutor` and the other A2A classes warns `[EXPERIMENTAL] RemoteA2aAgent: ADK Implementation for A2A support ...` (a `UserWarning` on instantiation, not import; `ADK_SUPPRESS_A2A_EXPERIMENTAL_FEATURE_WARNINGS=1` silences it); main drops the warning. An A2A task now completes when the run recovers from an error inside it (`512d56b22`); through 2.13.0 an error event inside the run could fail the task even after recovery, so a fault case whose remote retries and succeeds can read `FAILED` there. A workflow used as a tool by a single-turn sub-agent keeps the delegation pending while it waits for input (`601281049`); through 2.13.0 the delegator got `{'result': None}` and the answer never resumed the workflow. A single-turn agent node now sees its own `node_input` as `user_content`, not the parent's message (`175682204`).

## Routing with a classifier (labs)

On 2.13.0, `google.adk.labs.classifiers.ClassifierNode` (experimental) routes a
graph by asking one `BaseClassifier` one question — a `Binary` (route `True` /
`False` at `threshold`, default 0.5) or a `Choice` (route = label; graph
validation checks every label has an edge). `LlmClassifier` asks a model;
`google.adk.integrations.typesafe.TypesafeClassifier` calls TypeSafe's hosted Jev
models (`TYPESAFE_API_KEY`), which return calibrated probabilities; main adds
`OpenAIDecisionsClassifier` and per-request labels (`app-and-plugins.md`). The
usual job is a cheap model in front of an expensive one. Lessons worth taking
from Google's own write-up of exactly that (Villani, below):

- **Ask atomic yes/no questions, one per factor** (needs a lookup? spans several
  files? ambiguous?) rather than one broad "simple or complex" choice, which
  tends to send nearly everything down one path. In ADK that is a chain of
  `Binary` nodes, each with its own edge.
- **Hold out prompts before trusting a router.** A keyword or length heuristic
  fits the prompts it was tuned on and falls apart on new ones; give the router
  its own eval set with cases it never saw (`/adk:eval-create` `case-taxonomy.md`).
- **Measure cost per successful task**, everything spent (loop turns, retries,
  thinking) divided by the cases that pass, not price per token.
- **Route short, self-contained turns only.** A context cache belongs to one
  model, so switching models mid-conversation loses or duplicates it; keep long,
  context-heavy turns on one model. And try one fast model at a low thinking
  level first: a router is worth building only if that baseline fails.

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
error events) and `agents/sequential_agent.py`, read in the 2.13.0 wheel;
`cli/_cli_generate_agent_card.py`, `a2a/executor/` and `a2a/_compat.py` on main
`ee8720162` (`875e2aef7`, `14765ab2b`, `abd8f0f69`, `e44b31675`, all post-tag).
Main at `70e1cbde7`: `workflow/` (`efec445a4`, `3565ad3dd`, `f4fa73e80`), post-tag. Main at `175682204`: `a2a/` (`95d4a5660`, `512d56b22`), `agents/llm_agent.py` (`5e4f0e18c`), `workflow/` (`601281049`, `175682204`), post-tag; the A2A warning, the `max_parallel_workers` rejection and `parallel_worker=True` measured on 2.9.2 and 2.13.0. Retries: `adk-docs/docs/graphs/retry.md` (`24c196a1b`).
Classifiers: `labs/classifiers/_classifier_node.py`, `__init__.py` and
`integrations/typesafe/` read in the 2.13.0 wheel; `OpenAIDecisionsClassifier`
(`aa98d180b`) is post-tag. Villani, "Cost per task: Using cheap classifiers to
route across Gemini models and thinking levels", Google Developers Blog,
2026-11-03,
https://developers.googleblog.com/cost-per-task-using-cheap-classifiers-to-route-across-gemini-models-and-thinking-levels/
(paraphrased; no figures reproduced).
Remote steps: Saboo and Dong, "Build Cross-Language Multi-Agent Team with
Google's Agent Development Kit and A2A", Google Developers Blog, 2026-06-22,
https://developers.googleblog.com/en/build-cross-language-multi-agent-team-with-google-agent-development-kit-and-a2a/.
Background reading: the LinkedIn Learning course "Evaluating AI Agents
with Google ADK", for the evaluation methodology these cases follow.
