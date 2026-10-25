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
| `agents/skilled_agent` | skills done right: a `SkillToolset` with an inline `receipt-check` skill and a file-based `skills/claim-policy/SKILL.md` + `references/limits.md`, an `activation.evalset.json` whose cases expect `load_skill` with `{"skill_name": ...}` before the skill's steps, and a trajectory-only `test_config.json`. Clean under AGT070-079; loads on ADK 2.9.2 and 2.12.0 |
| `agents/skilled_agent_broken` | one skill problem per rule: a name with spaces and an 1,100-character description (AGT070), a directory with no SKILL.md (AGT070), a 600-line body (AGT071), an instruction copying an inline skill's body (AGT072), a 9-character description (AGT073), `SkillLifecycleConfig(default_mode=BOUNDED, max_active_skills=MAX_ACTIVE)` with `MAX_ACTIVE = 0` and no `.env` (AGT074, AGT075). Does not load, on purpose |
| `agents/compaction_broken` | `EventsCompactionConfig(compaction_interval=0, overlap_size=-1)` on the App (AGT076, twice), an LlmAgent transfer target left at `include_contents='default'` (AGT078), a tool returning a 2,356-character string (AGT079) |
| `agents/stateful_agent` | a resumable step machine in session state: the instruction reads `{claim_step}` and `{claimant?}`, a `before_agent_callback` puts a new session at `claim_step` "new", each tool writes the step it reached through `tool_context.state[...]`, and `resume.evalset.json` (the resume template filled in) seeds `session_input.state` to hold at receipt_requested and to proceed from receipt_validated with the seeded claimant details. Clean under AGT080, AGT015 and EVS075; loads on ADK 2.9.2 and 2.13.0 |
| `agents/stateful_agent_broken` | `{approver}` in an instruction that nothing ever writes (AGT080; ADK raises KeyError "Context variable not found" when it builds the instruction), two agents named `checker` under different parents (AGT015, cousins; ADK 2.13 logs "Found duplicate agent names"), and a resume case seeding the misspelt `claim_stage` (EVS075). Loads on ADK 2.9.2 and 2.13.0: both agent mistakes show only at run time |
| `evalsets/long_conversation.evalset.json` | one six-invocation case, the shape AGT077 looks for |
| `registry/frozen_suite/eval_registry.json` | a frozen `golden` suite over `happy.evalset.json` and an unfrozen one over `edge.evalset.json`, laid out for a copy of `agents/recipes_good` (EVS120) |
| `plugins/retail-like/` | an Agent-Plugins layer in the expense domain with the recipes' shapes: `plugin.json`, `skills/product-search/{SKILL.md, EVAL.yaml}` (two cases, four rubric lines, four assertions; clean under EVY001-010) |
| `agents/live_agent` | a streaming tool done right on a live model: `os.getenv("MODEL_NAME", "gemini-3.8-live")`, `track_claim_progress` is an `async def ... -> AsyncGenerator[str, None]` that yields `PROGRESS:` lines then `DONE:`, says "Call this ONCE per claim" and keeps a module-level latch, `stop_streaming(function_name: str)` is bound, and `test_config.json` sets `live_model_config`. Lint clean apart from AGT014; loads on ADK 2.9.2 and 2.13.0 |
| `agents/live_agent_broken` | the same tracker with two `yield`s on `gemini-flash-latest` (AGT081 warn: under `run_async` 2.12+ aborts on the second `yield`, 2.9.2 never runs the body) |
| `agents/search_agent` | a per-diem lookup agent with the built-in `google_search` and a `test_config.json` grading it with `rubric_based_multi_turn_trajectory_quality_v1` (CFG076: that judge raises `TypeError` on built-in tools through 2.13.0). Loads on ADK 2.9.2 and 2.13.0 |
| `eval_configs/action_rubric_reply_judge.json` | an action rubric ("calls `check_cost_centre_funds` before it calls `submit_claim`") under `rubric_based_final_response_quality_v1` with no trajectory metric (CFG075) |
| `eval_configs/compound_rubric.json` | one rubric holding two expectations joined by a single "and" (RUB006) |
| `projects/src_layout` | an agents-cli project in a src layout: `src/__init__.py` re-exports `root_agent`, `agents-cli-manifest.yaml` names `src/claims_agent`, the App is named "claims", and the cases are an agents-cli dataset under `tests/eval/datasets/` with `tests/eval/eval_config.yaml` (layout style `src`, dataset note, not ADK evalsets) |
| `agents/yaml_agent` | `root_agent.yaml`, the deprecated config layout |
| `evalsets/` | valid snake_case, valid camelCase, legacy list format, scenario, and broken variants |
| `eval_configs/` | one per criterion family, plus invalid ones |
| `eval_history/` | synthetic result files, including NOT_EVALUATED and a five-run series |
| `eval_history/app_efficiency_1790000000.123456.evalset_result.json` | **source-derived**: the ADK 2.10 efficiency-metric result shape (`eval_status` 4 = `INFORMATIONAL`, no threshold, no criterion, `details.token_usage_details` with its eight nested counts). The values and the field layout were measured by driving `evaluation/_efficiency_evaluators.py` over one synthetic invocation and serializing the result the way `LocalEvalService` does; a credential-less `adk eval` run cannot produce one, because a failed inference leaves no invocations to measure. The claim data around it (prompts, tools, responses) is this plugin's own expense-claim domain, as everywhere else here. |
| `eval_history/app_efficiency_cached_1790000100.5.evalset_result.json` | **source-derived**, same shape as the efficiency file above: a candidate run after enabling context caching (eval set `efficiency_cached`), two passing cases - the single-turn case again and a two-turn case - with `cached_tokens` > 0 in every breakdown. Per-type counts are averaged over invocations exactly as `_efficiency_evaluators.py` (`TokenUsageV1Evaluator._average`) does, and each breakdown keeps the nesting (total = input + output, input = prompt + tool use, output = candidates + reasoning). It is `compare_runs.py`'s candidate against the efficiency file as baseline |
| `traces/` | `adk run --jsonl` traces shaped like each failure signature |
| `bin/fake_adk.py` | stands in for a real ADK interpreter in subprocess tests |
