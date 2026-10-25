# The pytest path

`AgentEvaluator` raises an `AssertionError` when a case fails, so pytest's exit
code is a real gate. That is the reason to prefer it over `adk eval` anywhere the
result has to block something.

## The two entry points

```python
await AgentEvaluator.evaluate(
    agent_module="app.agent",            # an importable module, not a path (through 2.13.0)
    eval_dataset_file_path_or_dir="tests/eval/data",
    num_runs=2,
    agent_name=None,                     # evaluate a named sub-agent
    print_detailed_results=True,
)
```

`evaluate` walks a directory for **`*.test.json`** — only that pattern — and
looks for `test_config.json` **beside each file**. A config under `tests/eval/`
is not found, which is why the template uses the other entry point:

```python
await AgentEvaluator.evaluate_eval_set(
    agent_module="app.agent",
    eval_set=EvalSet.model_validate_json(path.read_text()),
    eval_config=EvalConfig.model_validate(json.loads(config.read_text())),
    num_runs=1,
)
```

Explicit eval set, explicit config, nothing inferred.

## What `num_runs` actually does

It repeats the inference and then **averages each metric across the runs**. So
`num_runs=5` on a case that passes three times and fails twice yields one
averaged score, and whether it passes depends on where the average lands
relative to the threshold.

That is not reliability measurement. It smooths over exactly the variance you
want to see. For reliability, keep `num_runs=1` and run the whole suite several
times with `/adk:eval-run --runs N`, which keeps each run's results separate for
`pass_at_k.py`.

`num_runs` defaults to 2. **On ADK 2.10 and later both entry points validate their inputs
before doing any work; on 2.9.2 they did not:**

| Input | 2.10.0 through 2.12.0 | 2.9.2 |
|---|---|---|
| `num_runs < 1` | `ValueError: \`num_runs\` must be at least 1, got 0.` | accepted, the loop simply ran zero times |
| `evaluate_eval_set` with an empty eval set | `ValueError: No eval cases were evaluated, so there is nothing to report a pass or a failure for.` | passed silently, reporting nothing |
| `evaluate` on a directory with no `*.test.json` | `ValueError: No \`*.test.json\` eval files found in <dir>, so there is nothing to evaluate.` | passed silently |

That last pair is the one worth knowing: on 2.9 a suite that had quietly stopped
finding its cases was a **green** test. On 2.10+ the same suite fails loudly, which
is what you want — a test that evaluates nothing has proved nothing.

**2.13.0 changes two details.** The overall per-metric verdict no longer
hard-codes `score >= threshold`: it reads the direction from the evaluator's own
per-invocation `eval_status` values (`5bf7f12`), so a lower-is-better custom
metric (a cost, a latency) passes when it should; when the invocations disagree
it falls back to `>=`. And
`JudgeModelOptions.judge_model` and the user simulator's `model` accept a
`BaseLlm` instance as well as a model-id string (`c12392e`, `cb48b91`), which is
how a test points the judge at a wrapped or fake model.

### Coming after 2.13.0 (unreleased main)

Main after the `v2.13.0` tag adds `EvalConfig.multi_run_config` and
`adk eval --num_runs` (`7f3517e`): per-run results tagged with
`repetition_index`, combined by `cross_run_scoring` (`mean_over_invocations` by
default, `mean_of_runs`, `min_over_runs`, or a `{"method": "pass_rate",
"threshold": ...}` rule), with `max_crashed_runs` and `custom_methods`.
`AgentEvaluator` keeps its own `num_runs` argument and its default of 2. Main also
has a tool argument matcher (`google.adk.tools.tool_args_matcher`, not yet used
by any evaluator), per-session compaction set by a plugin's
`before_run_callback`, and prompt-cache creation tokens on `LlmResponse`.
Released versions drop `multi_run_config` silently, so `pass_at_k.py` and
`--runs` remain the way to batch today.

**An agent built at runtime** (`ce92e9247`): on main, `evaluate` and
`evaluate_eval_set` accept an agent object (any `BaseNode`, a `Workflow`) or an
`App` through the same `agent_module` parameter — there is no `agent=` keyword.
An `App` contributes its `root_agent` plus its plugins, cache and resumability
config; selecting a sub-agent by `agent_name` needs a `BaseAgent` root (else a
`TypeError`). Through 2.13.0 the parameter takes only a module path: an object
fails with `ModuleNotFoundError` (its repr is imported as a module name), and
`agent=` with `TypeError: ... unexpected keyword argument 'agent'` (measured).

## A working test module

```python
EVAL_DIR = pathlib.Path(__file__).parent
EVALSETS = sorted(EVAL_DIR.glob("evalsets/*.evalset.json"))
NUM_RUNS = int(os.getenv("ADK_EVAL_NUM_RUNS", "1"))

@pytest.mark.asyncio
@pytest.mark.parametrize("evalset_path", EVALSETS, ids=lambda p: p.stem)
async def test_evalset(evalset_path):
    eval_set = EvalSet.model_validate_json(evalset_path.read_text())
    eval_config = EvalConfig.model_validate(json.loads(CONFIG.read_text()))
    await AgentEvaluator.evaluate_eval_set(
        agent_module="app.agent", eval_set=eval_set,
        eval_config=eval_config, num_runs=NUM_RUNS, print_detailed_results=True,
    )
```

Parametrizing over files means a failure names the evalset. `/adk:agent-create`
generates this.

## Requirements

- `pip install "google-adk[eval]==2.13.*" pytest pytest-asyncio` — the extra
  pins `google-cloud-aiplatform[evaluation]>=2.2,<3` on 2.12.0 and 2.13.0 (the
  `[eval]` extra is byte-identical between them; `>=1.148,<2` on
  2.11.0, no upper bound on 2.9.2); there is no separate `agentplatform`
  package to add. See `/adk:eval-criteria`'s `vertex-requirements.md`
- `asyncio_mode = "auto"` in the pytest config, or `@pytest.mark.asyncio` on each
  test
- the agent module importable from the working directory — run pytest from the
  project root
- `.env` loaded: ADK loads it for the agent, but a test process may need
  `dotenv.load_dotenv()` first

## Behavioural tests beside it

`AgentEvaluator` scores whole evalset cases. For one discrete behaviour (called
the validator, did not call the payment tool, asked before guessing), a plain
pytest assertion over a recorded turn is faster and less brittle:
`gen_test_eval.py --behavioral` writes `tests/eval/test_behaviors.py`, and
`run_pytest_eval.py --behavioral` runs it with `ADK_EVAL_RUNS` repeats and an
optional `ADK_EVAL_MIN_PASS_RATE` floor. Walkthrough:
`/adk:eval-create` `behavioral-tests.md`.

## Reading a failure

`AgentEvaluator` reports each metric that missed its threshold, with the score.
A `NOT_EVALUATED` metric is reported with wording that says it is **not a score
regression** — it still fails the test, but the fix is configuration, not the
agent.

For divergence points and quadrant diagnosis, read the result files with
`/adk:eval-analyze` rather than the pytest output.

## Old-format data

`evaluate` also accepts the legacy list format with a deprecation warning, and
there is a one-shot converter, `AgentEvaluator.migrate_eval_data_to_new_schema`.
`/adk:eval-create`'s `migrate_legacy.py` does the same with stable ids.

---

Sources: `adk-python: evaluation/agent_evaluator.py` (the `num_runs`, empty
eval-set and no-test-file `ValueError`s, at lines 171, 179 and 363 of the 2.10.0
wheel, 172, 180 and 364 of the 2.11.0 one, 172, 181 and 365 of the 2.12.0 one,
and absent from 2.9.2),
`evaluation/eval_config.py`,
`evaluation/eval_set.py`; `adk-recipes: contrib/python/llm-auditor/eval/`.
Compared across installed 2.9.2, 2.10.0, 2.11.0 and 2.12.0 wheels; the `[eval]`
pins read from each wheel's `METADATA` (2.13.0's compared with 2.12.0's). The
2.13.0 changes are commits `5bf7f12`, `c12392e` and `cb48b91`, in `v2.13.0`;
multi-run (`7f3517e`) and the matcher (`ed76f94`) are post-tag at `12c7bed76`,
the runtime-agent signature (`ce92e9247`) at `dfd534a88`.
