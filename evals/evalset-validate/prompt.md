---
max_turns: 12
allowed_tools: [Read, Glob, Grep, Skill, AskUserQuestion, "Bash(python3 *)"]
scaffold_script: ../scaffold.sh recipes_good bad-eval-set-id
tags: [validation]
---

Is `tests/eval/evalsets/happy.evalset.json` valid? I renamed its `eval_set_id` to
`happy-path.v2` earlier and I want to be sure nothing else is wrong with it.
