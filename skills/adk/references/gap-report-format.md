# The gap report

Every inspection script in this plugin prints the same structure, as markdown
for the user (`--format md`) or JSON for a skill to branch on (`--json`).

## Shape

```json
{
  "schema": "adk-skills.gap-report/1",
  "subject": "/path/that/was/inspected",
  "skill": "eval-review",
  "discovered": {"layout": {...}, "environment": {...}, "evalsets": {...}},
  "findings": [
    {
      "id": "EVS033",
      "severity": "warn",
      "area": "evalset",
      "path": "tests/eval/evalsets/happy.evalset.json",
      "locator": "eval_cases[2] (happy_claim_within_policy)",
      "message": "what is wrong, in one sentence",
      "fix": "what to do instead, and why it matters",
      "source": "adk-python: evaluation/eval_case.py"
    }
  ],
  "gaps": [{"id": "GAP-BRANCH", "what": "...", "why": "...", "recommendation": "..."}],
  "options": [{"key": "1", "label": "...", "recommended": true, "cost": "free",
               "writes": ["..."], "runs": ["..."]}],
  "summary": {"errors": 1, "warnings": 3, "infos": 2}
}
```

## Findings versus gaps

A **finding** is something present and wrong: a schema violation, a deprecated
API, a threshold that can never fail. A **gap** is something absent: no
adversarial case, no config, no CI. Findings have a location; gaps have a
recommendation.

## Severity

| | Meaning | Example |
|---|---|---|
| 🔴 `error` | ADK will reject it, or the check it implies cannot work | evalset missing `eval_set_id`; Vertex metric with no project configured |
| 🟠 `warn` | It runs, but not the way you meant, or it will break later | deprecated model id; per-case rubric with no `type`, so nothing reads it |
| 🔵 `info` | Worth knowing, no action implied | a case that asserts trajectory only |

The distinction that matters: ADK's eval models **accept unknown keys and ignore
them**. A misspelled criterion field or eval-case key raises nothing and changes
nothing, which is why those are warnings here rather than silent successes.

## Finding id prefixes

| Prefix | Area | Emitted by |
|---|---|---|
| `ENV###` | environment, runner, layout | discovery, `check_env.py` |
| `AGT###` | agent code, tools, state | `lint_agent.py`, discovery |
| `EVS###` | evalset files | `validate_evalset.py`, discovery |
| `CFG###` | eval config, criteria, judge options, custom metrics | `validate_config.py`, discovery |
| `RUB###` | rubric wording | rubric lint, inside `CFG`/`EVS` checks |
| `RES###` | results, statuses | `summarize_results.py` |
| `CI###` | workflows, gating | `lint_workflows.py` |

Ids are stable across releases, so a skill may branch on them and a test may
assert them.

## Exit codes

Identical in every script:

| Code | Meaning |
|---|---|
| 0 | no errors |
| 1 | errors found, or evals failed |
| 2 | usage or environment error (bad path, no runner) |
| 3 | inconclusive — results exist but only `NOT_EVALUATED`, under a `warn` policy |

Code 3 exists because "the judge could not be reached" is a different problem
from "the agent regressed", and a CI gate should be able to treat them
differently.

---

Sources: this plugin's `lib/adk_skills/report.py`.
