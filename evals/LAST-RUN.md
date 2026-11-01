# Last plugin eval run

Claude Code 2.1.318; `claude plugin eval . --scaffold --allow-tools Bash Write Edit --trust-plugin --runs 1 --ablation with-without --max-cost-usd 10 --threshold 0.8 --no-publish -j 2`; 495 s, USD 4.04, partial=False.

Aggregate: 9/10 cases at or above 0.8 with the plugin, overall score 0.90, mean with-minus-without delta +0.55.

| Case | with | without | delta | turns with/without | skill fired | failed graders (with) |
|---|---|---|---|---|---|---|
| `agent-review-drift` | 1.0 | 1.0 | +0.0 | 5/4 | yes | - |
| `analyze-results` | 1.0 | 1.0 | +0.0 | 5/5 | yes | - |
| `ci-setup` | 1.0 | 0.5 | +0.5 | 9/6 | yes | - |
| `create-edge-cases` | 1.0 | 1.0 | +0.0 | 11/8 | yes | - |
| `criteria-choice` | 1.0 | 0.0 | +1.0 | 7/3 | yes | - |
| `debug-loop` | 0.0 | 0.0 | +0.0 | 7/3 | yes | criteria |
| `evalset-validate` | 1.0 | 0.0 | +1.0 | 6/9 | yes | - |
| `pass-at-k` | 1.0 | 0.0 | +1.0 | 8/4 | yes | - |
| `routing-ambiguous` | 1.0 | 0.0 | +1.0 | 4/4 | yes | - |
| `simulation` | 1.0 | 0.0 | +1.0 | 19/11 | yes | - |

## Reading it

- Delta zero with both arms passing: `agent-review-drift`, `analyze-results` and `create-edge-cases`. The base model satisfies the criteria grader there too; the plugin's contribution is the deterministic script run, which only a script-use grader measures.
- Delta zero with both arms below 0.8: `debug-loop`. The plugin did not change the outcome; read the failed graders below.
- The plugin lifts the score on `ci-setup` (0.5 to 1.0), `criteria-choice` (0.0 to 1.0), `evalset-validate` (0.0 to 1.0), `pass-at-k` (0.0 to 1.0), `routing-ambiguous` (0.0 to 1.0) and `simulation` (0.0 to 1.0).
- `debug-loop` is below 0.8 with the plugin (0.0): failed graders criteria.

## Compared with the previous run

| Case | with before | with now | change | without before | without now | change |
|---|---|---|---|---|---|---|
| `agent-review-drift` | 1.0 | 1.0 | +0.0 | 1.0 | 1.0 | +0.0 |
| `analyze-results` | 0.0 | 1.0 | +1.0 | 1.0 | 1.0 | +0.0 |
| `ci-setup` | 0.5 | 1.0 | +0.5 | 0.5 | 0.5 | +0.0 |
| `create-edge-cases` | 1.0 | 1.0 | +0.0 | 1.0 | 1.0 | +0.0 |
| `criteria-choice` | 1.0 | 1.0 | +0.0 | 0.0 | 0.0 | +0.0 |
| `debug-loop` | 0.0 | 0.0 | +0.0 | 0.0 | 0.0 | +0.0 |
| `evalset-validate` | 1.0 | 1.0 | +0.0 | 0.5 | 0.0 | -0.5 |
| `pass-at-k` | 1.0 | 1.0 | +0.0 | 1.0 | 0.0 | -1.0 |
| `routing-ambiguous` | 1.0 | 1.0 | +0.0 | 1.0 | 0.0 | -1.0 |
| `simulation` | 0.0 | 1.0 | +1.0 | 0.0 | 0.0 | +0.0 |

With the plugin, `analyze-results`, `ci-setup` and `simulation` scored higher than last time. A single run per arm moves with one judge vote, so treat a 0.5 step as a prompt to rerun, not a verdict.

## After the agent-debug checklist change (plugin arm only, two runs)

`--runs 2 --ablation none --tag debug --max-cost-usd 2`; 40 s, USD 0.36, partial=False.

| Case | run scores | failed graders per run |
|---|---|---|
| `debug-loop` | 1.0, 1.0 | -; - |

Results JSON and HTML stay outside the repository (`evals/results/` is gitignored); rerun the command above to refresh this file.
