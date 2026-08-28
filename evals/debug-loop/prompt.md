---
max_turns: 12
allowed_tools: [Read, Glob, Grep, Skill, AskUserQuestion, "Bash(python3 *)"]
scaffold_script: ../scaffold.sh recipes_good stage-traces
tags: [debug]
---

My agent keeps calling the same tool over and over and never finishes. I captured
a trace at `fixtures/traces/loop.jsonl`. What is going on and what should I
change?
