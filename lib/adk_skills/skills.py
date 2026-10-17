"""Agent Skills as ADK loads them: `SKILL.md` frontmatter, body and resources.

An ADK skill has three layers, and each is loaded at a different time:

* **L1 metadata** - the frontmatter's `name` and `description`. Always in the
  context (as the `list_skills` result, or injected eagerly), so the description
  is what decides whether the model ever activates the skill.
* **L2 instructions** - the `SKILL.md` body, sent when the model calls
  `load_skill(skill_name=...)`.
* **L3 resources** - files under `references/`, `assets/` and `scripts/`,
  fetched one at a time with `load_skill_resource` / `run_skill_script`.

The checks here mirror what ADK enforces when it builds a `Frontmatter`
(`skills/models.py`) and what `load_skill_from_dir` adds (`skills/_utils.py`):
`name` lowercase kebab-case of at most 64 characters, `description` non-empty
and at most 1,024, `compatibility` at most 500, `metadata.adk_additional_tools`
a list and `metadata.adk_inject_state` a bool, and - for a directory - the name
equal to the directory name. A violation raises at import, so the agent never
loads. The size guidance (instructions under ~500 lines / ~5k tokens, a
description long enough to activate on) is advice, not enforcement.

Stdlib only; the frontmatter is read with :mod:`adk_skills.yaml_subset`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .yaml_subset import YamlError, loads

#: `Frontmatter._validate_name`: at most 64 characters, lowercase kebab-case.
MAX_NAME_CHARS = 64
#: `Frontmatter._validate_description`.
MAX_DESCRIPTION_CHARS = 1024
#: `Frontmatter._validate_compatibility`.
MAX_COMPATIBILITY_CHARS = 500
#: The guidance for L2: keep the body short and move detail into references/.
MAX_INSTRUCTION_LINES = 500
MAX_INSTRUCTION_TOKENS = 5000
CHARS_PER_TOKEN = 4
#: Below this a description rarely says both what the skill does and when to
#: use it, which is all the model has to decide on activation.
MIN_DESCRIPTION_CHARS = 40

KEBAB_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SNAKE_NAME = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")

#: Keys `Frontmatter` declares; anything else is kept (extra="allow").
FRONTMATTER_KEYS = ("name", "description", "license", "compatibility",
                    "allowed_tools", "allowed-tools", "metadata")
#: Metadata keys ADK itself reads.
ADK_METADATA_KEYS = ("adk_additional_tools", "adk_inject_state")
RESOURCE_DIRS = ("references", "assets", "scripts")

SRC_MODELS = "adk-python: skills/models.py (Frontmatter validators)"
SRC_UTILS = "adk-python: skills/_utils.py (_load_skill_from_dir)"


@dataclass
class SkillInfo:
    """One skill an agent registers, as far as the source shows it."""

    name: str | None
    #: inline (`models.Skill(...)`), dir (`load_skill_from_dir` /
    #: `load_skills_from_dir`), registry or gcs (decided at runtime).
    source: str
    path: str | None = None
    description: str | None = None
    instructions: str | None = None
    resources: dict[str, int] = field(default_factory=lambda: {k: 0 for k in RESOURCE_DIRS})
    metadata: dict[str, Any] = field(default_factory=dict)
    extra_keys: list[str] = field(default_factory=list)
    #: What ADK would reject, one phrase each. Non-empty means it never loads.
    problems: list[str] = field(default_factory=list)
    file: str | None = None
    lineno: int = 0
    #: The text is not statically readable (an expression ADK evaluates).
    unreadable: bool = False

    @property
    def description_chars(self) -> int:
        return len(self.description or "")

    @property
    def instruction_lines(self) -> int:
        return len((self.instructions or "").splitlines())

    @property
    def instruction_chars(self) -> int:
        return len(self.instructions or "")

    @property
    def instruction_tokens_estimate(self) -> int:
        return self.instruction_chars // CHARS_PER_TOKEN

    @property
    def extra_metadata_keys(self) -> list[str]:
        """Metadata keys beyond the two ADK reads itself."""
        return sorted(k for k in self.metadata if k not in ADK_METADATA_KEYS)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "path": self.path,
            "description_chars": self.description_chars,
            "instruction_lines": self.instruction_lines,
            "instruction_chars": self.instruction_chars,
            "resources": dict(self.resources),
            "metadata_keys": sorted(self.metadata),
            "extra_metadata_keys": self.extra_metadata_keys,
            "extra_frontmatter_keys": list(self.extra_keys),
            "problems": list(self.problems),
            "unreadable": self.unreadable,
        }


def parse_skill_md(text: str) -> tuple[dict[str, Any] | None, str, str | None]:
    """(frontmatter, body, error) the way `_parse_skill_md_content` splits it.

    The file must start with `---`; the text is split on the first two `---`
    and the body is stripped, exactly as ADK does it.
    """
    if not text.startswith("---"):
        return None, text.strip(), "SKILL.md must start with YAML frontmatter (---)"
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None, "", "SKILL.md frontmatter is not closed with ---"
    try:
        parsed = loads(parts[1])
    except YamlError as exc:
        return None, parts[2].strip(), f"invalid YAML in the frontmatter ({exc})"
    if not isinstance(parsed, dict):
        return None, parts[2].strip(), "SKILL.md frontmatter must be a YAML mapping"
    return parsed, parts[2].strip(), None


def frontmatter_problems(
    frontmatter: dict[str, Any], *, directory_name: str | None = None,
) -> list[str]:
    """What `Frontmatter.model_validate` (and the directory check) would reject."""
    problems: list[str] = []
    name = frontmatter.get("name")
    if name is None:
        problems.append("`name` is missing (required)")
    elif not isinstance(name, str):
        problems.append(f"`name` must be a string, not {type(name).__name__}")
    else:
        normalized = unicodedata.normalize("NFKC", name)
        if len(normalized) > MAX_NAME_CHARS:
            problems.append(f"`name` is {len(normalized)} characters; at most "
                            f"{MAX_NAME_CHARS} are allowed")
        if not KEBAB_NAME.match(normalized):
            problems.append(f"`name` {name!r} is not lowercase kebab-case (a-z, 0-9 and "
                            "single hyphens, no leading or trailing hyphen)")
        elif directory_name is not None and normalized != directory_name:
            problems.append(f"`name` {name!r} does not match the directory name "
                            f"{directory_name!r}")
    description = frontmatter.get("description")
    if description is None:
        problems.append("`description` is missing (required)")
    elif not isinstance(description, str):
        problems.append(f"`description` must be a string, not {type(description).__name__}")
    elif not description:
        problems.append("`description` is empty")
    elif len(description) > MAX_DESCRIPTION_CHARS:
        problems.append(f"`description` is {len(description)} characters; at most "
                        f"{MAX_DESCRIPTION_CHARS} are allowed")
    compatibility = frontmatter.get("compatibility")
    if isinstance(compatibility, str) and len(compatibility) > MAX_COMPATIBILITY_CHARS:
        problems.append(f"`compatibility` is {len(compatibility)} characters; at most "
                        f"{MAX_COMPATIBILITY_CHARS} are allowed")
    metadata = frontmatter.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        problems.append("`metadata` must be a mapping")
    elif isinstance(metadata, dict):
        if "adk_additional_tools" in metadata and not isinstance(metadata["adk_additional_tools"], list):
            problems.append("`metadata.adk_additional_tools` must be a list of tool names")
        if "adk_inject_state" in metadata and not isinstance(metadata["adk_inject_state"], bool):
            problems.append("`metadata.adk_inject_state` must be true or false")
    return problems


def count_resources(skill_dir: Path) -> dict[str, int]:
    """Files under references/, assets/ and scripts/, as `_load_dir` reads them."""
    counts: dict[str, int] = {}
    for kind in RESOURCE_DIRS:
        directory = skill_dir / kind
        counts[kind] = sum(
            1 for path in directory.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        ) if directory.is_dir() else 0
    return counts


def load_skill_dir(path: Path | str, *, file: str | None = None, lineno: int = 0) -> SkillInfo:
    """What `load_skill_from_dir(path)` would produce, or why it would raise."""
    skill_dir = Path(path)
    info = SkillInfo(name=None, source="dir", path=str(skill_dir), file=file, lineno=lineno)
    if not skill_dir.is_dir():
        info.problems.append(f"skill directory {skill_dir} does not exist "
                             "(load_skill_from_dir raises FileNotFoundError)")
        return info
    skill_md = next((skill_dir / name for name in ("SKILL.md", "skill.md")
                     if (skill_dir / name).is_file()), None)
    if skill_md is None:
        info.problems.append(f"no SKILL.md in {skill_dir.name}/ "
                             "(load_skill_from_dir raises FileNotFoundError)")
        return info
    try:
        text = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        info.problems.append(f"SKILL.md is unreadable: {exc}")
        return info
    frontmatter, body, error = parse_skill_md(text)
    info.instructions = body
    info.resources = count_resources(skill_dir)
    if error or frontmatter is None:
        info.problems.append(error or "unreadable frontmatter")
        return info
    _fill_from_frontmatter(info, frontmatter)
    info.problems.extend(frontmatter_problems(frontmatter, directory_name=skill_dir.name))
    return info


def load_skills_dir(path: Path | str, *, file: str | None = None, lineno: int = 0) -> list[SkillInfo]:
    """What `load_skills_from_dir(path)` would produce: one skill per subdirectory
    holding a SKILL.md, sorted, every one validated."""
    base = Path(path)
    if not base.is_dir():
        missing = SkillInfo(name=None, source="dir", path=str(base), file=file, lineno=lineno)
        missing.problems.append(f"skills directory {base} does not exist "
                                "(load_skills_from_dir raises FileNotFoundError)")
        return [missing]
    found: list[SkillInfo] = []
    for sub in sorted(base.iterdir()):
        if sub.is_dir() and ((sub / "SKILL.md").is_file() or (sub / "skill.md").is_file()):
            found.append(load_skill_dir(sub, file=file, lineno=lineno))
    return found


def _fill_from_frontmatter(info: SkillInfo, frontmatter: dict[str, Any]) -> None:
    name = frontmatter.get("name")
    info.name = name if isinstance(name, str) else None
    description = frontmatter.get("description")
    info.description = description if isinstance(description, str) else None
    metadata = frontmatter.get("metadata")
    info.metadata = dict(metadata) if isinstance(metadata, dict) else {}
    info.extra_keys = sorted(k for k in frontmatter if k not in FRONTMATTER_KEYS)


def inline_skill(
    frontmatter: dict[str, Any], instructions: str | None, resources: dict[str, int],
    *, file: str | None = None, lineno: int = 0, unreadable: bool = False,
) -> SkillInfo:
    """A `models.Skill(frontmatter=models.Frontmatter(...), ...)` read from source."""
    info = SkillInfo(name=None, source="inline", file=file, lineno=lineno,
                     instructions=instructions, unreadable=unreadable)
    info.resources = {kind: resources.get(kind, 0) for kind in RESOURCE_DIRS}
    _fill_from_frontmatter(info, frontmatter)
    # A field the extractor could not read is unknown, not missing.
    known = {k: v for k, v in frontmatter.items() if v is not _UNREADABLE}
    problems = frontmatter_problems(known)
    info.problems = [
        p for p in problems
        if not any(f"`{key}` is missing" in p for key, v in frontmatter.items() if v is _UNREADABLE)
    ]
    for key, value in frontmatter.items():
        if value is _UNREADABLE:
            if key == "name":
                info.name = None
            info.unreadable = True
    return info


class _Unreadable:
    def __repr__(self) -> str:
        return "<unreadable>"


#: Stands in for a frontmatter value the extractor saw but could not evaluate.
_UNREADABLE = _Unreadable()
UNREADABLE = _UNREADABLE


def distinct_lines(text: str | None, *, min_chars: int = 12) -> list[str]:
    """The distinct non-blank lines of a text, stripped, long enough to mean
    something (headings and list markers alone are not evidence of copying)."""
    seen: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip().lstrip("#*-> ").strip()
        if len(stripped) >= min_chars and stripped not in seen:
            seen.append(stripped)
    return seen


def repeated_share(skill_text: str | None, instruction: str | None) -> tuple[float, int, int]:
    """(share, repeated, total): how much of a skill's body the instruction
    already carries verbatim, line by line."""
    lines = distinct_lines(skill_text)
    if not lines or not instruction:
        return 0.0, 0, len(lines)
    haystack = " ".join(instruction.split())
    repeated = sum(1 for line in lines if " ".join(line.split()) in haystack)
    return repeated / len(lines), repeated, len(lines)
