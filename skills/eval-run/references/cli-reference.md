# `adk eval` and its neighbours

## `adk eval`

```
adk eval AGENT_MODULE_FILE_PATH [EVAL_SET_FILE_PATH_OR_ID ...]
         [--config_file_path PATH]
         [--print_detailed_results]
         [--eval_storage_uri gs://BUCKET/PREFIX]
         [--log_level DEBUG|INFO|WARNING|ERROR]
```

| Argument | Notes |
|---|---|
| `AGENT_MODULE_FILE_PATH` | the agent **directory**, not a file |
| eval set paths | one or more; append `:id1,id2` to run selected cases |
| `--config_file_path` | required unless `test_config.json` sits beside a single evalset |
| `--print_detailed_results` | per-case detail, including actual versus expected |
| `--eval_storage_uri` | store eval sets and results in GCS instead of `.adk/eval_history/` |

### What it does not have

- **No exit code.** It prints the summary and returns 0 whether cases passed or
  failed. This is the single most important fact about the command.
- **No `--num_runs`.** Repetition is either `AgentEvaluator(num_runs=...)`, which
  averages, or running the command again, which is what `run_evals.py --runs N`
  does.
- **No parallelism flag.** `InferenceConfig.parallelism` and
  `EvaluateConfig.parallelism` exist, but only on the programmatic path.

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
`cli/utils/evals.py`; `adk-docs: docs/evaluate/index.md`.
