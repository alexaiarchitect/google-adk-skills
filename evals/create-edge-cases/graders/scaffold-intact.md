---
type: file_exists
path: .scaffold/protected.sha256
---

`scaffold.sh` writes `.scaffold/protected.sha256` with a checksum of every file
this case must not modify, `tests/eval/evalsets/happy.evalset.json` among them.
Its presence is the cheap check that the sandbox survived the session intact.

This grader needs `--scaffold`; without it the scaffold never runs and the file
is absent.
