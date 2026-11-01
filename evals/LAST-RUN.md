# Last plugin eval run

Claude Code 2.1.318; `claude plugin eval . --scaffold --allow-tools Bash Write Edit --trust-plugin --runs 1 --ablation with-without --max-cost-usd 30 --threshold 0.8 --no-publish -j 2`; 520 s, USD 4.18, partial=False.

Aggregate: 6/10 cases at or above 0.8 with the plugin, overall score 0.65, mean with-minus-without delta +0.05.

| Case | with | without | delta | turns with/without | skill fired | failed graders (with) |
|---|---|---|---|---|---|---|
| `agent-review-drift` | 1.0 | 1.0 | +0.0 | 5/4 | yes | - |
| `analyze-results` | 0.0 | 1.0 | -1.0 | 5/7 | yes | criteria |
| `ci-setup` | 0.5 | 0.5 | +0.0 | 8/7 | yes | criteria |
| `create-edge-cases` | 1.0 | 1.0 | +0.0 | 16/8 | yes | - |
| `criteria-choice` | 1.0 | 0.0 | +1.0 | 5/4 | yes | - |
| `debug-loop` | 0.0 | 0.0 | +0.0 | 7/4 | yes | criteria |
| `evalset-validate` | 1.0 | 0.5 | +0.5 | 5/11 | yes | - |
| `pass-at-k` | 1.0 | 1.0 | +0.0 | 7/5 | yes | - |
| `routing-ambiguous` | 1.0 | 1.0 | +0.0 | 4/4 | yes | - |
| `simulation` | 0.0 | 0.0 | +0.0 | 15/10 | yes | criteria (run error: exit 1: Reached maximum number of turns (14)) |

## Reading it

- A delta of zero with both arms passing (`agent-review-drift`, `create-edge-cases`, `pass-at-k`, `routing-ambiguous`) means the criteria grader is satisfied by the base model too; the plugin's contribution there is the deterministic script run (the `skill-fired` indicator confirms the skill loaded), which only `evalset-validate` grades with `script-before-verdict`. Adding a script-use grader to those cases is the next step, not deleting the skills.
- `criteria-choice` and `evalset-validate` are where the plugin lifts the score: cost-aware metric advice, and running the validator before pronouncing.
- `ci-setup` and `debug-loop` fail in both arms: the skill ran (`skill-fired` yes) but the reply left out parts the grader demands (the per-version `adk eval` exit-code story and the required repository variables; the returned-not-raised tool error and a retry limit). Those are skill-behaviour gaps to fix in the SKILL.md guidance.
- `analyze-results` passed one judge vote of three with the plugin while naming the skipped tool and the trajectory-versus-response combination; see the rerun below.
- `simulation` reached the 14-turn limit with the plugin (the generator was validating against the wrong config when it ran out); the case now allows 24 turns.

## Rerun of the four failing cases (plugin arm only, two runs each)

`--runs 2 --ablation none --tag analysis ci debug simulation --max-cost-usd 12`; 264 s, USD 2.38, partial=False.

| Case | run scores | failed graders per run |
|---|---|---|
| `analyze-results` | 1.0, 1.0 | -; - |
| `ci-setup` | 0.7, 0.7 | criteria; criteria |
| `debug-loop` | 1.0, 0.5 | -; criteria |
| `simulation` | 0.5, 0.5 | criteria; criteria |

`analyze-results` passed both reruns, so the smoke failure was a 2-1 judge vote. `debug-loop` passed one of two: the failing reply stopped to ask which diagnostic to run before stating the returned-not-raised error and a retry limit. `ci-setup` and `simulation` failed consistently: the skill did the work (workflow written, scenario cases generated) but the reply omitted what the grader asks for, so both SKILL.md completion checklists now require the reply to state it.

## After the SKILL.md checklist change (plugin arm only, one run each)

`--runs 1 --ablation none --tag ci simulation --max-cost-usd 6`; 86 s, USD 0.63, partial=False.

| Case | run scores | failed graders per run |
|---|---|---|
| `ci-setup` | 1.0 | - |
| `simulation` | 1.0 | - |

Results JSON and HTML stay outside the repository (`evals/results/` is gitignored); rerun the command above to refresh this file.
