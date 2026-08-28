#!/usr/bin/env bash
# Prepare the eval session's working directory.
#
#   scaffold.sh [<fixture>|none] [<mutation>] [stage-history] [stage-traces]
#
# Fixtures live in tests/fixtures/agents/ and use an expense-claim domain, so a
# case can exercise a real project without touching the repository. `none`
# copies no agent, for cases that only need staged data.
#
# A mutation plants a specific defect, for cases whose prompt describes one:
#   bad-eval-set-id   an eval_set_id containing a dash and a dot
#
# Staging directives copy read-only fixture data into the sandbox, so a prompt
# can use a sandbox-relative path instead of reaching back into the plugin:
#   stage-history     tests/fixtures/eval_history -> ./fixtures/eval_history
#   stage-traces      tests/fixtures/traces       -> ./fixtures/traces
#
# It also writes ./.scaffold/, which the "nothing was clobbered" graders look
# for: manifest.txt records what ran, protected.sha256 records the checksum of
# every file a case must not modify.
#
# Requires `claude plugin eval --scaffold`; scaffold scripts are opt-in.
set -euo pipefail

plugin_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fixture=""
mutation=""
stage_history=0
stage_traces=0

for arg in "$@"; do
  case "${arg}" in
    stage-history) stage_history=1 ;;
    stage-traces)  stage_traces=1 ;;
    *)
      if [[ -z "${fixture}" ]]; then
        fixture="${arg}"
      elif [[ -z "${mutation}" ]]; then
        mutation="${arg}"
      else
        echo "unexpected argument: ${arg}" >&2
        exit 2
      fi
      ;;
  esac
done

fixture="${fixture:-recipes_good}"
mutation="${mutation:-none}"

if [[ "${fixture}" != "none" ]]; then
  source_dir="${plugin_root}/tests/fixtures/agents/${fixture}"
  if [[ ! -d "${source_dir}" ]]; then
    echo "no such fixture agent: ${fixture}" >&2
    exit 2
  fi
  cp -R "${source_dir}/." .
fi

case "${mutation}" in
  none) ;;
  bad-eval-set-id)
    python3 - <<'PY'
import json, pathlib
path = pathlib.Path("tests/eval/evalsets/happy.evalset.json")
doc = json.loads(path.read_text())
doc["eval_set_id"] = "happy-path.v2"
path.write_text(json.dumps(doc, indent=2) + "\n")
PY
    ;;
  *)
    echo "unknown mutation: ${mutation}" >&2
    exit 2
    ;;
esac

staged=""
if [[ "${stage_history}" -eq 1 ]]; then
  mkdir -p ./fixtures/eval_history
  cp -R "${plugin_root}/tests/fixtures/eval_history/." ./fixtures/eval_history/
  staged="${staged} eval_history"
fi
if [[ "${stage_traces}" -eq 1 ]]; then
  mkdir -p ./fixtures/traces
  cp -R "${plugin_root}/tests/fixtures/traces/." ./fixtures/traces/
  staged="${staged} traces"
fi

# Sentinel + checksums for the graders that assert nothing protected was touched.
mkdir -p .scaffold
{
  echo "fixture=${fixture}"
  echo "mutation=${mutation}"
  echo "staged=${staged# }"
} > .scaffold/manifest.txt

: > .scaffold/protected.sha256
for protected in tests/eval/evalsets/happy.evalset.json app/agent.py agent.py; do
  if [[ -f "${protected}" ]]; then
    python3 - "${protected}" >> .scaffold/protected.sha256 <<'PY'
import hashlib, sys
path = sys.argv[1]
print(f"{hashlib.sha256(open(path, 'rb').read()).hexdigest()}  {path}")
PY
  fi
done

echo "scaffolded ${fixture} (mutation: ${mutation}; staged:${staged:-" none"}) into $(pwd)"
