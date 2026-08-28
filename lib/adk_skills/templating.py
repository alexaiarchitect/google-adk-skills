"""Placeholder rendering and a write plan that never clobbers by surprise.

Generators in this plugin always produce a `WritePlan` first. A skill shows it
to the user, gets a yes, and only then applies it. Evalsets get extra
protection: a golden trace is a specification, so an existing case is never
silently rewritten.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

PLACEHOLDER = re.compile(r"\{\{\s*([A-Z0-9_]+)\s*\}\}")


class TemplateError(RuntimeError):
    pass


def render(text: str, mapping: dict[str, str], *, strict: bool = True) -> str:
    """Replace ``{{NAME}}`` placeholders. Unknown names are an error."""
    missing: set[str] = set()

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in mapping:
            return str(mapping[name])
        missing.add(name)
        return match.group(0)

    result = PLACEHOLDER.sub(_sub, text)
    if missing and strict:
        raise TemplateError(f"unfilled placeholders: {', '.join(sorted(missing))}")
    return result


def render_file(src: Path | str, mapping: dict[str, str], *, strict: bool = True) -> str:
    return render(Path(src).read_text(encoding="utf-8"), mapping, strict=strict)


def placeholders(text: str) -> set[str]:
    return {m.group(1) for m in PLACEHOLDER.finditer(text)}


@dataclass
class WriteItem:
    path: str
    content: str
    action: str = "create"        # create | overwrite | merge | skip
    reason: str | None = None

    @property
    def bytes_len(self) -> int:
        return len(self.content.encode("utf-8"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "action": self.action,
            "bytes": self.bytes_len,
            **({"reason": self.reason} if self.reason else {}),
        }


@dataclass
class WritePlan:
    items: list[WriteItem] = field(default_factory=list)
    blocked: list[dict[str, str]] = field(default_factory=list)

    def add(self, path: Path | str, content: str, *, force: bool = False, reason: str | None = None) -> WriteItem:
        target = Path(path)
        if target.exists():
            if not force:
                item = WriteItem(str(target), content, action="skip",
                                 reason=reason or "file exists; pass --force to replace")
                self.items.append(item)
                return item
            item = WriteItem(str(target), content, action="overwrite", reason=reason)
        else:
            item = WriteItem(str(target), content, action="create", reason=reason)
        self.items.append(item)
        return item

    def block(self, path: Path | str, reason: str) -> None:
        self.blocked.append({"path": str(path), "reason": reason})

    @property
    def writable(self) -> list[WriteItem]:
        return [i for i in self.items if i.action in ("create", "overwrite", "merge")]

    def apply(self) -> list[str]:
        written: list[str] = []
        for item in self.writable:
            target = Path(item.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item.content, encoding="utf-8")
            written.append(item.path)
        return written

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [i.to_dict() for i in self.items],
            "blocked": self.blocked,
            "will_write": [i.path for i in self.writable],
        }

    def to_md(self) -> str:
        lines = ["## Write plan", ""]
        if not self.items:
            lines.append("- (nothing to write)")
        for item in self.items:
            verb = {"create": "create", "overwrite": "replace", "merge": "update", "skip": "skip"}[item.action]
            lines.append(f"- **{verb}** `{item.path}` ({item.bytes_len} bytes)")
            if item.reason:
                lines.append(f"  - {item.reason}")
        if self.blocked:
            lines.extend(["", "## Refused", ""])
            for entry in self.blocked:
                lines.append(f"- `{entry['path']}` — {entry['reason']}")
        return "\n".join(lines)


def dumps_json(data: Any) -> str:
    """Canonical JSON for generated evalsets and configs."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def iter_template_files(root: Path | str) -> Iterable[Path]:
    root = Path(root)
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            yield path
