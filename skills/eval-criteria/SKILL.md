---
name: eval-criteria
description: >-
  Chooses and configures Google ADK evaluation criteria, and writes the eval
  config. Covers all 13 configurable prebuilt metrics (and the 4 always-on
  efficiency ones), thresholds and what a threshold can and cannot prove,
  trajectory match types (EXACT, IN_ORDER, ANY_ORDER, ignore_args), judge model
  options and sampling cost, rubric authoring
  including restraint rubrics that prove an agent held back, groundedness
  checks, safety and the other Vertex-only metrics, custom metrics, and
  cost-tiered configs for CI. Use when asked "which metric should I use", "set
  thresholds", "write rubrics", "add an LLM judge", "check groundedness or
  hallucinations", "test that the agent refuses", "why is this metric always
  passing", or to create eval_config.json / test_config.json. Don't use to write
  eval cases (use `/adk:eval-create`), to validate existing eval files (use
  `/adk:eval-review`), to run evals (use `/adk:eval-run`), or to set up simulated
  users (use `/adk:eval-simulation`).
argument-hint: "[agent-dir] [--tier pr|nightly]"
allowed-tools: Read, Grep, Glob, Write, Edit, AskUserQuestion, Skill, Bash(python3 *)
---

# Choose and configure ADK evaluation criteria

Every metric answers one specific question. Picking them is choosing which
questions the suite asks, and what each run costs. The two decisions that go
wrong most often: leaving `judge_model` unset (it defaults to a deprecated model
sampled five times), and gating pull requests on metrics that pay a model.

`${CLAUDE_SKILL_DIR}` is set by the skill runtime to this skill's own
directory. Use it verbatim in every command below; a sibling skill's scripts
are at `${CLAUDE_SKILL_DIR}/../<skill>/scripts/`.

## Flow

1. **Discover.** What exists, what the backend can serve, what the cases look
   like:

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/../adk/scripts/discover.py <agent-dir> --focus layout,env,agent,evalsets,configs
   python3 ${CLAUDE_SKILL_DIR}/scripts/validate_config.py <agent-dir>     # when a config exists
   ```

2. **Report.** Current criteria and their cost tier, the backend, whether cases
   carry reference responses, and what is missing.

3. **Options.** Usually a choice among:

   | Option | Cost | When |
   |---|---|---|
   | deterministic gate | free | there is no config, or CI needs something to run per change |
   | add semantic response matching | judge | wording is free but meaning is fixed |
   | add groundedness | judge | the agent repeats numbers and ids from tools |
   | add restraint guarantees | judge | the agent can do something it must sometimes refuse |
   | add safety | vertex | the agent talks to end users |

4. **Confirm**, then generate with `--dry-run` first, show the config, write it,
   and validate what you wrote.

## Decision rules

- **Start deterministic.** `tool_trajectory_avg_score` at 1.0 costs nothing, runs
  in seconds, and is the only metric that catches a skipped validation. Add paid
  metrics when there is a question they answer that this one cannot.
- **Pick the match type deliberately.** `EXACT` for a pipeline that must not
  vary. `IN_ORDER` when a schema retry or a self-correction is legitimate and
  should not fail the case. `ANY_ORDER` when several calls are genuinely
  unordered, such as parallel lookups.
- **Leave `ignore_args` off in the suite that gates releases.** With it on, the
  right tool called with the wrong claim id passes.
- **`response_match_score` compares wording, not meaning.** Set it to 0.0 for a
  trajectory-first suite, use 0.8 only where the reference text is genuinely
  stable, and reach for `final_response_match_v2` when the meaning is what
  matters.
- **Always set `judge_model` and `num_samples`.** Unset means `gemini-2.5-flash`
  — which this plugin's policy treats as retiring — at 5 samples per invocation.
  Use an alias id, and 3 samples for a majority vote at moderate cost.
- **Rubrics are yes/no claims.** No 1-5 scales: graded prompts make scores cluster
  in the middle, where they decide nothing. One property per rubric, named in
  terms a reader of the transcript could check.
- **Restraint needs its own metric.** Trajectory matching and response quality
  both pass an agent that does too much. `rubric_based_tool_use_quality_v1` at
  threshold **1.0** with "does NOT call X when Y" is the only check that fails it.
- **Groundedness is not correctness.** `hallucinations_v1` asks whether each
  sentence is supported by what the tools returned. Set
  `evaluate_intermediate_nl_responses` to true or only the last message is read.
- **Know which metrics go through the Vertex evaluation service.** `safety_v1`,
  `response_evaluation_score` and the `multi_turn_*` metrics need either a
  `GOOGLE_API_KEY` or a project and location with ADC, whatever
  `GOOGLE_GENAI_USE_VERTEXAI` says. They are the `vertex` tier because it is a
  separate, separately billed service that ignores `judge_model_options` — not
  because a key is insufficient.
- **The four efficiency metrics cannot be configured.** On ADK 2.10+
  `tool_call_count_v1`, `inference_call_count_v1`, `token_usage_v1` and
  `invocation_duration_v1` are appended to every eval automatically and reported
  as informational values. Naming one in `criteria` is an error at run time, so
  never write one into a config — there is no flag for it and nothing to add.
- **Check the range before the threshold.** `response_evaluation_score` runs 1 to
  5; a threshold of 0.8 on it can never fail.
- **Where the file goes matters.** `test_config.json` beside a single evalset is
  found automatically; anything else must be passed with `--config_file_path`.

## References

| Question | File |
|---|---|
| All 13 configurable metrics, plus the 4 always-on efficiency ones | [metric-catalog.md](references/metric-catalog.md) |
| Criterion fields, judge options, custom metrics, config shape | [criterion-schemas.md](references/criterion-schemas.md) |
| Writing rubrics a judge can answer consistently | [rubric-authoring.md](references/rubric-authoring.md) |
| Groundedness and safety in depth | [groundedness-and-safety.md](references/groundedness-and-safety.md) |
| What Vertex-backed metrics require | [vertex-requirements.md](references/vertex-requirements.md) |
| Writing a custom metric function ADK will call correctly | [custom-metrics.md](references/custom-metrics.md) |

Templates: `assets/templates/configs/` (one per criterion family) and
`assets/templates/rubrics/` (restraint, response quality, trajectory quality).

## Scripts

| Command | Does | Exit codes |
|---|---|---|
| `scripts/metric_info.py [name] [--tier pr\|nightly] [--user-sim] [--json]` | the catalog (17 entries: 13 configurable, 4 informational), or one metric in detail | 0, 2 unknown name |
| `scripts/gen_config.py --tier pr\|nightly\|custom [--metrics ...] [--rubrics-file F] [--judge-model M] [--num-samples N] [--style snake\|camel] [--out F] [--dry-run]` | writes a config and validates it. No tier emits an efficiency metric and `--metrics` refuses one (exit 2) — there is nothing to add, so there is no flag for it | 0, 1 contradictory, 2 usage |
| `scripts/validate_config.py <config\|agent-dir> [--agent-dir D] [--no-backend] [--no-probe] [--adk-version V] [--strict]` | checks an existing config, and reports the resolved `adk_version`: `CFG070` (error) for an efficiency metric named in `criteria`, `CFG071` (info) that 2.10+ reports the four anyway, `CFG072` (info) a `multi_run_config` only unreleased main reads and `CFG073` (error) a malformed one. `--no-probe` skips resolving the runner, `--adk-version` states it | 0 clean, 1 findings, 2 bad target |
| `scripts/gen_rubrics.py --kind positive\|restraint\|trajectory [--tool T --condition C] [--spec JSON] [--lint F]` | builds and lints rubrics | 0 clean, 1 findings, 2 usage |
| `scripts/gen_custom_metric.py --name N --out F [--kind sync\|async\|trajectory-precision\|trajectory-recall] [--with-args] [--threshold F] [--with-metric-info] [--dry-run]` | scaffolds a custom metric that reads `criterion.threshold` and returns per-invocation results, plus its `criteria` + `custom_metrics` config snippet (also kept as `CONFIG_SNIPPET`); the trajectory kinds score extra or missing tool calls as a fraction, multiset match, order ignored | 0 written, 1 refused, 2 usage |

## Completion checklist

- [ ] Every judge metric names its model and sample count explicitly.
- [ ] No Vertex-only metric is configured without a project and location, or it is
      deliberately confined to a nightly config.
- [ ] Thresholds are inside each metric's range, and none is accidentally 0.
- [ ] Rubrics pass the lint: binary, observable, one property each.
- [ ] The config is where the command that runs it will actually find it.
- [ ] `validate_config.py` exits 0 on the file that was written.
