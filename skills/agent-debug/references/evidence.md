# Where the evidence is

## `adk run --jsonl`

```bash
adk run <agent-dir> --jsonl "the request that fails" > /tmp/run.jsonl
```

One JSON event per line, with aliased (camelCase) keys. **Without `--jsonl` the
command prints text parts only**, so tool calls and tool errors are invisible —
which is why a "the agent did nothing" report so often turns out to be a tool
that returned an error nobody saw.

Useful fields per event: `author`, `invocationId`, `content.parts[].functionCall`,
`content.parts[].functionResponse`, `content.parts[].text`, and the node path in
a workflow.

**Read the node path from `nodeInfo.path`.** The event is dumped with
`by_alias=True`, so the nested `node_info.path` field serializes camelCase like
everything else. `adk run --jsonl` then *also* injects a flat top-level
`node_path` as a convenience, and orders it near the front for readability — but
only when the path is non-empty, and only from this one code path. Anything else
that serializes an `Event` (the dev-UI trace routes, a session file, an eval
result) gives you `nodeInfo.path` alone. So: read `nodeInfo.path`, and treat
`node_path` as a fallback rather than the other way round.

Other flags worth knowing: `--save_session` writes the session for later capture,
`--replay` and `--resume` take a session file back in.

Logs go to `$TMPDIR/agents_log/agent.latest.log`, not the terminal; `adk web`
does the opposite. The directory is `tempfile.gettempdir()`, so it is platform
dependent — `/tmp` on Linux, a private per-user directory on macOS, and the
`.latest.log` part is a symlink to a timestamped file. Do not guess it: ADK
prints `Log setup complete: <path>` and `To access latest log: tail -F <path>`
at startup, and if the symlink could not be created the second line names the
real file instead.

## A tool failure does not raise

ADK converts a missing argument, a rejected confirmation or a tool-side failure
into a **function response carrying an `error` key**, and the run continues. So:

- the agent looks like it ignored the tool;
- the final answer is often still plausible;
- nothing in the terminal says anything went wrong.

Look for `functionResponse` payloads with `error`, or a `status` of `"error"`.
`analyze_trace.py` lists them, deduplicated.

## The dev UI

```bash
adk web <agents-dir>
```

The **Trace** tab groups spans by user message; clicking a row shows the event,
the request, the response and the graph. The **Eval** tab runs cases and shows
actual against expected.

Trace endpoints, if you want them programmatically:

```
GET /dev/apps/{app}/debug/trace/session/{session_id}
GET /dev/apps/{app}/debug/trace/{event_id}
```

Spans are **in memory only** — restarting the server loses them. Only `adk web`
registers these routes; `adk api_server` returns 404.

## Spans and what they carry

Under the current OpenTelemetry schema: `invoke_agent {name}`,
`invoke_workflow {name}`, `execute_tool {name}`, `generate_content {model}`.

Attributes worth reading: `gcp.vertex.agent.llm_request` and `.llm_response`
(JSON strings — the ground truth for *what the model actually received*),
`.tool_call_args`, `.tool_response`, plus `gen_ai.usage.input_tokens`,
`gen_ai.usage.output_tokens` and `gen_ai.response.finish_reasons`.

Read `finish_reasons` rather than inferring from the text: a truncated response
and a refused one look similar in prose and are different problems.

Content capture is controlled by `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS`. Empty
`"{}"` attributes mean capture is off, not that something failed.

Export with `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`, or `adk web --otel_to_cloud`.

## Eval results

`<agent_dir>/.adk/eval_history/*.evalset_result.json` holds, per case, both the
**actual** and the **expected** invocation — which is what makes a divergence
point computable after the fact, without re-running anything.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/analyze_trace.py --result <agent-dir>/.adk/eval_history --eval-id <id>
```

## Reasoning trace versus system trace

Two different records of the same run, answering different questions:

| | Reasoning trace | System trace |
|---|---|---|
| What it is | the agent's decisions: model turns, function calls with their arguments, function responses, transfers, node paths | the infrastructure's view: OpenTelemetry spans with timings, token counts, finish reasons, the exact request sent to the model |
| Question it answers | *did it do the right thing, in the right order, with the right values?* | *why was it slow, expensive, truncated, or fed the wrong context?* |
| Where it lives in ADK | `adk run --jsonl` events; the session (`--save_session`); `actual_invocation` in `.adk/eval_history/*.evalset_result.json` | spans from `adk web`'s Trace tab and the `/debug/trace` routes, an OTLP exporter, or Cloud Trace via `adk web --otel_to_cloud` |
| What reads it | trajectory metrics, rubric judges, `analyze_trace.py` | you, and whatever latency or cost dashboard the exporter feeds (the eval efficiency metrics read the events' `usage_metadata`, not spans) |

Start with the reasoning trace: most agent failures are decisions, and the
divergence point is there. Open the system trace when the decisions look right
and the outcome is still wrong — a `MAX_TOKENS` finish, a request missing the
instruction you thought it had, a call that took forty seconds — or when the
question is cost. Prompt tokens served from a context cache show on the
`generate_content` span as `gen_ai.usage.cache_read.input_tokens`, and on the
event as `usageMetadata.cachedContentTokenCount`.

## Which to reach for

| Question | Source |
|---|---|
| what did the agent do? | `adk run --jsonl` |
| did a tool fail? | the function responses in that trace |
| what did the model actually receive? | the `llm_request` span attribute |
| why did the case fail? | the eval result file |
| does it fail every time? | five runs, then pass@k |
| did this used to work? | conformance recordings, or an older result file |

---

Sources: `adk-python: cli/cli.py` (`_print_event`, `--jsonl`), `events/event.py`
(`node_info`, the camelCase alias generator), `cli/utils/logs.py`
(`log_to_tmp_folder`), `cli/dev_server.py` (trace routes), `telemetry/`,
`flows/llm_flows/functions.py`, `evaluation/eval_result.py`;
`telemetry/_token_usage.py` (`gen_ai.usage.cache_read.input_tokens`, read on
2.11.0); `adk-docs: docs/observability/*`. The reasoning-trace / system-trace
distinction is paraphrased from Vlad Kolesnikov, "From 'vibe checks' to
continuous evaluation: engineering reliable AI agents", Google Cloud blog,
2026-02-28,
https://cloud.google.com/blog/topics/developers-practitioners/from-vibe-checks-to-continuous-evaluation-engineering-reliable-ai-agents.
