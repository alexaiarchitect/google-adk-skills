# Live agents: tools that keep talking

A text agent can make the user wait; a voice agent cannot. Under `run_live`, a
tool that blocks for ten seconds is ten seconds of silence, and an instruction
("tell the user you are working on it") does not help, because the model's turn
is stalled until the function response arrives. The fix is in the tool's shape,
not the prompt. This page covers that shape, what it needs from the agent, and
why the evals of a live agent have to run live.

## Live models and the two architectures

Both architectures run under `Runner.run_live()`; switching changes only `model`.

| Architecture | `model=` | Notes |
|---|---|---|
| speech-to-speech (native audio) | `gemini-3.8-live` (GA, AI Studio and Agent Platform) | non-blocking tools and `response_scheduling` supported |
| | `gemini-3.8-live-extended-thinking` (AI Studio, GA) | ADK 2.10+, every tool `behavior = NON_BLOCKING`, `response_scheduling` unset; watch `event.interaction_status` (`IN_PROGRESS`/`IDLE`) instead of `turn_complete` |
| | `gemini-3.1-flash-live-preview` (AI Studio) | no non-blocking tools |
| | `gemini-live-2.5-flash-native-audio` (Agent Platform) / `gemini-2.5-flash-native-audio-preview-12-2025` (AI Studio) | `LlmAgent.DEFAULT_LIVE_MODEL` |
| cascade | `CascadeLive(model="gemini-3.8-flash", stt=GeminiSTT(), tts=GeminiTTS(speech_config="Kore"))` from `google.adk.live` | 2.13.0+, experimental (`CASCADE_LIVE`); any text model; also runs text-only under `run_async` |

The `global` location does not serve Live models. The plugin's model policy
(`/adk:agent-review` `model-policy.md`) applies to these ids like any other.

## Three tool shapes

| The wait | Shape | How |
|---|---|---|
| under a second | blocking (default) | a plain `return` |
| long, nothing to say | non-blocking | `return`-once tool plus `response_scheduling` or `behavior` |
| long, worth narrating | streaming | an `async def` that `yield`s |

Google's voice-agent guide puts the rule in one line: "If the wait has a story,
stream it."

### Streaming tools

```python
import asyncio
from typing import AsyncGenerator

_running: set[str] = set()   # single-flight latch; use a real store in production

async def build_claims_report(cost_centre: str) -> AsyncGenerator[str, None]:
    """Build the monthly claims report for a cost centre.

    Call this ONCE; it pushes its own updates. Do not call it again to poll.
    """
    if cost_centre in _running:
        yield "DONE: already running; wait for the result."
        return
    _running.add(cost_centre)
    try:
        yield "PROGRESS: reading this month's claims"
        rows = await fetch_claims(cost_centre)
        yield "PROGRESS: totalling by category"
        yield f"DONE: {summarise(rows)}"
    finally:
        _running.discard(cost_centre)


def stop_streaming(function_name: str):
    """Stop a running streaming tool.

    Args:
      function_name: The name of the streaming function to stop.
    """


root_agent = Agent(name="claims_voice", model="gemini-3.8-live",
                   instruction=INSTRUCTION,
                   tools=[build_claims_report, FunctionTool(stop_streaming)])
```

- Under `run_live`, ADK marks every async-generator tool `NON_BLOCKING`, sends
  the model an immediate acknowledgement, and turns each `yield` into a partial
  function response the model can paraphrase. Prefixes such as `PROGRESS:` and
  `DONE:` tell it which update is final.
- **Single flight.** Models re-invoke tools. Say "call this ONCE" in the
  docstring and back it with a latch or an idempotency key, as above.
- **`stop_streaming`** is reserved: ADK intercepts it by name, cancels the
  generator's task and raises `asyncio.CancelledError` inside it, so put cleanup
  in `finally`. Without it the user cannot cancel ("never mind").
- **Barge-in does not cancel tools.** An interruption stops the model's audio
  and emits an event with `interrupted=True` (stop playback client-side), but a
  running streaming or background tool keeps going and its result still lands.
  Only `stop_streaming` stops it.
- **Video:** an `input_stream: LiveRequestQueue` parameter makes ADK feed the
  user's realtime input into the tool.

### Non-blocking `return`-once tools

```python
claims_tool = FunctionTool(export_claims)
claims_tool.response_scheduling = types.FunctionResponseScheduling.WHEN_IDLE
# or, 2.10+: claims_tool.behavior = types.Behavior.NON_BLOCKING  (takes precedence)
```

| `response_scheduling` | The result... | Use for |
|---|---|---|
| `WHEN_IDLE` | waits until the model and the user pause | reports, lookups — the usual choice |
| `INTERRUPT` | cuts into the current speech | failures, alarms |
| `SILENT` | enters context without an announcement | background facts for later turns |

A return-once tool gives the model nothing until it finishes, so the agent often
says nothing at the start; a single-`yield` generator gets the immediate
acknowledgement instead. Scheduling and shape are independent: set on a
streaming tool, `response_scheduling` applies to every update, and from 2.8 a
streaming tool can schedule each update itself by yielding
`types.FunctionResponse(response={...}, scheduling=types.FunctionResponseScheduling.SILENT)`.
`response_scheduling` dates from 2.4 and support is per model (table above).

**Version notes.** `behavior` on `FunctionTool` is 2.10+ (absent on 2.9.2);
`event.interaction_status` and per-update scheduling are 2.8+; `CascadeLive`
is 2.13.0+. Pin `google-adk>=2.10` for a live agent that uses either
non-blocking form.

## Streaming tools off the live path (`AGT081`)

The `NON_BLOCKING` marking happens only when the invocation has a live request
queue, that is only under `run_live`. `adk run`, `adk eval` without
`live_model_config`, `AgentEvaluator` without it, and the text mode of `adk web`
all use `run_async`, where an async-generator tool is run once as an ordinary
function (measured, fake model):

| Version | Two-`yield` tool under `run_async` |
|---|---|
| 2.12.0, 2.13.0 | the run aborts: `DynamicNodeFailError` from `ValueError: Output already set. A node can produce at most one output.` A one-`yield` tool returns that value. |
| 2.9.2 | silent: the response is the generator object's repr, the body never runs, the model answers without the data |
| either | a tool with `input_stream: LiveRequestQueue` raises `TypeError: ... missing 1 required positional argument: 'input_stream'` |

`lint_agent.py` reports `AGT081`: a **warn** when an async-generator tool sits on
an agent whose model is not live (no `live`/`native-audio` in the id, no
`CascadeLive(...)`), and an **info** when the agent is live but its eval configs
have no `live_model_config`, so every eval runs the tool off the path it was
written for.

## Evaluating a live agent

- Put `live_model_config` in the eval config (`/adk:eval-simulation`
  `user-simulation.md`): it is what makes the eval drive `run_live`, and the
  only way the streaming and scheduling behaviour above gets exercised.
- Grade **order and restraint**, not update counts: an acknowledgement before
  results, the final figures matching the `DONE` update, no second call to a
  single-flight tool (`rubric_based_tool_use_quality_v1`, threshold 1.0), and
  `stop_streaming` called when the user cancels.
- Unit-test the generator itself with plain `async for`: the latch, the
  `finally` cleanup on `CancelledError`, and the `DONE` payload need no model.
- A cascade agent can also be evaluated cheaply in text: under `run_async` it
  uses its inner model directly — but its streaming tools are then off the live
  path, with the consequences in the table.

---

Sources: `adk-python: flows/llm_flows/tools/_agent_tools.py`
(`mark_live_async_tools_non_blocking`, called only with a live request queue),
`tools/function_tool.py` (`_should_run_generator_as_node`, `behavior`),
`agents/context.py` (the single-output `ValueError`), `live/` (`CascadeLive`),
`evaluation/evaluation_generator.py` (`run_live` under `live_model_config`), read
in the 2.13.0 wheel; the `run_async` behaviour measured on 2.9.2 and 2.13.0 with
a fake model (the generator-as-node path first shipped in 2.12.0, `0ee891a53`).
`adk-docs: docs/live/tools.md`, `docs/live/models.md`, `docs/live/events.md`,
`docs/live/architectures/{index,cascade}.md` at `49c928f1e`. Streaming tools,
the docstring and latch, `stop_streaming`, barge-in, scheduling modes and the
2.8/2.10 notes are paraphrased from Allen and Sato, "Step by Step guide: Build a
Voice Agent That Never Goes Silent using ADK", Google Developers Blog,
2026-10-19,
https://developers.googleblog.com/step-by-step-guide-build-a-voice-agent-that-never-goes-silent-using-adk/
(the one quoted line is theirs).
