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
| `agents/kwargs_agent` | agent built from a runtime kwargs dict inside a class and exported as `root_agent = _claims_app._agent`: nothing about it is statically readable, which is what AGT055 reports |
| `agents/shim` | agent package whose directory name differs from `App.name` |
| `agents/cached_agent` | context caching done right: the procedure is a long module constant wrapped in `types.Content(parts=[types.Part(text=...)])` and passed as `static_instruction`, the per-turn `instruction` is short with a `{claimant_name?}` placeholder, the `App` sets `ContextCacheConfig(min_tokens=2048, ttl_seconds=600, cache_intervals=5)`, and the evalset has a two-turn case. Clean under AGT060-066; loads on ADK 2.9.2 and 2.11.0 |
| `agents/cached_agent_broken` | the same agent with an f-string `static_instruction` stamped with `datetime.datetime.now()` (AGT060) and `cache_intervals=0` (AGT062) |
| `agents/yaml_agent` | `root_agent.yaml`, the deprecated config layout |
| `evalsets/` | valid snake_case, valid camelCase, legacy list format, scenario, and broken variants |
| `eval_configs/` | one per criterion family, plus invalid ones |
| `eval_history/` | synthetic result files, including NOT_EVALUATED and a five-run series |
| `eval_history/app_efficiency_1790000000.123456.evalset_result.json` | **source-derived**: the ADK 2.10 efficiency-metric result shape (`eval_status` 4 = `INFORMATIONAL`, no threshold, no criterion, `details.token_usage_details` with its eight nested counts). The values and the field layout were measured by driving `evaluation/_efficiency_evaluators.py` over one synthetic invocation and serializing the result the way `LocalEvalService` does; a credential-less `adk eval` run cannot produce one, because a failed inference leaves no invocations to measure. The claim data around it (prompts, tools, responses) is this plugin's own expense-claim domain, as everywhere else here. |
| `eval_history/app_efficiency_cached_1790000100.5.evalset_result.json` | **source-derived**, same shape as the efficiency file above: a candidate run after enabling context caching (eval set `efficiency_cached`), two passing cases - the single-turn case again and a two-turn case - with `cached_tokens` > 0 in every breakdown. Per-type counts are averaged over invocations exactly as `_efficiency_evaluators.py` (`TokenUsageV1Evaluator._average`) does, and each breakdown keeps the nesting (total = input + output, input = prompt + tool use, output = candidates + reasoning). It is `compare_runs.py`'s candidate against the efficiency file as baseline |
| `traces/` | `adk run --jsonl` traces shaped like each failure signature |
| `bin/fake_adk.py` | stands in for a real ADK interpreter in subprocess tests |
