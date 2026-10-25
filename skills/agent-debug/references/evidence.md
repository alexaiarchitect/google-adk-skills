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

**2.13 changed the default schema** to the OpenTelemetry GenAI semantic
conventions ("v2", `otel_semconv_1_44`). One run, as measured on 2.13.0:

```
invoke_workflow claims_agent          gen_ai.workflow.name, gen_ai.conversation.id
  invoke_agent claims_agent
    generate_content <model>          gen_ai.request.model, gen_ai.provider.name, gen_ai.tool.definitions
    execute_tool lookup_policy        gen_ai.tool.name, gen_ai.tool.call.id, gen_ai.tool.type
    generate_content <model>
```

`invoke_workflow` replaces the old `invocation` root, there is **no `call_llm`
span**, and `gen_ai.system` became `gen_ai.provider.name`. Message content is
**off by default**: set `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=SPAN_ONLY`
(or `SPAN_AND_EVENT`) and `generate_content` gains `gen_ai.input.messages`,
`gen_ai.output.messages` and `gen_ai.system_instructions` — the ground truth for
*what the model actually received* — while `execute_tool` gains
`gen_ai.tool.call.arguments` and `gen_ai.tool.call.result`. A value of `true`
sends content to log records only (`EVENT_ONLY`), not to spans.

**Through 2.12, or on 2.13 with `ADK_TELEMETRY_SCHEMA_VERSION_OPT_IN=otel_semconv_1_36`**
(or `1`; supported until at least 2027-03-31), the legacy tree comes back:
`invocation` › `invoke_agent` › `call_llm` › `generate_content`, and
`execute_tool` beside it, with the JSON-string attributes
`gcp.vertex.agent.llm_request`, `.llm_response`, `.tool_call_args` and
`.tool_response`; `ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS` (on by default) controls
them, and empty `"{}"` values mean capture is off, not that something failed.
A dashboard or query written against those names finds nothing under v2 — pin the
opt-in until it is migrated. `adk web`'s Trace tab still shows model content
under v2 (it rebuilds it from log records). This plugin's `analyze_trace.py`
reads `adk run --jsonl` events and result files, not spans, so neither schema
affects it.

Under both: `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` and
`gen_ai.response.finish_reasons`. Read `finish_reasons` rather than inferring from
the text: a truncated response and a refused one look similar in prose and are
different problems.

**Coming after 2.13.0 (unreleased main).** Under schema v2 only, main
(`ad467193e`, `7367645c5`) adds the semconv-genai token metrics: counters
`gen_ai.client.inference.usage.{input_tokens,output_tokens,cache_read.input_tokens,cache_write.input_tokens,reasoning.output_tokens}`
split by `gen_ai.token.modality`, and histograms
`gen_ai.client.inference.operation.{input,output}_tokens`, beside the unchanged
`gen_ai.client.token.usage`; failed calls record 0 under modality `unknown`.
Spans that end in an error, and compaction failures, carry `error.type`
(`a96fc11b5`, `8038bd7f4`), which is the attribute to filter on when a run
failed without a useful message. Through 2.13.0 neither exists.

Export with `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` (install `google-adk[otel]` on
2.13+ for the OTLP exporter), or `adk web --otel_to_cloud` for Cloud Trace.
Third-party sinks read the same spans; ADK's docs link CoreWeave's Agent Lens
(from the Weave integration page), which groups an agent's conversations, turns
and tool calls.

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
| what did the model actually receive? | `gen_ai.input.messages` with content capture on (v2), or the `llm_request` attribute (v1) |
| why did the case fail? | the eval result file |
| does it fail every time? | five runs, then pass@k |
| did this used to work? | conformance recordings, or an older result file |

---

Sources: `adk-python: cli/cli.py` (`_print_event`, `--jsonl`), `events/event.py`
(`node_info`, the camelCase alias generator), `cli/utils/logs.py`
(`log_to_tmp_folder`), `cli/dev_server.py` (trace routes), `telemetry/`,
`flows/llm_flows/functions.py`, `evaluation/eval_result.py`;
`telemetry/_token_usage.py` (`gen_ai.usage.cache_read.input_tokens`, read on
2.11.0); `telemetry/_schema_version.py`, `telemetry/context.py`
(`ContentCapturingMode`), `telemetry/_stable_semconv.py`, read in the installed
2.13.0 wheel, and the span tree measured on 2.12.0 and 2.13.0 with an in-memory
exporter (schema v2 is `adk-python` commit `7838488`, in `v2.13.0`);
`adk-docs: docs/observability/*`, `docs/integrations/weave.md` (Agent Lens,
`cc53b6c14`); main after 2.13.0: the four telemetry commits above, read at
`dfd534a88`. The reasoning-trace / system-trace
distinction is paraphrased from Vlad Kolesnikov, "From 'vibe checks' to
continuous evaluation: engineering reliable AI agents", Google Cloud blog,
2026-02-28,
https://cloud.google.com/blog/topics/developers-practitioners/from-vibe-checks-to-continuous-evaluation-engineering-reliable-ai-agents.
