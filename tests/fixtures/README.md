# Test fixtures

All original work, written for this plugin. The domain is **expense claim
approval**: an agent that looks up a policy, checks whether a cost centre has
funds, and then either submits or rejects a claim.

Nothing here is copied or adapted from the LinkedIn Learning course repository,
whose exercise files are licensed for personal practice only and may not be
redistributed. The evaluation practices these fixtures exercise are described in
this plugin's references in its own words.

| Path | What it is for |
|---|---|
| `agents/flat_good` | `adk create`-style layout, clean: tools with contracts, env-driven model |
| `agents/flat_drift` | the same agent with deliberate problems, one per review rule |
| `agents/recipes_good` | `app/` package plus `tests/eval/`, an `App` export, a pytest runner |
| `agents/shim` | agent package whose directory name differs from `App.name` |
| `agents/yaml_agent` | `root_agent.yaml`, the deprecated config layout |
| `evalsets/` | valid snake_case, valid camelCase, legacy list format, scenario, and broken variants |
| `eval_configs/` | one per criterion family, plus invalid ones |
| `eval_history/` | synthetic result files, including NOT_EVALUATED and a five-run series |
| `traces/` | `adk run --jsonl` traces shaped like each failure signature |
| `bin/fake_adk.py` | stands in for a real ADK interpreter in subprocess tests |
