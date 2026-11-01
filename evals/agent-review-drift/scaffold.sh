#!/usr/bin/env bash
# `claude plugin eval --scaffold` runs context.scaffold_script as `bash <path>`:
# a path inside this case directory, with no arguments (2.1.318 rejects `..`).
# This hands the case's arguments to the shared script one level up.
set -euo pipefail
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../scaffold.sh" flat_drift
