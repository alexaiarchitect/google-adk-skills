# Conformance testing

## What it is, and how it differs from `adk eval`

`adk eval` asks whether the agent produced the expected tool calls and answer.
**Conformance** asks whether the agent's behaviour is what it was when you
recorded it: the same requests to the model, the same tool calls, in the same
order.

Different instrument, different question. Eval is correctness against a
specification. Conformance is drift after a refactor, a dependency bump, or an
instruction rewrite that was supposed to change nothing.

## Recording

Recording does not run the agent by itself: it drives a **running dev server**
over HTTP. Start one and leave it up.

```bash
# terminal 1 - must end up listening on 127.0.0.1:8000
adk web <agents-dir> -v \
    --extra_plugins=google.adk.cli.plugins.recordings_plugin.RecordingsPlugin

# terminal 2 - run the interaction in the UI first, then:
adk conformance record tests/conformance/<category>/<case> none
```

`AdkWebServerClient` hardcodes `http://127.0.0.1:8000`, so a server on another
port is not found and the command fails with a connection error rather than a
useful message.

The last argument is the **streaming mode, positional**, not a flag. The choices
come from `StreamingMode`, matched case-insensitively:

| Mode | What happens |
|---|---|
| `none` | records `generated-session.yaml` and `generated-recordings.yaml` |
| `sse` | records the `-sse.yaml` variants of both |
| `bidi` | parses, then raises `ValueError("Unsupported streaming mode: ...")` — in record and in test |

Files are written beside a hand-written `spec.yaml`:

```yaml
description: Approves a travel claim within policy.
agent: app
initial_state: {}
user_messages:
  - text: "Submit claim CLM-000412 for 900 dollars of travel on the engineering cost centre."
```

**`user_messages` entries are objects, not strings.** A bare string is rejected
(*"Input should be a valid dictionary or instance of UserMessage"*). Each takes
`text`, or a full `content` (for a function response or a non-text part), plus an
optional `state_delta`. `TestSpec` is `extra="forbid"`, so a misspelled key is an
error rather than a silent no-op — the opposite of the eval-config rule, and
worth remembering when you move between the two.

## Replaying

```bash
adk conformance test tests/conformance \
    [--mode replay|live] [--generate_report] [--report_dir reports/] [--streaming-mode sse]
```

Replay compares the live LLM requests and tool calls against the recording.
`--mode live` is not implemented yet.

**It exits non-zero when a case fails.** On failure the command raises
`click.ClickException("N test(s) failed")`, which click turns into exit 1. That
makes it the one command in the ADK eval family you can gate CI on directly —
unlike `adk eval`, which prints failures and still exits 0. Say which one a
workflow is calling before you trust its green tick.

## Where the files go

```
tests/conformance/<category>/<case>/
    spec.yaml
    generated-recordings.yaml
    generated-session.yaml
```

Under `tests/conformance/`, not loose in `tests/` — `adk test` collects
`tests/*.json` and will pick up strays.

## When it earns its place

**Use it for**: proving a refactor changed nothing; catching a prompt edit that
quietly altered every request; pinning an agent nobody is actively developing.

**Do not use it for**: correctness — an eval case says what *should* happen, a
recording only says what *did* — or for anything you expect to change often. A
recording regenerated on every commit is noise with a maintenance cost.

Conformance is strict by design. That is both its value and its price.

## Alongside evals

| Tier | What runs |
|---|---|
| per change | deterministic evals; conformance replay if the agent is stable |
| nightly | judge metrics, groundedness, safety, repeated runs for pass@k |
| release | the above plus 20-50 runs for a reliability estimate |

---

Sources: `adk-python: cli/conformance/` (`cli_record.py`, `cli_test.py`,
`test_case.py` (`TestSpec`, `UserMessage`), `adk_web_server_client.py` (the
`127.0.0.1:8000` default), `_replay_validators.py`), `cli/cli_tools_click.py`
(`_STREAMING_MODE_CHOICES`, the record/test commands), `cli/agent_test_runner.py`;
`adk-docs: docs/evaluate/index.md`.
