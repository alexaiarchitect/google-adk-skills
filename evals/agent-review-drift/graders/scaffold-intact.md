---
type: file_exists
path: .scaffold/protected.sha256
---

`scaffold.sh` writes `.scaffold/protected.sha256` with a checksum of every file
this case must not modify. Its presence is the cheap check that the sandbox was
set up and then left standing — a session that deleted or rebuilt the tree loses
it.

This grader needs `--scaffold`; without it the scaffold never runs and the file
is absent.
