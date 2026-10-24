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
| `bidi` | parses, then raises `ValueError("Unsupported streaming mode: ...")` — in record and in test, on 2.9.2 through 2.12.0. This plugin's wrapper refuses it up front (`CNF021`, exit 2) |

Files are written beside a hand-written `spec.yaml`, the only input file the
code reads (it collects `spec.yaml` files recursively and loads each as a
`TestSpec`). **`adk conformance record --help` is stale on every release through
2.11.0:** it still describes `input.yaml` → `test.yaml`, and neither name is read.
2.12.0 rewrote the record help (commit `d56a994`) to name `spec.yaml`, the
`generated-*.yaml` outputs and the streaming-mode argument; `adk conformance
test --help` always said `spec.yaml`. The plugin's wrapper refuses a case with `input.yaml` or
`test.yaml` and no `spec.yaml` (`CNF015`, exit 2) instead of letting ADK skip it.
2.9.2 through 2.12.0 run the same recording and replay code (2.12.0 only adds
UTF-8 to the report writer).

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
`--mode live` is not implemented yet. No flag on `record` or `test` changed
between 2.9.2 and 2.12.0; the help text changed only in 2.12.0's docstring fixes.

**It exits non-zero when a case fails.** On failure the command raises
`click.ClickException("N test(s) failed")`, which click turns into exit 1. That
made it the one command in the ADK eval family you could gate CI on directly —
unlike `adk eval`, which prints failures and still exits 0 through 2.12.0 (from
2.13.0 it exits 1 on a failure, but still cannot gate on a pass rate or a
regression). Say which one a workflow is calling, and on which ADK version,
before you trust its green tick.

### The gap in that gate, closed in 2.11.0

On **2.9.2 and 2.10.0** `_print_test_summary` walks one summary per streaming
mode and **`return`s on the first mode that ran no tests**, so a later mode's
failures are never printed or raised and the command exits 0; it also raises on
the first failing mode only.

**2.11.0** ships the rewrite (`5355c7c6`): every mode is summarized, and the
command fails when any mode failed, when the mode named by `--streaming-mode`
ran nothing, or when no mode ran anything at all. On a 2.11 or 2.12 pin a
single multi-mode run is a sound gate. On 2.9 or 2.10, check that the run reported the
number of tests you expected and prefer one invocation per streaming mode.

### Missing recordings

Two different failures, and only one of them is loud.

**A case directory with no recordings file is skipped.** In replay mode
`cli_test.py` looks for `generated-recordings.yaml` (or
`generated-recordings-sse.yaml` in `sse` mode) and, when it is absent, prints
`Skipping <category>/<name>: no recordings` in yellow and moves on, on every
release through 2.12.0. Skip every case and the mode runs no tests: 2.9.2 and
2.10.0 report "No tests were run." and exit **0** — a conformance job that proves
nothing while looking fine. 2.11.0 and later fail that run, but still skip any single
case that lacks its file while the rest pass. This plugin's wrapper therefore refuses the run itself
with **`CNF020`**, naming each case directory and the file it lacks, before ADK is
invoked; `--skip-recordings-check` runs anyway, for the case where skipping is what
you meant.

**Recordings that never got loaded now raise, new in 2.10.0:**

```
ReplayVerificationError: Replay recordings were not loaded. The ADK web server
must be started with the replay plugin, e.g. `adk web
--extra_plugins=google.adk.cli.plugins.replay_plugin.ReplayPlugin`, for `adk
conformance test` to work.
```

2.9.2 had no such check and compared against nothing. This is about the *server*,
not the files: the fix is the `--extra_plugins` argument, not a re-record.

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

Sources: `adk-python: cli/conformance/` (`cli_record.py`, `cli_test.py`
(`_print_test_summary`, read in the installed 2.10.0, 2.11.0 and 2.12.0 wheels), `test_case.py`
(`TestSpec`, `UserMessage`), `adk_web_server_client.py` (the `127.0.0.1:8000`
default), `_replay_validators.py`, `cli_test.py`'s `Skipping ...: no recordings` branch
(identical on all four wheels), `_conformance_test_google_llm.py` (the
`ReplayVerificationError` quoted above, absent from 2.9.2)),
`cli/cli_tools_click.py` (`_STREAMING_MODE_CHOICES`, the record/test commands),
`cli/agent_test_runner.py`; `adk-docs: docs/evaluate/index.md`. The multi-mode
summary rewrite is `adk-python` commit `5355c7c6`, released in `v2.11.0`;
`--help` compared between the 2.9.2, 2.10.0, 2.11.0 and 2.12.0 wheels; the
2.12.0 record-help rewrite is commit `d56a994`.
