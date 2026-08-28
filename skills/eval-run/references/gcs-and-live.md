# Shared eval storage, and what "live" does not yet mean

Two flags in this area promise more than they deliver if you read them quickly:
`--eval_storage_uri` really does move a team's evaluation history into one
place, and `--mode live` on conformance is not implemented. This page is the
detail behind both, plus the live-model configuration that user simulation uses.

## `--eval_storage_uri gs://<bucket>`

Accepted by `adk web`, `adk eval`, and all three `adk eval_set` subcommands
(`create`, `add_eval_case`, `generate_eval_cases`). When set, ADK swaps its
local managers for the GCS ones, and **both** halves move:

| Without it | With it |
|---|---|
| eval sets read from and written to the agent directory | eval sets in `gs://<bucket>/...` (`GcsEvalSetsManager`) |
| results in `<agent>/.adk/eval_history/*.evalset_result.json` | results in the bucket (`GcsEvalSetResultsManager`) |

```bash
adk eval <agent-dir> <eval-set-id> \
    --config_file_path <config> \
    --eval_storage_uri gs://acme-adk-evals/claims
```

Note the second argument: with GCS storage you usually pass an **eval set id**
rather than a file path, because the set lives in the bucket. `adk eval` accepts
either ids or paths, but never both in one command.

### Why this matters more than it looks

The value is not the storage. It is that a nightly CI run and a person opening
the dev UI on a laptop end up looking at **the same history**. Without it, CI
accumulates results inside an ephemeral container that is discarded, and the
only evaluation history that survives is whatever happened to run on someone's
machine — which is exactly the history you cannot draw a trend from.

So the rule of thumb:

- **Results belong in the bucket** once more than one person or machine runs the
  suite. Result files are run records; they are append-only, they are numerous,
  and nobody reviews them in a pull request.
- **Eval sets belong in the repository** while they are small enough to review.
  A case is a specification: a diff in a pull request is how a reviewer sees
  that the specification moved deliberately. Move sets to the bucket when the
  dev UI is the primary authoring surface for a team that is not reading diffs.

The two are not exclusive — keeping the sets in git and pointing only CI's
results at a bucket is a coherent setup, and usually the right first step.

### CI notes

- The runner needs a credential that can read and write the bucket. With
  Workload Identity Federation the `google-github-actions/auth` step already
  provides it; with a key file, `GOOGLE_APPLICATION_CREDENTIALS` must be set for
  the eval step as well as the model step.
- Bucket writes fail late — after the model spend. Do a one-off
  `adk eval_set create <agent> smoke --eval_storage_uri gs://...` the first time
  you wire it, so a permissions problem surfaces for free.
- `run_evals.py --storage gs://...` passes the flag through, and still parses the
  printed summary for its own exit code. The exit-code problem does not change:
  `adk eval` returns 0 with the flag exactly as it does without it.

## Live mode

Three different things get called "live", and only two exist today.

**`adk conformance test --mode live`** is declared and **not implemented**. The
help text says "evaluation-based verification (not yet implemented)". Replay is
the mode that works: it compares model requests, model responses, tool calls and
tool results against recorded YAML, which is what makes it cheap enough to run
on every pull request. Treat `--mode live` as a placeholder, not an option.

**Streaming modes** (`none`, `sse`, `bidi`) are real and apply to recording and
replay. `none` is the default and the one to use: a non-streaming recording is
deterministic and compares cleanly. `sse` records server-sent-event streaming,
which is worth pinning when streaming behaviour is itself the thing you ship.
`bidi` is accepted by the CLI but raises in this version — do not build a suite
on it.

**Live-model configuration for user simulation** is the third meaning, and
unrelated to conformance: it is how the simulated user reaches a model. It lives
in the eval config under the user-simulator settings, where the simulator's own
model is named explicitly. Leave it unset and the simulator falls back to a
default that this plugin's model policy flags as deprecated, so an entire
simulated suite quietly changes behaviour when that id is retired. See
`/adk:eval-simulation` for the configuration itself.

## Choosing between conformance and evaluation

They answer different questions, and a project wants both:

| | Conformance (`adk conformance test`) | Evaluation (`adk eval`) |
|---|---|---|
| Question | did the interactions change? | is the behaviour still correct? |
| Cost per run | none — replayed from recordings | model calls, and judge calls if configured |
| Exit code | real | always 0; needs `run_evals.py` |
| Breaks when | a prompt, tool or model output shifts at all | the agent's decisions get worse |
| Right cadence | every pull request | pull request (cheap tier) and nightly (judged) |

Conformance is strict in a way that is useful and annoying in equal measure: an
intentional prompt edit fails it. That failure is the feature — it forces the
recording to be refreshed deliberately, which is a record of what changed.

Sources: `adk-python/src/google/adk/cli/cli_tools_click.py` (`--eval_storage_uri`
on `web`, `eval` and `eval_set`; `EVAL_SET_FILE_PATH_OR_ID` semantics);
`adk-python/src/google/adk/evaluation/gcs_eval_sets_manager.py` and
`gcs_eval_set_results_manager.py`; `adk-python/src/google/adk/cli/conformance/
cli_test.py` (replay vs live, streaming modes);
`adk-docs/docs/evaluate/index.md` (running evaluations, conformance reports).
Background reading: the LinkedIn Learning course "Evaluating AI Agents with
Google ADK", which covers running evaluations in a build pipeline.
