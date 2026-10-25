# Managing an evalset that already exists

Authoring cases is the easy half. The half that decides whether a suite is still
trusted a year later is maintenance: keeping ids meaningful, keeping one concern
per case, not paying twice for the same assertion, and keeping the suite aligned
with an agent that keeps moving. This page covers the three routes that do that
work — the dev UI, the `adk eval_set` CLI, and this plugin's `manage_cases.py` —
and when each is the right one.

## Which route for which job

| Job | Route |
|---|---|
| turn a conversation you just had into a case | dev UI, "Add current session" |
| tweak an agent response inside a saved case | dev UI, "Edit current eval case" |
| create an empty set, or add scenario cases in bulk | `adk eval_set create` / `add_eval_case` |
| generate scenarios from a description | `adk eval_set generate_eval_cases` (see `/adk:eval-simulation`) |
| rename, move, split, dedupe, rebase app names | `manage_cases.py` |
| share history between CI and humans | `--eval_storage_uri gs://<bucket>` (`file://` and `ADK_EVAL_STORAGE_URI` only on main after 2.13.0) |

The dev UI is for *capture and inspection*; the CLI is for *bulk creation*;
`manage_cases.py` is for *refactoring*, which is the operation neither of the
other two offers.

## The dev-server Eval tab

```bash
adk web <agents-dir>
```

1. Pick the agent and have the conversation you want to pin.
2. Open the **Eval** tab on the right.
3. Create a new eval set, or select one that exists.
4. **Add current session** saves the whole conversation as one case.
5. Click a case id to inspect it. The pencil icon (**Edit current eval case**)
   lets you modify agent text responses, delete individual agent messages, or
   delete the case.
6. **Run Evaluation** opens a metric dialog with sliders for
   `tool_trajectory_avg_score` and `response_match_score`, and records the
   thresholds it used alongside the run.

Two cautions, both of which follow from what the UI is for:

- **The sliders are not your config.** They set thresholds for that one
  interactive run. The file CI reads is still `eval_config.json` /
  `test_config.json`; a threshold agreed in the UI has to be written into it by
  hand or with `/adk:eval-criteria`.
- **"Add current session" captures what happened, not what should happen.** If
  the run you just had contains a mistake, the case now specifies the mistake.
  Read the trajectory before you keep it — the same rule as any golden trace.

Results of a UI run land in the same place as a CLI run:
`<agent>/.adk/eval_history/*.evalset_result.json`.

Two 2.10 improvements to editing through the UI or the API. **An edited case keeps
its position** in the file — through 2.9 the manager removed the case and appended
the updated one, so every edit reordered the evalset and made the diff unreadable.
And the UI's own transcript indices (`invocationIndex`, `toolUseIndex`, written
onto each event) are now **dropped on save** instead of being rejected, because
`InvocationEvent` became `extra="ignore"`. `Invocation` itself still refuses
unknown keys.

## The `adk eval_set` CLI

Three subcommands, all taking the agent directory and an eval set id:

```bash
adk eval_set create <agent-dir> <eval_set_id>

adk eval_set add_eval_case <agent-dir> <eval_set_id> \
    --scenarios_file scenarios.json \
    --session_input_file session_input.json

adk eval_set generate_eval_cases <agent-dir> <eval_set_id> \
    --user_simulation_config_file generation_config.json
```

Details that bite:

- `add_eval_case` today only adds **scenario** cases — the `--scenarios_file`
  holds a serialized `ConversationScenarios`, and `--session_input_file` is
  **required**, not optional. Static conversation cases come from capture
  (`session_to_evalset.py`) or from `gen_cases.py`.
- Cases are keyed by a generated id, and an id that already exists is **skipped**
  rather than replaced. That is the same rule this plugin applies everywhere: an
  existing case is a specification.
- All three accept `--eval_storage_uri`, so a set can be created straight into a
  bucket rather than into the working tree.
- `adk eval` accepts either file paths **or** eval set ids managed by ADK, but
  not both in one command.

## Refactoring with `manage_cases.py`

Every mutator shows a write plan first, accepts `--dry-run`, re-validates the
result, and refuses to write over an `eval_id` that already exists.

| Situation | Command |
|---|---|
| an id says nothing about the behaviour | `rename <id> <better_id>` |
| a case sits in the wrong tier file | `move <id> --to <evalset>` |
| a case proves two things at once | `split <id>` |
| the same request was pasted twice | `dedupe [--apply]` |
| the agent directory was renamed | `rebase-app-name <name>` |
| a case is obsolete | `remove <id>` |

`split` deserves a note. It cuts a multi-turn case into `<id>_turn_1..N`, one
invocation each, and copies `session_input` and any per-case rubrics onto every
part. That is right for a case that bundled unrelated requests into one
conversation. It is **wrong** for a genuine context-retention test, where turn 3
is only meaningful because turn 1 happened — splitting one of those destroys the
thing it tests. Read the turns before splitting, and keep memory cases whole.

`dedupe` matches on the user text plus the expected tool calls and their
arguments. Two cases that match on both cost twice and prove once. Two cases
with the same text but different expected arguments are not duplicates, and it
will not touch them.

**Frozen suites.** A suite marked `"frozen": true` in `eval_registry.json` is a
golden baseline: `remove`, `rename`, `move`, `split`, `dedupe` and
`rebase-app-name` refuse
any of its cases with `EVS120` (exit 2) unless you pass `--unfreeze`. Adding
cases is allowed. Freezing exists because deleting the cases a change keeps
failing is the quickest way to a green suite and a worse agent; the rule and
when to lift it are in `/adk:eval-ci` `merge-gates.md`.

None of the mutators will open a legacy list-format file. The conversion loses
enough (ADK assigns a random eval set id on read) that it is its own decision:
run `migrate_legacy.py` first.

## Shared history with GCS

`--eval_storage_uri gs://<bucket>/<prefix>` is accepted by `adk web`, `adk eval`
and every `adk eval_set` subcommand. With it, both the eval **sets** and the
**results** move out of the working tree and into the bucket
(`GcsEvalSetsManager`, `GcsEvalSetResultsManager`), which is what makes a
nightly CI run and a person at a laptop share one history instead of two.
Through 2.13.0 only `gs://` is accepted; main after 2.13.0 also takes
`file:///abs/path` and reads `ADK_EVAL_STORAGE_URI` when the flag is omitted
(`/adk:eval-run` `gcs-and-live.md`), without migrating existing files.

Use it when the suite is the team's, not one engineer's. Keep the evalset files
in the repository as well when the suite is small: a file under version control
is diffable, reviewable in a pull request, and impossible to lose to a bucket
lifecycle rule. The two are not exclusive — the repository holds the
specification, the bucket holds the run history.

## `EVAL.yaml`: the recipes' plugin-skill format

Skills under `adk-recipes/plugins/<plugin>/skills/<skill>/` carry an `EVAL.yaml`
instead of an evalset. It is not an ADK format and `adk eval` cannot read it.
Its shape, as used by `plugins/retail` at `05811bba`:

```yaml
cases:
- id: headphones_under_200
  prompt: "I'm looking for wireless headphones under $200, what do you have?"
  metadata:
    skill_name: retail-product-search
    products: [Vector Search, BigQuery]
  rubric:
    - "The response MUST only include products priced under $200."
    - "The response SHOULD use semantic search to match the user intent."
  assertions:
    - {type: tool_called, tool: retrieve_docs}
    - {type: price_under, value: 200}
```

- One top-level key, `cases`. Each case has `id`, `prompt`, `metadata`
  (`skill_name`, `products`), `rubric` (a list of sentences phrased with MUST,
  MUST NOT and SHOULD, for an LLM judge) and optionally `assertions`.
- Assertion types in use: `contains_product {field, value}`, `price_under
  {value}`, `tool_called {tool}`, `no_hallucination {forbidden}`,
  `mentions_price {value}`, `mentions_field {field}`, `response_not_empty`,
  `response_min_length {value}`, `graceful_no_results`, `skill_covers {topic}`.
- **No threshold key and no runner.** The 80% target is prose in the skill's
  `SKILL.md`; the repository's CI only checks that the file exists.

`/adk:eval-review`'s `validate_eval_yaml.py` checks this shape (findings
`EVY001`–`EVY010`: parse errors, missing `cases`, `id` or `prompt`, duplicate ids, empty rubrics, rubric lines without MUST/SHOULD, unknown assertion types or missing fields, and a `tool_called` the `SKILL.md` never mentions) and never runs the cases. If you want the same
behaviours gated in an ADK project, translate each case: `prompt` becomes the
user turn, `tool_called` an expected tool use, the MUST sentences per-case
rubrics, and the threshold a decision you write into `eval_config.json`.

## Versioning an evalset with its agent

An evalset is a specification of the agent it was written against, so the two
version together:

- **Commit them in the same change.** A tool rename plus the case updates that
  follow it belong in one commit, with the reason in the message. Reviewers can
  then see that the specification moved deliberately.
- **Never edit a case to make a run pass.** Understand why the behaviour changed
  first. If the new behaviour is correct, change the case and say so; if it is
  not, the case just did its job.
- **Split files by tier, not by topic.** The useful boundary is what a change
  can afford to run: a cheap deterministic set that gates every pull request,
  and a judged set that runs nightly. Topic-shaped files (`claims.evalset.json`,
  `refunds.evalset.json`) look tidy and give CI no way to pick a cheap subset.
- **Keep regression cases forever.** They are the only cases whose value is
  entirely historical, and they are the first ones people delete when a file
  gets long.

Sources: `adk-docs/docs/evaluate/index.md` (Eval tab flow, "Add current
session", "Edit current eval case", the metric dialog, `adk eval` arguments);
`adk-python/src/google/adk/cli/cli_tools_click.py` (`eval_set create`,
`add_eval_case`, `generate_eval_cases` signatures and `--eval_storage_uri`);
`adk-python/src/google/adk/evaluation/local_eval_sets_manager.py` (id rules and
the skip-on-existing behaviour);
`adk-python/src/google/adk/evaluation/_eval_sets_manager_utils.py`
(`update_eval_case_in_eval_set`, in place on 2.10+ and remove-then-append on 2.9);
`adk-python/src/google/adk/evaluation/eval_case.py`
(`InvocationEvent.model_config`, `extra="ignore"` since 2.10); `adk-python/src/google/adk/evaluation/
gcs_eval_sets_manager.py` and `gcs_eval_set_results_manager.py` (GCS storage).
`adk-recipes: plugins/retail/skills/*/EVAL.yaml`,
`docs/recipe-handbook/plugins.md` (`05811bba`); Ippolito and Saadati, "Three
layers, one repo: a tour of google/adk-recipes", Google Developers Blog,
2026-10-15,
https://developers.googleblog.com/three-layers-one-repo-a-tour-of-googleadk-recipes/.
Background reading: the LinkedIn Learning course "Evaluating AI Agents with
Google ADK", which covers the dev-UI evaluation workflow.
