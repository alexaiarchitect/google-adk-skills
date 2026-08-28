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
`adk-docs: docs/observability/*`.
