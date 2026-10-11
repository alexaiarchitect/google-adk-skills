# The pytest path

`AgentEvaluator` raises an `AssertionError` when a case fails, so pytest's exit
code is a real gate. That is the reason to prefer it over `adk eval` anywhere the
result has to block something.

## The two entry points

```python
await AgentEvaluator.evaluate(
    agent_module="app.agent",            # an importable module, not a path
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

| Input | 2.10.0, 2.11.0 | 2.9.2 |
|---|---|---|
| `num_runs < 1` | `ValueError: \`num_runs\` must be at least 1, got 0.` | accepted, the loop simply ran zero times |
| `evaluate_eval_set` with an empty eval set | `ValueError: No eval cases were evaluated, so there is nothing to report a pass or a failure for.` | passed silently, reporting nothing |
| `evaluate` on a directory with no `*.test.json` | `ValueError: No \`*.test.json\` eval files found in <dir>, so there is nothing to evaluate.` | passed silently |

That last pair is the one worth knowing: on 2.9 a suite that had quietly stopped
finding its cases was a **green** test. On 2.10+ the same suite fails loudly, which
is what you want — a test that evaluates nothing has proved nothing.

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

- `pip install "google-adk[eval]==2.11.*" pytest pytest-asyncio` — the extra
  pins `google-cloud-aiplatform[evaluation]>=1.148,<2` on 2.11.0 (2.9.2 has no
  upper bound); see `/adk:eval-criteria`'s `vertex-requirements.md`
- `asyncio_mode = "auto"` in the pytest config, or `@pytest.mark.asyncio` on each
  test
- the agent module importable from the working directory — run pytest from the
  project root
- `.env` loaded: ADK loads it for the agent, but a test process may need
  `dotenv.load_dotenv()` first

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
wheel, 172, 180 and 364 of the 2.11.0 one, and absent from 2.9.2),
`evaluation/eval_config.py`,
`evaluation/eval_set.py`; `adk-recipes: contrib/python/llm-auditor/eval/`.
Compared across installed 2.9.2, 2.10.0 and 2.11.0 wheels; the `[eval]` pins
read from each wheel's `METADATA`.
