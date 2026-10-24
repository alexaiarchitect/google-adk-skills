# `adk eval` and its neighbours

## `adk eval`

```
adk eval AGENT_MODULE_FILE_PATH [EVAL_SET_FILE_PATH_OR_ID ...]
         [--config_file_path PATH]
         [--print_detailed_results]
         [--eval_storage_uri gs://BUCKET/PREFIX]
         [--enable_features NAME,NAME] [--disable_features NAME,NAME]
         [--log_level debug|info|warning|error|critical]
```

That is the whole list, and the **options are identical on 2.9.2, 2.10.0, 2.11.0,
2.12.0 and 2.13.0**: no eval or conformance flag was added, renamed or removed in
2.10 through 2.13 (2.13.0's `--help` for `eval`, `run`, `web`, `conformance` and
`eval_set` matches 2.12.0's). The only `--help` changes in 2.12.0 are docstring fixes (commit
`d56a994`): `adk eval` now says `AGENT_MODULE_FILE_PATH` is the agent *folder*,
and `adk conformance record` finally names `spec.yaml` instead of `input.yaml` /
`test.yaml` and documents its streaming-mode argument. `adk eval_set` and its
sub-commands are byte-for-byte unchanged.

| Argument | Notes |
|---|---|
| `AGENT_MODULE_FILE_PATH` | the agent **directory**, not a file |
| eval set paths | one or more; append `:id1,id2` to run selected cases |
| `--config_file_path` | required unless `test_config.json` sits beside a single evalset |
| `--print_detailed_results` | per-case detail, including actual versus expected |
| `--eval_storage_uri` | store eval sets and results in GCS instead of `.adk/eval_history/` |
| `--enable_features` / `--disable_features` | comma-separated experimental feature names, an alternative to the environment variables |

### What it does not have

- **No gate before 2.13.0, and only a coarse one after.** Through 2.12.0 it
  prints the summary and returns 0 whether cases passed or failed (measured on
  2.12.0). From 2.13.0 it exits 1 when a case fails or no case ran, and
  `NOT_EVALUATED` counts as failed (measured on 2.13.0). Either way it cannot
  gate on a pass rate, on regressions against a baseline, or on a budget; see
  "In 2.13.0" below.
- **No `--num_runs` on any release.** Repetition is either
  `AgentEvaluator(num_runs=...)`, which averages, or running the command again,
  which is what `run_evals.py --runs N` does. Main after 2.13.0 adds the flag
  (see the last section).
- **No parallelism flag.** `InferenceConfig.parallelism` and
  `EvaluateConfig.parallelism` exist, but only on the programmatic path. On 2.10+ a
  value below 1 raises ``ValueError: `parallelism` must be at least 1, got N.``
  where 2.9.2 built a semaphore of 0 and hung forever.

### And one more thing about the summary

Anything not `PASSED` lands in the failed column, so `NOT_EVALUATED` reads as a
failure there. The result files distinguish them; the printed summary does not.

## Config discovery

`test_config.json` is auto-discovered **only** when exactly one evalset *file* is
passed and the config sits in the same directory. Several evalsets, or a config
under `tests/eval/`, means passing `--config_file_path` explicitly. Without it,
ADK silently uses its defaults — `tool_trajectory_avg_score` 1.0 and
`response_match_score` 0.8 — and the wording metric starts failing cases nobody
meant to score on wording.

## Managing eval sets from the CLI

```bash
adk eval_set create <agent-dir> <eval_set_id>
adk eval_set add_eval_case <agent-dir> <eval_set_id> \
    --scenarios_file scenarios.json --session_input_file session.json
adk eval_set generate_eval_cases <agent-dir> <eval_set_id> \
    --user_simulation_config_file config.json
```

`generate_eval_cases` uses the Vertex AI evaluation SDK, so it needs a project
and location. See `/adk:eval-simulation`.

Which sub-command owns which flag is easy to get wrong, so: `--scenarios_file`
and `--session_input_file` are **both required** by `add_eval_case` and exist
nowhere else; `--user_simulation_config_file` is required by
`generate_eval_cases`; `create` takes no options of its own. And
`--sampler_config_file_path` / `--optimizer_config_file_path` belong to
**`adk optimize`**, a top-level command for prompt optimization — not to
`adk eval` and not to any `eval_set` sub-command.

## Running and inspecting

```bash
adk run <agent-dir> --jsonl "a request"     # structured events; without --jsonl, text only
adk run <agent-dir> --save_session          # writes the session for later capture
adk web <agents-dir>                        # dev UI: trace tab and eval tab
adk api_server <agents-dir>                 # same API, no UI, no /dev routes
```

**`initial_state.json` (2.13+, `adk web` only).** There is no flag: `adk web`
reads `<agents_dir>/<app>/initial_state.json` whenever a session is created
explicitly (the UI's new-session button, `POST .../sessions`) and merges it under
the caller's state, so a key the caller sends wins. Sessions the runner creates
on its own (`--auto_create_session`) are not seeded, and `adk api_server` never
reads the file. A missing or malformed file logs a warning and seeds nothing.
`adk run --state_file` (below) is the equivalent for the terminal.

`adk run` prints events to stdout and writes its log to
`$TMPDIR/agents_log/agent.latest.log`; `adk web` does the opposite. The directory
comes from `tempfile.gettempdir()`, so it is platform dependent — `/tmp` on
Linux, a private per-user directory on macOS. Do not guess it: ADK echoes both
the real filename and `To access latest log: tail -F <path>` at startup.

**`--state_file` (2.12+).** `adk run <agent-dir> --state_file state.json` seeds
the session state from a JSON file (commit `c390266`, released in 2.12.0); it sits
beside the older `--state '<json string>'`. On 2.9–2.11 the flag does not exist,
so seed state with `--state`, an evalset's `session_input`, or the dev UI. This plugin's
`run_jsonl.py` (`/adk:agent-debug`) passes `--state-file F` through as
`--state_file` on a 2.12+ runner, and stops with exit 2 on an older runner or
one whose version it cannot read. The 2.11.0 `adk run` help change was service-URI wording only
(Agent Platform auto-detection, a `sqlite:///` memory-service URI).

`--print_detailed_results` output is easier to read on 2.12.0 (commit `69c58ea`):
the per-invocation details print as readable text instead of a dumped object.
The judge requests also run with automatic function calling disabled (`cc339e0`),
and eval session ids are generated in a form Agent Engine accepts (`4368d68`).

### In 2.13.0

- **Exit code** (`c2ff634`): `adk eval` exits
  `1 if total_failed or not eval_run_summary else 0` — 1 on a failed case, 1 when
  no case ran, and `NOT_EVALUATED` counts as failed. Measured: a failing case
  exits 0 on 2.12.0 and 1 on 2.13.0; a passing case exits 0 on both.
- **Duration split** (`e3797a4`): `invocation_duration_v1` results carry
  `details.duration_details.{total,model,tool,framework}_seconds`, and
  `--print_detailed_results` prints the breakdown.
- **Vertex rubric verdicts** (`bc220d2`): extra `RubricScore` rows with the ids
  `"explanation"` and `"error"` inside the existing `rubric_scores`.
- **`AgentEvaluator.evaluate`** takes each metric's pass direction from the
  evaluator's own per-invocation `eval_status` (`5bf7f12`), so lower-is-better
  custom metrics gate correctly; judge and user-simulator models may be `BaseLlm` instances, not
  only model-id strings (`c12392e`, `cb48b91`).
- **Drive-qualified ids** (`29bb053`): an `eval_set_id` (or app name) starting
  with a letter and a colon (`c:pass`) is rejected when results are saved — after
  inference has already been paid for. `validate_evalset.py` flags it (`EVS076`).

See `/adk:eval-analyze` `result-schema.md` for the result-file shapes. **`run_evals.py`
stays the recommended command on every version**: it reads the result files,
separates failed from never-scored, and gates on `--min-pass-rate`; with
`compare_runs.py --max-regressions` and the budget flags it gates on things a
single process exit cannot express. Bare `adk eval` on a 2.13+ pin is a
`CI001` warning in `lint_workflows.py`, an error on an older or unknown pin.

### Coming after 2.13.0 (unreleased main)

Not in any release yet (checked against `adk-python` `12c7bed76`):

- **`adk eval --num_runs N` and `EvalConfig.multi_run_config`** (`7f3517e`):
  `num_runs`; `cross_run_scoring` (`mean_over_invocations` by default, or
  `mean_of_runs`, `min_over_runs`, or a rule such as
  `{"method": "pass_rate", "threshold": 0.9}`); `cross_run_scoring_by_metric`,
  whose keys must appear in `criteria`; `cross_run_criteria`, a list of rules;
  `max_crashed_runs` (default 0); `custom_methods` with a `code_config` and
  `uses_scores`. Results gain `repetition_index`, and pinned session ids are
  dropped with a warning when `num_runs > 1`. `AgentEvaluator` keeps its own
  `num_runs` default of 2.
- **`google.adk.tools.tool_args_matcher`** (`ed76f94`): `BaseArgMatcher` with
  exact, casefold, numeric, contains and ignore matchers and
  `ToolArgsMatcher(allow_extra_args=...)`. No evaluator uses it yet; trajectory
  matching is still exact.
- **Per-session compaction** (`9331638`): a plugin's `before_run_callback` can set
  the compaction config for one session.
- **Prompt-cache creation tokens** on `LlmResponse` (`b89dda4`).

Released versions drop `multi_run_config` silently (the config model ignores
unknown keys; measured on 2.9.2, 2.12.0 and 2.13.0), so `pass_at_k.py` and
`run_evals.py --runs` remain the way to batch today. `validate_config.py` reports
the key as `CFG072` (info) and checks its shape (`CFG073`).

## Two other test commands

| Command | What it is |
|---|---|
| `adk test [folder]` | replays recorded interactions from `tests/*.json`. Not `adk eval`. It collects an evalset left loose in `tests/`, finds no `events` key and **skips** it — so the suite reads green while nothing ran |
| `adk conformance record\|test` | records and replays full transcripts to detect drift in requests and tool calls |

## Result files

`<agent_dir>/.adk/eval_history/<app>_<eval_set>_<timestamp>.evalset_result.json`,
written by field name, so snake_case on disk. `adk create` gitignores `.adk/`.

**The timestamp is a raw float**, straight from `time.time()`, so the filename
contains a decimal point:
`app_happy_1730000000.123456.evalset_result.json`. Anything that globs, parses or
sorts these must not assume an integer — and the extra dot means a naive
`name.split(".")[0]` loses the fraction and collides runs made in the same
second. Case management in this plugin sorts on the parsed float.

With `--eval_storage_uri gs://...` the same content goes to Cloud Storage, which
is how CI and humans end up reading the same history.

## Deploy, briefly

`adk deploy cloud_run --with_cloud_run_sandbox` (present since 2.12.0; only the
docs entry is new) enables the Cloud Run sandbox for code execution, off by
default; ADK then deploys with `gcloud beta run deploy --sandbox-launcher`.
Deployment is outside this plugin; what matters here is that the evals run
against the same agent directory before it ships.

## Neighbours: `agents-cli eval`

Google's Agents CLI wraps a hosted path around the same agents:
`agents-cli eval dataset synthesize` (cases from the user simulator),
`eval generate` (run inference), `eval grade --traces --config` (score traces with
the Agent Platform evaluation service), `eval compare`, `eval analyze`, and
`eval optimize`; `agents-cli eval run` scores trajectories in CI. Its extension
system is **experimental**, so pin the CLI version and commit the extension
lockfile (`agents-cli-extensions.yaml`) and vendored `extensions/` directory. In a
LangChain project, the first-party extension overrides `eval generate`, keeps
`eval grade|compare|analyze` (they read a shared dataset format), and refuses
`eval dataset` and `eval optimize`, which need an ADK agent object. This plugin's
scripts are the local, credential-light equivalent; `/adk:eval-analyze`
`improvement-loop.md` maps one onto the other.

Python is ADK's reference implementation and where its evaluation tooling
lives, so the evals stay in Python even when the production service is written
in Go, Java, TypeScript or Kotlin: `adk eval` or `agents-cli eval run` scores
trajectories against golden datasets in CI and tests the system end to end.

---

Sources: `adk-python: cli/cli_tools_click.py`, `cli/cli_eval.py`, `cli/cli.py`,
`cli/utils/logs.py` (`log_to_tmp_folder`), `cli/agent_test_runner.py`,
`evaluation/eval_config.py`, `evaluation/local_eval_set_results_manager.py`,
`evaluation/_eval_set_results_manager_utils.py` (`create_eval_set_result`),
`cli/utils/evals.py`, `evaluation/local_eval_service.py`
(`_parallelism_semaphore`, the 2.10 guard); `adk-docs: docs/evaluate/index.md`.
Every flag list above was diffed between the installed 2.9.2, 2.10.0, 2.11.0 and
2.12.0 wheels' `--help` output; `--state_file` is `adk-python` commit `c390266`
(in `v2.12.0`); the bare `adk eval` exit code was measured as 0 on 2.12.0 with a
failing case and as 1 on 2.13.0 (`c2ff634`, in `v2.13.0`); `initial_state.json` is
`3c8c63e`, in `v2.13.0` (`cli/dev_server.py`); the multi-run, matcher, compaction
and cache-token items were classified post-tag against `v2.13.0` at `12c7bed76`;
`adk-docs: docs/deploy/cloud-run.md` (`cc53b6c14`). Neighbours: "Powering the
Agent Development Lifecycle with any framework, from ADK to LangChain and beyond"
(https://developers.googleblog.com/powering-the-agent-development-lifecycle-with-any-framework-from-adk-to-langchain-and-beyond/),
"One Agent Development Kit, five languages"
(https://cloud.google.com/blog/topics/developers-practitioners/one-agent-development-kit-five-languages),
"Driving the Agent Quality Flywheel from Your Coding Agent"
(https://developers.googleblog.com/driving-the-agent-quality-flywheel-from-your-coding-agent/).
