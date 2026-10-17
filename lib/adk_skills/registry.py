"""The eval registry (`eval_registry.json`) and frozen suites.

A registry names the suites a repository runs, by tier. A suite may be marked
`"frozen": true`: its cases are the golden baseline, and editing one while
tuning the agent is how a suite quietly stops measuring anything - the case
that failed gets "adjusted" until it passes. `manage_cases.py` therefore
refuses to rename, move, split, remove, dedupe or re-point a case in a frozen
suite (EVS120) unless told `--unfreeze`, which is the explicit, reviewable act
of revising a baseline.

    {"schema": "adk-skills.registry/1",
     "suites": [{"name": "golden", "tier": "pr", "frozen": true,
                 "agent_dir": "app", "evalsets": ["tests/eval/evalsets/*.evalset.json"]}]}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA = "adk-skills.registry/1"
REGISTRY_NAME = "eval_registry.json"
#: How far up from an evalset to look for the registry that governs it.
MAX_PARENTS = 8


def resolve_paths(base: Path, patterns: list[str]) -> list[str]:
    """A suite's `evalsets` entries, globbed and resolved against the manifest."""
    found: list[str] = []
    for pattern in patterns:
        if any(ch in pattern for ch in "*?["):
            found.extend(str(p.resolve()) for p in sorted(base.glob(pattern)))
        else:
            found.append(str((base / pattern).resolve()))
    return found


def find_registry(start: Path | str) -> Path | None:
    """The nearest `eval_registry.json` at or above `start`."""
    here = Path(start).resolve()
    if here.is_file():
        here = here.parent
    for folder in [here, *list(here.parents)[:MAX_PARENTS]]:
        candidate = folder / REGISTRY_NAME
        if candidate.is_file():
            return candidate
    return None


def load_registry(path: Path | str) -> dict[str, Any] | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return None
    return data


def frozen_suites_for(evalset: Path | str, registry: Path | str | None = None) -> tuple[list[str], str | None]:
    """(names of frozen suites that include `evalset`, the registry consulted)."""
    target = Path(evalset).resolve()
    registry_path = Path(registry) if registry else find_registry(target)
    if registry_path is None:
        return [], None
    manifest = load_registry(registry_path)
    if not manifest:
        return [], str(registry_path)
    base = registry_path.resolve().parent
    names: list[str] = []
    for suite in manifest.get("suites") or []:
        if not isinstance(suite, dict) or suite.get("frozen") is not True:
            continue
        if str(target) in resolve_paths(base, list(suite.get("evalsets") or [])):
            names.append(str(suite.get("name") or "?"))
    return names, str(registry_path)
