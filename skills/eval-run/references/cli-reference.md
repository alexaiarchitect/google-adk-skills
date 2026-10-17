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

That is the whole list, and the **options are identical on 2.9.2, 2.10.0, 2.11.0
and 2.12.0**: no eval or conformance flag was added, renamed or removed in 2.10,
2.11 or 2.12. The only `--help` changes in 2.12.0 are docstring fixes (commit
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

- **No exit code on any release.** It prints the summary and returns 0 whether
  cases passed or failed — measured again on 2.12.0. This is the single most
  important fact about the command; see "Coming after 2.12.0" below.
- **No `--num_runs`.** Repetition is either `AgentEvaluator(num_runs=...)`, which
  averages, or running the command again, which is what `run_evals.py --runs N`
  does.
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

### Coming after 2.12.0 (unreleased main)

`adk-python` main after the `v2.12.0` tag (`c2ff634`) makes `adk eval` exit with
`1 if total_failed or not eval_run_summary else 0`, so a failing case finally
fails the process. Main also splits each invocation's duration into model, tool
and framework time (`e3797a4`) and folds Vertex rubric verdicts into the result
files (`bc220d2`); see `/adk:eval-analyze` `result-schema.md`. None of this is in
a release yet. **On every released version, `run_evals.py`'s exit code is the
authoritative verdict**: it reads the result files and exits 1 on any failed or
not-evaluated case, and it will keep doing so when the upstream fix ships.

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
failing case, and `c2ff634` (the exit-code fix) is on main after the tag.
