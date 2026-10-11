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

That is the whole list, and it is **identical on 2.9.2, 2.10.0 and 2.11.0** — the
`--help` output of `adk eval`, `adk eval_set`, `adk eval_set create`,
`adk eval_set add_eval_case`, `adk eval_set generate_eval_cases`,
`adk conformance record` and `adk conformance test` is byte-for-byte the same
across the three releases. No eval or conformance flag was added, renamed or
removed in 2.10 or 2.11.

| Argument | Notes |
|---|---|
| `AGENT_MODULE_FILE_PATH` | the agent **directory**, not a file |
| eval set paths | one or more; append `:id1,id2` to run selected cases |
| `--config_file_path` | required unless `test_config.json` sits beside a single evalset |
| `--print_detailed_results` | per-case detail, including actual versus expected |
| `--eval_storage_uri` | store eval sets and results in GCS instead of `.adk/eval_history/` |
| `--enable_features` / `--disable_features` | comma-separated experimental feature names, an alternative to the environment variables |

### What it does not have

- **No exit code.** It prints the summary and returns 0 whether cases passed or
  failed. This is the single most important fact about the command.
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

**No `--state_file` on any release.** `adk-python` main has an `adk run
--state_file` option (seed the session state from a JSON file), but it landed
after the 2.11.0 tag; `adk run --help` on the 2.11.0 wheel does not list it. Until
it ships, seed state through an evalset's `session_input` or the dev UI. The only
`adk run` help change in 2.11.0 is service-URI wording: Agent Platform
auto-detection and a `sqlite:///` memory-service URI.

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
Every flag list above was diffed between the installed 2.9.2, 2.10.0 and 2.11.0
wheels' `--help` output; `--state_file` is `adk-python` commit `c390266`, on main
after the `v2.11.0` tag.
