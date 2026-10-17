"""The Agent-Plugins skill layer: `plugin.json` and per-skill `EVAL.yaml`.

`google/adk-recipes` ships installable vertical skills under
``plugins/<plugin>/`` (Agent Plugins v1.0.0): a ``plugin.json`` manifest and
``skills/<name>/{SKILL.md, EVAL.yaml, scripts/, references/, assets/}``. These
are *not* ADK `SkillToolset` skills - their `SKILL.md` names do not match their
directories, so `load_skill_from_dir` would refuse them - and the `EVAL.yaml`
next to each is not an ADK evalset. It is the plugin's own test sheet:

    cases:
    - id: <unique>
      prompt: "<what the user asks>"
      metadata: {skill_name: ..., products: [...]}
      rubric:                    # LLM-judge lines, MUST / MUST NOT / SHOULD
        - "The response MUST ..."
      assertions:                # optional deterministic checks
        - type: tool_called
          tool: retrieve_docs

There is no threshold key (the recipes state an 80% target in the skill's
prose) and no runner in the recipes repository; this module reads, counts and
validates the files and never executes anything.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .report import Finding
from .yaml_subset import YamlError, loads

#: Assertion types seen in google/adk-recipes `plugins/retail`, with the keys
#: each one needs.
ASSERTION_FIELDS: dict[str, tuple[str, ...]] = {
    "contains_product": ("field", "value"),
    "price_under": ("value",),
    "tool_called": ("tool",),
    "no_hallucination": ("forbidden",),
    "mentions_price": ("value",),
    "mentions_field": ("field",),
    "response_not_empty": (),
    "response_min_length": ("value",),
    "graceful_no_results": (),
    "skill_covers": ("topic",),
}
CASE_KEYS = ("id", "prompt", "metadata", "rubric", "assertions")
RUBRIC_MODAL = re.compile(r"\b(?:MUST|SHOULD)\b")

_SRC = "adk-recipes: plugins/retail/skills/*/EVAL.yaml"
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".adk"}


@dataclass
class EvalYaml:
    path: str
    doc: Any = None
    error: str | None = None

    @property
    def cases(self) -> list[Any]:
        if isinstance(self.doc, dict) and isinstance(self.doc.get("cases"), list):
            return self.doc["cases"]
        return []

    def summary(self) -> dict[str, Any]:
        rubric_lines = 0
        assertions = 0
        types: dict[str, int] = {}
        skills: set[str] = set()
        for case in self.cases:
            if not isinstance(case, dict):
                continue
            rubric = case.get("rubric")
            if isinstance(rubric, list):
                rubric_lines += len(rubric)
            for assertion in case.get("assertions") or []:
                if isinstance(assertion, dict):
                    assertions += 1
                    kind = str(assertion.get("type"))
                    types[kind] = types.get(kind, 0) + 1
            metadata = case.get("metadata")
            if isinstance(metadata, dict) and isinstance(metadata.get("skill_name"), str):
                skills.add(metadata["skill_name"])
        return {
            "path": self.path,
            "skill_dir": Path(self.path).parent.name,
            "readable": self.error is None,
            "cases": len(self.cases),
            "rubric_lines": rubric_lines,
            "assertions": assertions,
            "assertion_types": dict(sorted(types.items())),
            "skill_names": sorted(skills),
            # No EVAL.yaml in the recipes declares one; the 80% target is prose.
            "threshold": (self.doc or {}).get("threshold") if isinstance(self.doc, dict) else None,
        }


def load_eval_yaml(path: Path | str) -> EvalYaml:
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return EvalYaml(str(target), error=str(exc))
    try:
        return EvalYaml(str(target), doc=loads(text))
    except YamlError as exc:
        return EvalYaml(str(target), error=str(exc))


def _walk(root: Path, depth: int = 0, max_depth: int = 6) -> Iterable[Path]:
    if depth > max_depth:
        return
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.is_dir():
            if entry.name in SKIP_DIRS or entry.name.startswith("."):
                continue
            yield from _walk(entry, depth + 1, max_depth)
        else:
            yield entry


def find_eval_yamls(root: Path | str) -> list[str]:
    """Every `EVAL.yaml` that sits in a skill directory under `root` (or is `root`)."""
    target = Path(root)
    if target.is_file():
        return [str(target)] if target.name == "EVAL.yaml" else []
    return [str(p) for p in _walk(target) if p.name == "EVAL.yaml"]


def find_plugin_manifests(root: Path | str) -> list[str]:
    """`plugin.json` files that look like Agent-Plugins manifests."""
    target = Path(root)
    found: list[str] = []
    candidates = [target / "plugin.json"] if target.is_dir() else []
    if target.is_dir():
        candidates += [p for p in _walk(target, max_depth=3) if p.name == "plugin.json"]
    for candidate in dict.fromkeys(candidates):
        if candidate.is_file() and ((candidate.parent / "skills").is_dir()
                                    or "agent-plugins" in _read(candidate)):
            found.append(str(candidate))
    return found


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def plugin_layer(root: Path | str) -> dict[str, Any] | None:
    """What the plugin layer under `root` holds, or None when there is none."""
    manifests = find_plugin_manifests(root)
    evals = find_eval_yamls(root)
    if not manifests and not evals:
        return None
    plugins: list[dict[str, Any]] = []
    for manifest in manifests:
        try:
            data = json.loads(_read(Path(manifest)) or "{}")
        except json.JSONDecodeError:
            data = {}
        skills_dir = Path(manifest).parent / "skills"
        plugins.append({
            "path": manifest,
            "name": data.get("name") if isinstance(data, dict) else None,
            "version": data.get("version") if isinstance(data, dict) else None,
            "skills": sorted(p.name for p in skills_dir.iterdir()
                             if p.is_dir() and (p / "SKILL.md").is_file()) if skills_dir.is_dir() else [],
        })
    summaries = [load_eval_yaml(path).summary() for path in evals]
    return {
        "plugins": plugins,
        "eval_yamls": summaries,
        "cases": sum(s["cases"] for s in summaries),
        "rubric_lines": sum(s["rubric_lines"] for s in summaries),
        "assertions": sum(s["assertions"] for s in summaries),
        "threshold_declared": any(s["threshold"] is not None for s in summaries),
        "note": "EVAL.yaml is the plugin's own test sheet (LLM-judge rubric lines plus "
                "deterministic assertions); no ADK runner executes it and no file declares a "
                "threshold",
    }


def validate_eval_yaml(loaded: EvalYaml) -> list[Finding]:
    """EVY001-EVY010 for one file."""
    found: list[Finding] = []
    path = loaded.path

    def add(fid: str, severity: str, message: str, *, locator: str | None = None,
            fix: str | None = None) -> None:
        found.append(Finding(id=fid, severity=severity, area="eval_yaml", message=message,
                             path=path, locator=locator, fix=fix, source=_SRC))

    if loaded.error is not None:
        add("EVY001", "error", f"EVAL.yaml cannot be parsed: {loaded.error}",
            fix="fix the YAML; every case is a mapping under a top-level `cases:` list")
        return found
    doc = loaded.doc
    cases = doc.get("cases") if isinstance(doc, dict) else None
    if not isinstance(cases, list) or not cases:
        add("EVY002", "error",
            "no cases: the file needs a top-level `cases:` list with at least one case",
            fix="add `cases:` with one mapping per case (id, prompt, rubric, assertions)")
        return found

    skill_md = Path(path).parent / "SKILL.md"
    skill_text = _read(skill_md) if skill_md.is_file() else None
    seen: dict[str, int] = {}
    for index, case in enumerate(cases):
        locator = f"cases[{index}]"
        if not isinstance(case, dict):
            add("EVY003", "error", "case is not a mapping", locator=locator)
            continue
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            add("EVY003", "error", "case has no `id`", locator=locator,
                fix="give every case a unique snake_case id; it is how a failure is named")
        else:
            locator = f"{locator} ({case_id})"
            if case_id in seen:
                add("EVY005", "error", f"duplicate id {case_id!r} (also cases[{seen[case_id]}])",
                    locator=locator, fix="ids must be unique within the file")
            else:
                seen[case_id] = index
        prompt = case.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            add("EVY004", "error", "case has no `prompt`", locator=locator,
                fix="the prompt is the user's request; without it there is nothing to run")

        rubric = case.get("rubric")
        assertions = case.get("assertions")
        has_assertions = isinstance(assertions, list) and bool(assertions)
        if not isinstance(rubric, list) or not rubric:
            add("EVY006", "warn" if has_assertions else "error",
                "case has an empty `rubric`" + (
                    ", so only its deterministic assertions check it" if has_assertions
                    else " and no assertions, so nothing checks it"),
                locator=locator,
                fix="add one boolean line per requirement: \"The response MUST ...\" / "
                    "\"MUST NOT ...\" / \"SHOULD ...\"")
        else:
            for position, line in enumerate(rubric):
                if not isinstance(line, str) or not RUBRIC_MODAL.search(line):
                    add("EVY007", "warn",
                        f"rubric line {position + 1} has no MUST or SHOULD: "
                        f"{str(line)[:70]!r}",
                        locator=f"{locator}.rubric[{position}]",
                        fix="phrase each line as a yes/no requirement with MUST, MUST NOT or "
                            "SHOULD, so a judge can answer it without a scale")

        if assertions is None:
            continue
        if not isinstance(assertions, list):
            add("EVY008", "error", "`assertions` must be a list", locator=locator)
            continue
        for position, assertion in enumerate(assertions):
            where = f"{locator}.assertions[{position}]"
            if not isinstance(assertion, dict):
                add("EVY008", "error", "assertion is not a mapping", locator=where)
                continue
            kind = assertion.get("type")
            if kind not in ASSERTION_FIELDS:
                add("EVY008", "error", f"unknown assertion type {kind!r}", locator=where,
                    fix=f"use one of {', '.join(sorted(ASSERTION_FIELDS))}")
                continue
            missing = [key for key in ASSERTION_FIELDS[kind] if assertion.get(key) in (None, "", [])]
            if missing:
                add("EVY009", "error",
                    f"`{kind}` assertion is missing {', '.join(f'`{m}`' for m in missing)}",
                    locator=where,
                    fix=f"`{kind}` takes {', '.join(ASSERTION_FIELDS[kind])}")
            if kind == "tool_called" and isinstance(assertion.get("tool"), str) \
                    and skill_text is not None and assertion["tool"] not in skill_text:
                add("EVY010", "info",
                    f"`tool_called` names {assertion['tool']!r}, which the sibling SKILL.md "
                    "never mentions",
                    locator=where,
                    fix="check the spelling against the tool the skill tells the agent to "
                        "build; a name nothing produces can never be called")
    return found
