# Plugin eval cases

Behavioural tests for the **skills**, not for your agents. Each case runs a prompt
in a session with only this plugin loaded, and grades what the model did. They run
with `claude plugin eval` (Claude Code 2.1.318 or later; the case layout below is
what that version loads).

The smoke run, one pass per case, with a no-plugin baseline arm beside each:

```bash
claude plugin eval . --scaffold --allow-tools Bash Write Edit --trust-plugin \
  --runs 1 --ablation with-without --max-cost-usd 30 --threshold 0.8 \
  --no-publish --json results.json
```

Narrower runs: `--case routing-ambiguous` (a name glob) or `--tag smoke`. The last
recorded run is in [`LAST-RUN.md`](LAST-RUN.md).

**`--scaffold` and `--allow-tools` are both required, and neither is a default.**

- `--scaffold` runs each case's `scaffold_script`. It is opt-in because a
  scaffold script is author-supplied bash that runs as you. Without it, every
  case that needs a project starts in an empty directory (the loader warns "its
  scaffold_script is not run without --scaffold"), and the `scaffold-intact`
  graders fail because the sentinel they look for was never written.
- `--allow-tools Bash Write Edit` is the operator grant for gated tools. Every
  case runs the plugin's scripts through `Bash`; the authoring cases also write
  files. Without the grant those calls are refused and the cases fail for the
  wrong reason.
- `--trust-plugin` answers the first-run trust question, which a `--json` run
  cannot stop to ask; without it the run exits 1 before loading anything.
- `--max-cost-usd` is a hard ceiling checked before each run starts; hitting it
  exits 2 with partial results. `--max-cost-usd 0` loads and validates every case
  and stops before the first run, at no cost: the quickest check that a case
  file is well formed.

Results land in `evals/results/<timestamp>/`, which is gitignored; keep the JSON
and HTML report somewhere else if you want to compare runs.

## Case layout

```
evals/<case>/
  case.yaml        schema_version, name, context.scaffold_script
  scaffold.sh      three lines: runs ../scaffold.sh with this case's arguments
  prompt.md        frontmatter (max_turns, allowed_tools, tags) + the prompt
  graders/*.md     one grader per file: frontmatter (type, ...) + what it proves
```

```yaml
# case.yaml
schema_version: "1.0"
name: evalset-validate
context:
  scaffold_script: scaffold.sh
```

```bash
# evals/evalset-validate/scaffold.sh
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../scaffold.sh" recipes_good bad-eval-set-id
```

The two files combine: `prompt.md`'s frontmatter maps onto the case's
`execution` settings and its body is the prompt. `prompt.md` accepts only
`schema_version, name, description, tags, plugins, runs, expected_outcome,
model, max_turns, timeout_seconds, allowed_tools, disallowed_tools,
artifact_publish, growthbook_overrides, append_system_prompt, env`; any other key
(including `scaffold_script`, which older suites put there) fails the case at
load. `scaffold_script` is read only from `context:` in `case.yaml`.

Why a per-case `scaffold.sh` instead of `../scaffold.sh recipes_good`: Claude
Code 2.1.318 treats the whole `scaffold_script` value as one path, resolves it
against the case directory, refuses one that leaves it (`..` or an absolute
path: "escapes the case directory"), and runs it as `bash <path>` with no
arguments, in the run's empty working directory with only `PATH`, `HOME` and
`TMPDIR` set. The loader does not check the path, so a `--max-cost-usd 0` load
passes either way; the mistake shows only when the first run starts. The
wrapper lives inside the case directory and passes the arguments itself.

## Reading a with/without run

Under `--ablation with-without` each case runs twice: once with the plugin, once
in a clean session without it, and the report gives both scores and the delta.

- **Delta 0, both arms low** — the skill never fired. Check the `skill-fired`
  indicator first: the description did not win the routing decision, so nothing
  the skill says was ever read. Fix the description before anything else.
- **Both arms pass, equally fast** — the model already knows how to do this
  without the skill; the skill is a context tax (its description is in every
  session's catalog and earns nothing). Delete it, or narrow it to what the
  baseline gets wrong.
- **With passes, without fails** — the skill earns its place; this is the case
  to protect.

The `skill-fired` graders are `tool_used: Skill` with an `input_match` that names
the one skill the case must load (`"skill"\s*:\s*"(?:adk:)?eval-review"`; the
router case names `adk`). Under with-without ablation they are a with-only
**indicator**, reported beside the score, not part of it: the baseline arm has no
plugin to fire. A case whose score passes while its indicator says the wrong
skill (or none) fired passed for a reason the case was not written to test.

## What each case pins

| Case | Asserts |
|---|---|
| `routing-ambiguous` | a vague request goes to the router, which discovers before advising |
| `evalset-validate` | a validation request reaches `eval-review`, runs the validator before pronouncing, and finds the planted error |
| `criteria-choice` | metric advice comes with cost, and nothing is written unasked |
| `create-edge-cases` | coverage is measured before cases are written, and no existing case is edited |
| `agent-review-drift` | a 2.x review reports the drift and edits nothing, with `Write`/`Edit` available |
| `analyze-results` | a failure is explained by quadrant and divergence, not by score alone |
| `pass-at-k` | reliability comes from repeated runs, read as pass@1 |
| `debug-loop` | a looping trace is classified and assigned a layer |
| `ci-setup` | CI gates on a wrapper, never on bare `adk eval`, and a workflow file actually lands |
| `simulation` | simulated users bring the metric-compatibility constraint with them |

## Shared setup

`scaffold.sh` prepares the session's working directory so nothing in the
repository is touched:

```
scaffold.sh [<fixture>|none] [<mutation>] [stage-history] [stage-traces]
```

- **fixture** — copies an agent from `tests/fixtures/agents/`. `none` copies no
  agent, for cases that only need data.
- **mutation** — plants a specific defect the prompt describes
  (`bad-eval-set-id`).
- **stage-history** / **stage-traces** — copy `tests/fixtures/eval_history` and
  `tests/fixtures/traces` into `./fixtures/` inside the sandbox. Prompts then use
  sandbox-relative paths (`fixtures/eval_history/`, `fixtures/traces/loop.jsonl`)
  instead of reaching back into the plugin directory with `../../`, which is
  fragile and lets a case accidentally read the real repository.

It also writes `.scaffold/manifest.txt` (what ran) and `.scaffold/protected.sha256`
(a checksum per file the case must not modify).

## How the "nothing was touched" graders work

Two cases assert that something *did not* happen, which an llm judge cannot see
from a transcript summary. They use two free graders instead:

- a `regex` grader over `target: trace` with `match: not_contains`, failing if
  any `Edit` or `Write` call in the execution trace names the protected file;
- a `regex` grader over `target: {source: file, path: .scaffold/protected.sha256}`,
  the sentinel the scaffold leaves behind, passing when it still holds a
  checksum line. Not `file_exists`: that grader (like `target: files`) sees only
  files created during the run, and the scaffold writes the sentinel before the
  run starts, so `file_exists` on it always fails.

The regex carries the real assertion — the sentinel only proves the sandbox was
set up and survived. Each grader file says what it proves and what it does not.

## Grader types in use

`llm` (a judged rubric), `tool_used` (the skill fired), `tool_order` (the
validator ran before the verdict), `file_exists` (a deliverable the run created
landed), and `regex` over the trace (something did not happen) or over a file in
the sandbox (the scaffold's sentinel survived).
