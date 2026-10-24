# User simulation

## What it changes

A scripted case fixes the user's turns. A scenario case fixes the user's
**intent** and lets an LLM produce the turns:

```json
"conversation_scenario": {
  "starting_prompt": "I need to claim some expenses from last week's trip.",
  "conversation_plan": "Say you work in engineering. Ask about a 900 dollar flight, then a 40 dollar meal, without repeating the cost centre. Confirm both outcomes, then stop.",
  "user_persona": "NOVICE"
}
```

A case has either a `conversation` or a `conversation_scenario`, never both.

What that buys: phrasings nobody scripted, follow-ups nobody anticipated, and the
ordinary human behaviour of stating something once and expecting it to be
remembered. What it costs: the reference.

There is no fixed expected invocation to compare against, so ADK returns
`NOT_EVALUATED` for reference-based metrics here, and raises for the ones that
require an expected invocation outright. Nothing in the schema forbids the
combination — **this plugin refuses it up front**, as its own rule, so you are
told when you write the config instead of after a paid run comes back blank.

## Writing the plan

The plan is the specification, so write behaviour rather than topic.

```
Bad:  Ask about expenses.
Good: Say you work in engineering. Ask about a 900 dollar flight, then a 40 dollar
      meal, without repeating the cost centre. If the agent asks for it again,
      say you already told it. Confirm both outcomes, then stop.
```

Three things a good plan states:

1. **What the user reveals, and when.** This is how a memory test gets built
   without scripting turns.
2. **What they do not repeat.** The interesting failures live here.
3. **When they stop.** Otherwise the conversation runs until the cap.

## The simulator config

```json
"user_simulator_config": {
  "type": "llm_backed",
  "model": "gemini-flash-latest",
  "max_allowed_invocations": 8,
  "include_function_calls": false
}
```

| Field | Notes |
|---|---|
| `type` | `llm_backed`, or `llm_audio` for voice; a missing type is back-filled to `llm_backed` |
| `model` | set it: the default is a deprecated id |
| `max_allowed_invocations` | the cap. `-1` removes it, which is rarely what you want |
| `include_function_calls` | whether the simulated user sees the agent's tool calls. Usually false: real users do not |
| `custom_instructions` | a Jinja template replacing the default simulator prompt |

A custom prompt **must** keep `{{ stop_signal }}`, `{{ conversation_plan }}` and
`{{ conversation_history }}`, with `{{ persona }}` optional. Without them the
simulator cannot see the plan or know when to stop.

## Which metrics work

| Works | Does not |
|---|---|
| the three `rubric_based_*` metrics | `tool_trajectory_avg_score` |
| `hallucinations_v1` | `response_match_score` |
| `safety_v1` | `final_response_match_v2` |
| `multi_turn_*` (Vertex) | `response_evaluation_score` |
| `per_turn_user_simulator_quality_v1` | |

The rule is simple: if a metric needs something the eval case declared in
advance — an expected trajectory or an expected answer — it cannot score a
conversation that was invented at runtime.

**Use two configs.** One deterministic, for the scripted cases; one for the
simulated ones. Sharing a config means half the metrics quietly apply to half the
cases, which reads as a passing suite that is testing less than you think.

## Rubrics that suit simulated runs

Since there is no reference, the rubrics carry the assertion. The ones that earn
their place:

```
The agent uses a value the user gave earlier in the conversation instead of
asking for it again.

The agent does NOT call `submit_claim` when the cost centre is frozen.

When the agent declines, its response names the specific constraint that blocked
the request.
```

All three are true-or-false about a transcript, whatever words the simulated user
chose.

## Generating scenarios

```bash
adk eval_set generate_eval_cases <agent-dir> <eval_set_id> \
    --user_simulation_config_file generation_config.json
```

The generation config takes a `count`, a `generation_instruction`, an
`environment_context` and a `model_name`. It calls the Vertex AI evaluation SDK,
so it needs a project and location.

Treat the output as a draft. Generated scenarios drift toward the obvious cases
and occasionally describe something the agent cannot do at all; read them before
committing, as you would a captured trace.

## Cost

Every turn costs two model calls — one for the simulated user, one for the agent
— plus the judge calls for each metric on each invocation. With
`max_allowed_invocations: 8` and two judge metrics at three samples, a single case
can be over sixty calls. Cap the conversations, and keep simulated suites in the
nightly tier.

## Live and voice

A voice agent runs on a Live API model and hears audio, so its suite has to speak.
`type: llm_audio` keeps the LLM simulator for deciding *what* the user says and
adds a text-to-speech step for *how* it sounds; `live_model_config` sends the
run down ADK's live inference path. Both shapes are identical on 2.12.0 and
2.13.0, and the audio simulator class is still marked experimental.

The same two case kinds apply:

- **Scenario** — `conversation_scenario` with a plan and a persona; the simulated
  user improvises and hangs up when the plan is done. Use these for coverage.
- **Fixed** — a scripted `conversation`, each user turn spoken verbatim. Use these
  for regressions, where the same words must produce the same behaviour.

```json
{
  "criteria": {"rubric_based_multi_turn_trajectory_quality_v1": {
    "threshold": 0.7,
    "judge_model_options": {"judge_model": "gemini-flash-latest", "num_samples": 3}}},
  "live_model_config": {"timeout_seconds": 300},
  "user_simulator_config": {
    "type": "llm_audio",
    "model": "gemini-flash-latest",
    "max_allowed_invocations": 10,
    "audio_model": "cloud_tts",
    "audio_model_configuration": {"speech_config": {
      "voice_config": {"prebuilt_voice_config": {"voice_name": "en-US-Studio-O"}},
      "language_code": "en-US"}},
    "include_text_with_audio": true
  }
}
```

| Field | Default | Notes |
|---|---|---|
| `audio_model` | `cloud_tts` | Cloud Text-to-Speech; or a Gemini TTS model id, in which case also set `"response_modalities": ["AUDIO"]` in `audio_model_configuration` |
| `...speech_config.voice_config.prebuilt_voice_config.voice_name` | `en-US-Studio-O` | a Cloud TTS voice matching the language, or a Gemini TTS voice name |
| `...speech_config.language_code` | `en-US` | BCP-47 |
| `max_allowed_invocations` | 20 | the opening prompt counts as one |
| `model` | `gemini-2.5-flash` | the text model behind the simulated user; name it |
| `include_text_with_audio` | true | sends the text alongside the audio part |
| `live_model_config.timeout_seconds` | 300 | the only key; ADK enforces no bound |

**Nothing in the audio block is required, and typos are accepted silently**
(`extra="allow"`): `{"type": "llm_audio"}` alone validates, and a misspelled
top-level key such as `max_allowed_invocation` is kept and ignored, so the cap
silently falls back to 20. `validate_sim_config.py` catches what ADK lets through:
`SIM034` an unknown key in `user_simulator_config`, `SIM032` a
`timeout_seconds` ≤ 0, `SIM033` a `live_model_config` while the agent's model is
not a Live model. Generate the block rather than typing it:
`gen_sim_config.py --kind user --audio [--audio-model M] [--voice V]
[--language L] [--live-timeout S]` (the last adds `live_model_config`).

**Omit `live_model_config` and the same cases run in text**, through the
ordinary path, which is a cheap way to debug a plan before paying for audio.

**Metrics.** Judge the whole conversation with a rubric on intent rather than
wording — `rubric_based_multi_turn_trajectory_quality_v1` at around 0.7 is the
example in Google's live-evaluation post — and add per-turn metrics where a turn
has a checkable outcome. Speech recognition varies, so exact text matching on a
spoken turn measures the transcriber as much as the agent.

**Vary the voice and the user.** Several voices and accents (one config per
voice, or one suite run per voice) and several personas, `NOVICE` among them,
because a voice agent that only understands one studio voice is untested.
Keep the turn cap and the timeout tight: a live conversation that loops costs
TTS, Live API and judge calls on every turn.

**Credentials.** The run needs the Live API for the agent's model and TTS for the
simulator: `cloud_tts` calls Cloud Text-to-Speech in `GOOGLE_CLOUD_PROJECT`
(ADC, the API enabled); a Gemini TTS id goes through the same Gemini or Vertex
credentials as any model. `check_env.py` reports what it can see.

**In `adk web`**, the eval run dialog has a **Standard | Live** toggle, with the
input modality, voice and language for the simulated user; the result shows a
transcript rebuilt from the audio, each turn with a playable clip. Listen to a
few before trusting a score.

**Where it runs.** Google's post recommends live evals in CI to catch
regressions before release. In this plugin's tiers that means the nightly or
release tier: every turn spends on TTS, the Live API and the judge. A short set of
fixed conversations can gate a pull request when the budget allows.

## When a simulated suite goes red

Check the harness before the agent. `per_turn_user_simulator_quality_v1` scores
whether the simulated user behaved as instructed; if it did not, the agent's
failure may be a reasonable response to an unreasonable user.

---

Sources: `adk-python: evaluation/conversation_scenarios.py`,
`evaluation/simulation/llm_backed_user_simulator.py`,
`evaluation/simulation/_llm_audio_user_simulator.py`
(`LlmAudioUserSimulatorConfig`, defaults and `extra="allow"` measured on 2.12.0
and 2.13.0), `evaluation/simulation/_cloud_tts_llm.py` (Cloud TTS voices,
`GOOGLE_CLOUD_PROJECT`), `LiveModelConfig` in `evaluation/eval_config.py`,
`evaluation/simulation/user_simulator_provider.py`, `evaluation/eval_config.py`,
`cli/cli_tools_click.py`; `adk-docs: docs/evaluate/user-sim.md`,
`docs/evaluate/criteria.md`. The two case kinds in live mode, the
conversation-level rubric, the text fallback, voices and personas, the `adk web`
Live toggle and the CI recommendation are paraphrased from Stephen Allen, "How to
Evaluate Live & Voice Agents in ADK", Google Developers Blog, 2026-08-24,
https://developers.googleblog.com/how-to-evaluate-live-voice-agents-in-adk/ (its
example uses a Gemini TTS model, the "Kore" voice and a cap of 10; those are
choices, not ADK defaults).
