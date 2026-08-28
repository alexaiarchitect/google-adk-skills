---
max_turns: 12
allowed_tools: [Read, Glob, Grep, Skill, AskUserQuestion, "Bash(python3 *)"]
scaffold_script: ../scaffold.sh none stage-history
tags: [reliability]
---

I ran my eval suite five times. The results are in `fixtures/eval_history/`,
under the eval set `reliability`. Is my agent reliable enough to ship?
