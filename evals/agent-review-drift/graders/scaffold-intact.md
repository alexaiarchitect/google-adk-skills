---
type: regex
target: {source: file, path: .scaffold/protected.sha256}
pattern: '[0-9a-f]{64}  \S'
match: contains
---

`scaffold.sh` writes `.scaffold/protected.sha256` with a checksum of every file
this case must not modify. Its presence is the cheap check that the sandbox was
set up and then left standing — a session that deleted or rebuilt the tree loses
it.

It reads the file itself rather than using `file_exists`: `file_exists` (like
`target: files`) sees only files created during the run, and the scaffold writes
this one before the run starts, so a `file_exists` check on it always fails.

This grader needs `--scaffold`; without it the scaffold never runs and the file
is absent.
