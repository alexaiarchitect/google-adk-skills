"""Findings, gap reports and the plugin-wide exit-code contract.

Every script that inspects something emits a `GapReport`: a machine-readable
record of what was discovered, what is wrong (`findings`), what is missing
(`gaps`) and what the user could do next (`options`). Skills render the
markdown form to the user and branch on the JSON form.

Exit codes (identical in every script):

===  ==========================================================
  0  success, no errors
  1  errors present (validation failures, failing evals)
  2  usage or environment error (bad args, missing target, no runner)
  3  inconclusive (e.g. only NOT_EVALUATED results under `warn` policy)
===  ==========================================================
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Literal

SCHEMA = "adk-skills.gap-report/1"

Severity = Literal["error", "warn", "info"]

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2
EXIT_INCONCLUSIVE = 3

_SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}
_SEVERITY_ICON = {"error": "\U0001f534", "warn": "\U0001f7e0", "info": "\U0001f535"}
_SEVERITY_HEADING = {
    "error": "Errors",
    "warn": "Warnings",
    "info": "Notes",
}


@dataclass
class Finding:
    """One concrete problem, tied to a place and a fix.

    `id` is stable across releases so skills and tests can reference it
    (`AGT###` agent, `EVS###` evalset, `CFG###` eval config, `ENV###`
    environment, `RES###` results, `CI###` continuous integration).
    `source` cites the ADK file or doc page the rule derives from.
    """

    id: str
    severity: Severity
    area: str
    message: str
    path: str | None = None
    locator: str | None = None
    fix: str | None = None
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    def one_line(self, *, relative_to: str | None = None) -> str:
        path = self.path or ""
        if path and relative_to:
            # The report already names its subject, so don't repeat it on every
            # line. Resolve both sides: the subject is often a relative path
            # while findings carry absolute ones.
            try:
                import os

                base = os.path.realpath(relative_to)
                full = os.path.realpath(path)
                if full == base:
                    path = ""
                elif full.startswith(base.rstrip(os.sep) + os.sep):
                    path = full[len(base.rstrip(os.sep)) + 1:]
            except (OSError, ValueError):
                pass
        where = f"{path} {self.locator}".strip() if self.locator else path
        head = f"{_SEVERITY_ICON[self.severity]} `{self.id}`"
        if where:
            head = f"{head} {where}"
        return f"{head} — {self.message}"


@dataclass
class Gap:
    """Something absent rather than wrong: a missing case category, config..."""

    id: str
    what: str
    why: str
    recommendation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Option:
    """A next action the skill may offer the user.

    `cost` is the spend tier so the user can judge before confirming:
    `free` (deterministic, no model calls), `judge` (Gemini judge calls),
    `vertex` (Vertex AI Gen AI Evaluation Service).
    """

    key: str
    label: str
    detail: str = ""
    recommended: bool = False
    cost: Literal["free", "judge", "vertex"] = "free"
    writes: list[str] = field(default_factory=list)
    runs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GapReport:
    subject: str
    skill: str
    discovered: dict[str, Any] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    options: list[Option] = field(default_factory=list)

    # -- building -------------------------------------------------------
    def add(
        self,
        id: str,
        severity: Severity,
        area: str,
        message: str,
        *,
        path: str | None = None,
        locator: str | None = None,
        fix: str | None = None,
        source: str | None = None,
    ) -> Finding:
        finding = Finding(
            id=id,
            severity=severity,
            area=area,
            message=message,
            path=path,
            locator=locator,
            fix=fix,
            source=source,
        )
        self.findings.append(finding)
        return finding

    def extend(self, findings: Iterable[Finding]) -> None:
        self.findings.extend(findings)

    def add_gap(self, id: str, what: str, why: str, recommendation: str) -> Gap:
        gap = Gap(id=id, what=what, why=why, recommendation=recommendation)
        self.gaps.append(gap)
        return gap

    def add_option(self, option: Option) -> Option:
        self.options.append(option)
        return option

    # -- querying -------------------------------------------------------
    def count(self, severity: Severity) -> int:
        return sum(1 for f in self.findings if f.severity == severity)

    @property
    def summary(self) -> dict[str, int]:
        return {
            "errors": self.count("error"),
            "warnings": self.count("warn"),
            "infos": self.count("info"),
        }

    def has_errors(self) -> bool:
        return self.count("error") > 0

    def ids(self) -> list[str]:
        return [f.id for f in self.findings]

    # -- rendering ------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "subject": self.subject,
            "skill": self.skill,
            "discovered": self.discovered,
            "findings": [f.to_dict() for f in self.findings],
            "gaps": [g.to_dict() for g in self.gaps],
            "options": [o.to_dict() for o in self.options],
            "summary": self.summary,
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)

    def to_md(self) -> str:
        lines: list[str] = [f"# {self.skill}: {self.subject}", ""]

        if self.discovered:
            lines.append("## Found")
            lines.append("")
            lines.extend(_render_discovered(self.discovered))
            lines.append("")

        if self.gaps:
            lines.append("## Missing")
            lines.append("")
            for gap in self.gaps:
                lines.append(f"- **{gap.what}** — {gap.why}")
                lines.append(f"  - Recommendation: {gap.recommendation}")
            lines.append("")

        if self.findings:
            ordered = sorted(
                self.findings, key=lambda f: (_SEVERITY_ORDER[f.severity], f.id)
            )
            current: str | None = None
            for finding in ordered:
                if finding.severity != current:
                    if current is not None:
                        lines.append("")
                    current = finding.severity
                    lines.append(f"## {_SEVERITY_HEADING[current]}")
                    lines.append("")
                lines.append(f"- {finding.one_line(relative_to=self.subject)}")
                if finding.fix:
                    lines.append(f"  - Fix: {finding.fix}")
                if finding.source:
                    lines.append(f"  - Source: {finding.source}")
            lines.append("")

        if self.options:
            lines.append("## Options")
            lines.append("")
            for option in self.options:
                mark = " **(recommended)**" if option.recommended else ""
                lines.append(f"{option.key}. **{option.label}**{mark} — cost: {option.cost}")
                if option.detail:
                    lines.append(f"   - {option.detail}")
                if option.writes:
                    lines.append(f"   - Writes: {', '.join(option.writes)}")
                if option.runs:
                    lines.append(f"   - Runs: {', '.join(option.runs)}")
            lines.append("")

        s = self.summary
        lines.append(
            f"_Summary: {s['errors']} error(s), {s['warnings']} warning(s), "
            f"{s['infos']} note(s)._"
        )
        return "\n".join(lines)

    def render(self, fmt: str) -> str:
        return self.to_json() if fmt == "json" else self.to_md()


def _render_discovered(data: dict[str, Any], prefix: str = "") -> list[str]:
    lines: list[str] = []
    for key, value in data.items():
        label = f"{prefix}{key}"
        if isinstance(value, dict) and value:
            lines.append(f"- **{label}**")
            lines.extend(f"  {line}" for line in _render_discovered(value))
        elif isinstance(value, list):
            if not value:
                lines.append(f"- **{label}**: (none)")
            elif all(not isinstance(v, (dict, list)) for v in value):
                lines.append(f"- **{label}**: {', '.join(str(v) for v in value)}")
            else:
                lines.append(f"- **{label}**: {len(value)} item(s)")
                for item in value:
                    if isinstance(item, dict):
                        inline = ", ".join(
                            f"{k}={v}"
                            for k, v in item.items()
                            if not isinstance(v, (dict, list))
                        )
                        lines.append(f"  - {inline}")
                    else:
                        lines.append(f"  - {item}")
        else:
            shown = "(none)" if value in (None, "") else value
            lines.append(f"- **{label}**: {shown}")
    return lines


def exit_code_for(report: GapReport, *, inconclusive: bool = False) -> int:
    """Map a report to the plugin-wide exit-code contract."""
    if report.has_errors():
        return EXIT_FINDINGS
    if inconclusive:
        return EXIT_INCONCLUSIVE
    return EXIT_OK
