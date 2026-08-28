# Plugin eval cases

Behavioural tests for the **skills**, not for your agents. Each case runs a prompt
in a session with only this plugin loaded, and grades what the model did.

```bash
claude plugin eval . --scaffold --allow-tools Bash Write Edit
claude plugin eval . --scaffold --allow-tools Bash Write Edit --case routing --runs 1
claude plugin eval . --scaffold --allow-tools Bash Write Edit --json results.json --threshold 0.8
```

**Both flags are required, and neither is a default.**

- `--scaffold` runs each case's `scaffold_script`. It is opt-in because a
  scaffold script is author-supplied bash that runs as you. Without it, every
  case that needs a project starts in an empty directory, and the
  `scaffold-intact` graders fail because the sentinel they look for was never
  written.
- `--allow-tools Bash Write Edit` is the operator grant for gated tools. Every
  case runs the plugin's scripts through `Bash`; the authoring cases also write
  files. Without the grant those calls are refused and the cases fail for the
  wrong reason.

`claude plugin eval` is an early-access feature. If it is not enabled for your
account, these cases still serve as a written specification of what each skill
should do — every one of them names the skill that must fire and the script it
must run.

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
- a `file_exists` grader on `.scaffold/protected.sha256`, the sentinel the
  scaffold leaves behind.

The regex carries the real assertion — the sentinel only proves the sandbox was
set up and survived. Each grader file says what it proves and what it does not.

## Grader types in use

`llm` (a judged rubric), `tool_used` (the skill fired), `tool_order` (the
validator ran before the verdict), `file_exists` (a deliverable landed), and
`regex` over the trace (something did not happen).
