"""One pass over a project: what exists, what is missing, what to do next.

Every skill starts here. Guessing a project's layout, its backend or which
evalsets already exist produces confident wrong advice, so each skill runs a
discovery script first and reports what it found before proposing anything.

The output is a `GapReport`: `discovered` facts, `findings` (things that are
wrong), `gaps` (things that are absent) and `options` (what the user can choose
to do). The `focus` argument keeps a skill from paying for analysis it will not
show.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Sequence

from . import ADK_TARGET
from .env import (
    BACKEND_REQUIREMENT, BackendInfo, RunnerSpec, detect_backend, dotenv_flag, resolve_runner,
    version_at_least, version_is_supported,
)
from .eval_yaml import plugin_layer
from .eval_config import LoadedConfig, load_config, validate_config
from .evalset import LoadedEvalSet, load_evalset, stats, validate_evalset
from .layout import LayoutInfo, detect_layout
from .metrics import DEFAULT_CRITERIA, cost_tier
from .report import GapReport, Option
from .results import find_result_files, latest_per_evalset, parse_result
from .tools_ast import (
    LLM_AGENT_CALLS, PLUGIN_EXPORTS, ModuleFacts, cache_suite_findings,
    compaction_suite_findings, extract_module, skill_lifecycle_findings, tool_resolver,
)

ALL_FOCUS = ("layout", "env", "agent", "evalsets", "configs", "results", "coverage", "ci")


def _rel(path: str | None, base: Path | str) -> str | None:
    """Show a path relative to what the user asked about, so reports stay readable."""
    if not path:
        return path
    try:
        return str(Path(path).resolve().relative_to(Path(base).resolve())) or "."
    except (ValueError, OSError):
        return Path(path).name


class Discovery:
    """Lazily computed view of one ADK project."""

    def __init__(
        self,
        target: Path | str,
        *,
        focus: Sequence[str] = ALL_FOCUS,
        probe_runner: bool = True,
        skill: str = "adk",
    ) -> None:
        self.target = Path(target)
        self.focus = set(focus or ALL_FOCUS)
        self.skill = skill
        self._probe = probe_runner

        self.layout: LayoutInfo = detect_layout(self.target)
        self.backend: BackendInfo | None = None
        self.runner: RunnerSpec | None = None
        self.facts: ModuleFacts | None = None
        self.evalsets: dict[str, LoadedEvalSet] = {}
        self.configs: dict[str, LoadedConfig] = {}
        self.result_files: list[str] = []
        self.latest_results: dict[str, str] = {}
        #: The Agent-Plugins skill layer (plugin.json + skills/*/EVAL.yaml),
        #: when the target is one or holds one.
        self.plugin_layer: dict[str, Any] | None = None

        if "env" in self.focus:
            self.backend = detect_backend(self.layout.agent_dir or self.target)
            self.runner = resolve_runner(
                self.layout.agent_dir or self.target,
                self.layout.project_root,
                probe=self._probe,
            )
        if "agent" in self.focus and self.layout.agent_dir:
            self.facts = extract_module(self.layout.agent_dir)
        if "layout" in self.focus and self.target.is_dir() and (
            not self.layout.is_agent or (self.target / "plugins").is_dir()
            or (self.target / "plugin.json").is_file()
        ):
            self.plugin_layer = plugin_layer(self.target)
        if "evalsets" in self.focus:
            for path in self.layout.evalsets:
                self.evalsets[path] = load_evalset(path)
        if "configs" in self.focus:
            for path in self.layout.configs:
                self.configs[path] = load_config(path)
        if "results" in self.focus:
            for directory in self.layout.eval_history_dirs or ([self.layout.agent_dir] if self.layout.agent_dir else []):
                if directory:
                    self.result_files.extend(find_result_files(directory))
            self.result_files = sorted(set(self.result_files))
            self.latest_results = latest_per_evalset(self.result_files)

    # -- derived facts ---------------------------------------------------
    @property
    def tool_names(self) -> set[str]:
        return self.facts.bound_tool_names if self.facts else set()

    @property
    def context_cache_config(self) -> dict[str, Any] | None:
        """The App's `ContextCacheConfig` kwargs: None when caching is off, `{}`
        when it is on but not readable from the source."""
        if not self.facts:
            return None
        app = next((a for a in self.facts.apps if a.context_cache_set), None)
        return None if app is None else dict(app.context_cache_config or {})

    @property
    def adk_version(self) -> str | None:
        return self.runner.adk_version if self.runner else None

    @property
    def events_compaction_config(self) -> dict[str, Any] | None:
        """The App's `EventsCompactionConfig` kwargs: None when unset, `{}` when
        set but not readable from the source."""
        if not self.facts:
            return None
        app = next((a for a in self.facts.apps if a.events_compaction_set), None)
        return None if app is None else dict(app.events_compaction_config or {})

    @property
    def resolver(self):
        """The one judgement of "is this tool name real", or None with no agent."""
        return tool_resolver(self.facts) if self.facts else None

    @property
    def primary_config(self) -> LoadedConfig | None:
        """The config a run would most likely use."""
        for name in ("test_config.json", "eval_config.json"):
            for path, config in self.configs.items():
                if Path(path).name == name and config.ok:
                    return config
        for config in self.configs.values():
            if config.ok:
                return config
        return None

    @property
    def active_criteria(self) -> dict[str, Any]:
        config = self.primary_config
        if config and config.doc:
            criteria = config.doc.get("criteria")
            if isinstance(criteria, dict):
                return criteria
        return dict(DEFAULT_CRITERIA)

    @property
    def uses_default_criteria(self) -> bool:
        return self.primary_config is None

    def has_reference_metrics(self) -> bool:
        config = self.primary_config
        if config:
            return config.has_reference_metrics()
        return True          # the fallback criteria are reference-based

    def cost(self) -> str:
        return cost_tier(self.active_criteria)

    def case_count(self) -> int:
        # Legacy files are migrated on read, so their cases count too. Scoring a
        # project with two legacy cases as having none is how a real suite gets
        # graded L0 and told to write its first case.
        return sum(
            stats(loaded.doc)["cases"]
            for loaded in self.evalsets.values()
            if isinstance(loaded.doc, dict)
        )

    def evalset_stats(self) -> dict[str, dict[str, Any]]:
        return {
            path: stats(loaded.doc)
            for path, loaded in self.evalsets.items()
            if isinstance(loaded.doc, dict)
        }

    def coverage(self) -> dict[str, Any] | None:
        """Suite shape, branch coverage and tool coverage, when both sides exist."""
        if "coverage" not in self.focus or not self.facts:
            return None
        from .coverage import (
            extract_sop_rules, matrix, n_plus_one_gaps, shape, skill_coverage, summarize_cases,
            tool_coverage,
        )

        agent = self.facts.root_agent()
        dynamic = bool(agent and agent.instruction_is_dynamic)
        rules = [] if dynamic else extract_sop_rules(
            agent.spec_text if agent else None, self.tool_names
        )
        docs = {
            path: loaded.doc
            for path, loaded in self.evalsets.items()
            if isinstance(loaded.doc, dict)
        }
        cases = summarize_cases(docs)
        return {
            "rules": len(rules),
            "branches": sum(1 for rule in rules if rule.is_branch),
            "instruction_dynamic": dynamic,
            "matrix": matrix(rules, cases).to_dict(),
            "shape": shape(cases),
            "n_plus_one": n_plus_one_gaps(rules, cases),
            "tools": tool_coverage(self.tool_names, cases, resolver=self.resolver),
            "skills": skill_coverage(self.facts.registered_skill_names, docs),
        }

    # -- report ----------------------------------------------------------
    def discovered(self) -> dict[str, Any]:
        data: dict[str, Any] = {}

        if "layout" in self.focus:
            root = self.layout.project_root or self.target
            data["layout"] = {
                "style": self.layout.style,
                "agent_dir": _rel(self.layout.agent_dir, root),
                "app_name": self.layout.app_name,
                "declared_app_name": self.layout.declared_app_name,
                "eval_app_name": self.layout.expected_app_name(),
                "entry_point": _rel(self.layout.agent_module_file, root),
                "export": self.layout.exports.preferred_export if self.layout.exports else None,
            }
            if self.layout.is_agent:
                # Without an agent there is no `adk eval` invocation to advise
                # about, and answering "yes, pass --config_file_path" to a
                # directory with no agent in it is noise.
                data["layout"]["needs_explicit_config"] = \
                    self.layout.eval_command_needs_explicit_config()
            if self.layout.foreign_configs:
                data["layout"]["foreign_configs"] = [
                    Path(p).name for p in self.layout.foreign_configs
                ]
            data["layout"]["project_root"] = str(root)
            if self.layout.sibling_agents:
                data["layout"]["agents_found"] = [
                    _rel(p, root) for p in self.layout.sibling_agents
                ]
            if self.plugin_layer:
                layer = self.plugin_layer
                data["plugin_layer"] = {
                    "plugins": [
                        {"name": p["name"], "version": p["version"], "skills": p["skills"],
                         "path": _rel(p["path"], self.target)}
                        for p in layer["plugins"]
                    ],
                    "eval_yamls": [
                        {"skill": e["skill_dir"], "path": _rel(e["path"], self.target),
                         "cases": e["cases"], "rubric_lines": e["rubric_lines"],
                         "assertions": e["assertions"], "assertion_types": e["assertion_types"],
                         "threshold": e["threshold"], "readable": e["readable"]}
                        for e in layer["eval_yamls"]
                    ],
                    "cases": layer["cases"],
                    "rubric_lines": layer["rubric_lines"],
                    "assertions": layer["assertions"],
                    "threshold_declared": layer["threshold_declared"],
                    "note": layer["note"],
                }

        if "env" in self.focus and self.backend and self.runner:
            data["environment"] = {
                "backend": self.backend.backend,
                "project": self.backend.project,
                "location": self.backend.location,
                "vertex_ready": self.backend.vertex_ready,
                "judge_ready": self.backend.judge_ready,
                "model_name_env": self.backend.model_name,
                "env_files": [_rel(p, self.layout.project_root or self.target) for p in self.backend.env_files],
                "env_example_files": [
                    _rel(p, self.layout.project_root or self.target)
                    for p in self.backend.example_files
                ],
                "runner": self.runner.kind,
                "runner_source": self.runner.source,
                "adk_version": self.runner.adk_version,
            }

        if "agent" in self.focus and self.facts:
            agent = self.facts.root_agent()
            data["agent"] = {
                "name": agent.name if agent else None,
                "type": agent.call if agent else None,
                "model": agent.model if agent else None,
                "model_from_env": agent.model_from_env if agent else None,
                "instruction_chars": agent.instruction_len if agent else 0,
                "tools": sorted(self.tool_names),
                "opaque_tools": sorted(self.facts.opaque_tools),
                "tools_unresolved": self.facts.tools_unresolved,
                # The agent's construction can be unreadable (`Agent(**kwargs)`,
                # or an export that is an object's attribute), in which case the
                # fields above are unknown rather than empty.
                "agent_unresolved": self.facts.agent_unresolved,
                "agent_unresolved_reason": self.facts.agent_unresolved_reason,
                "instruction_dynamic": agent.instruction_is_dynamic if agent else False,
                "static_instruction_chars": agent.static_instruction_len if agent else 0,
                "static_instruction_dynamic": bool(agent and agent.static_instruction_dynamic),
                "cache_prefix_tokens_estimate":
                    agent.cache_prefix_tokens_estimate if agent else 0,
                "context_cache_config": self.context_cache_config,
                "sub_agents": agent.sub_agents if agent else [],
                "app_exported": self.facts.app is not None,
                "plugins": self.facts.app.plugins if self.facts.app else [],
                "pydantic_models": sorted(self.facts.models),
                "events_compaction_config": self.events_compaction_config,
                "include_contents": agent.include_contents if agent else None,
                "skills": [
                    {key: value for key, value in skill.to_dict().items()
                     if key in ("name", "source", "description_chars", "instruction_lines",
                                "instruction_chars", "resources", "extra_metadata_keys",
                                "problems")}
                    for skill in self.facts.skills
                ],
                "skill_toolsets": [
                    {"var": t.label, "discovery_mode": t.discovery_mode or "LAZY",
                     "lifecycle_config": t.lifecycle_config, "registry": t.registry,
                     "skills_unresolved": t.skills_unresolved,
                     "unresolved_reason": t.unresolved_reason, "tools": t.tool_names}
                    for t in self.facts.skill_toolsets
                ],
            }

        if "evalsets" in self.focus:
            per_set = self.evalset_stats()
            totals: dict[str, int] = {}
            for entry in per_set.values():
                for category, count in entry["by_category"].items():
                    totals[category] = totals.get(category, 0) + count
            data["evalsets"] = {
                "files": [Path(p).name for p in self.evalsets],
                "count": len(self.evalsets),
                "cases": self.case_count(),
                "by_category": totals,
                "legacy_format": [
                    Path(p).name for p, loaded in self.evalsets.items() if loaded.is_legacy
                ],
                "unreadable": [
                    Path(p).name for p, loaded in self.evalsets.items() if loaded.fmt == "invalid"
                ],
            }

        if "configs" in self.focus:
            data["criteria"] = {
                "config": Path(self.primary_config.path).name if self.primary_config else None,
                "using_defaults": self.uses_default_criteria,
                "metrics": list(self.active_criteria),
                "cost_tier": self.cost(),
                "other_config_files": [
                    Path(p).name for p, c in self.configs.items()
                    if not (self.primary_config and p == self.primary_config.path)
                ],
            }

        if "results" in self.focus:
            latest_summary: list[dict[str, Any]] = []
            for eval_set_id, path in self.latest_results.items():
                run = parse_result(path)
                latest_summary.append({
                    "eval_set": eval_set_id,
                    "passed": run.passed,
                    "failed": run.failed,
                    "not_evaluated": run.not_evaluated,
                })
            data["results"] = {
                "runs_on_disk": len(self.result_files),
                "latest": latest_summary,
            }

        if "ci" in self.focus:
            data["ci"] = {
                "workflows": [Path(p).name for p in _github_workflows(self.layout)],
                "pytest_eval_files": [Path(p).name for p in self.layout.pytest_eval_files],
                "conformance_dirs": [Path(p).name for p in self.layout.conformance_dirs],
            }

        if "coverage" in self.focus:
            coverage = self.coverage()
            if coverage:
                data["coverage"] = {
                    "sop_rules": coverage["rules"],
                    "branches": coverage["branches"],
                    "minimum_cases": coverage["n_plus_one"]["minimum_cases"],
                    "shortfall": coverage["n_plus_one"]["shortfall"],
                    "shape": coverage["shape"]["ratios"],
                    "uncovered_branches": len(coverage["matrix"]["uncovered_branch_indexes"]),
                    "tools_never_exercised": coverage["tools"]["never_exercised"],
                    "skills_never_activated":
                        coverage["skills"]["never_activated"] if coverage["skills"] else None,
                }
        return data

    def report(self) -> GapReport:
        report = GapReport(subject=str(self.target), skill=self.skill)
        report.discovered = self.discovered()
        self._add_environment_findings(report)
        self._add_agent_findings(report)
        self._add_evalset_findings(report)
        self._add_config_findings(report)
        self._add_gaps(report)
        return report

    # -- findings --------------------------------------------------------
    def _add_environment_findings(self, report: GapReport) -> None:
        if "layout" in self.focus:
            if self.layout.style == "collection":
                report.add("ENV010", "warn", "layout",
                           f"{len(self.layout.sibling_agents)} agents found under this path",
                           path=str(self.target),
                           fix="point the skill at one agent directory")
            elif not self.layout.is_agent and self.plugin_layer:
                report.add("ENV014", "info", "layout",
                           "this is an Agent-Plugins skill layer (plugin.json, "
                           "skills/*/SKILL.md and EVAL.yaml), not an ADK agent",
                           path=str(self.target),
                           fix="check its EVAL.yaml files with /adk:eval-review "
                               "validate_eval_yaml.py; point the agent skills at the agent "
                               "a plugin skill helps you build",
                           source="adk-recipes: plugins/ (Agent Plugins v1.0.0)")
            elif not self.layout.is_agent:
                report.add("ENV011", "error", "layout",
                           "no ADK agent here: expected agent.py, a loadable __init__.py, "
                           "a <name>.py module or root_agent.yaml",
                           path=str(self.target),
                           fix="run this from the agent directory or its project root",
                           source="adk-python: cli/utils/agent_loader.py")
            for note in self.layout.notes:
                if "deprecated" in note:
                    report.add("ENV012", "warn", "layout", note, path=self.layout.agent_dir)
            mismatch = self.layout.app_name_mismatch()
            if mismatch:
                declared, directory = mismatch
                report.add(
                    "ENV013", "info", "layout",
                    f'App(name="{declared}") does not match the agent directory "{directory}"',
                    path=self.layout.agent_module_file,
                    fix=f'not a fault: a run resolves to "{declared}" (app_name or app.name), '
                        f'and that is what eval cases must declare. ADK still appends an '
                        f'alignment hint to errors because the loader found the agent in '
                        f'"{directory}"; renaming one of the two silences it',
                    source="adk-python: runners.py (_enforce_app_name_alignment)",
                )

        if "env" not in self.focus or not self.runner or not self.backend:
            return

        if not self.runner.available:
            report.add("ENV001", "warn", "environment",
                       "no interpreter with google.adk, so nothing can actually be run",
                       fix=self.runner.note or "install google-adk[eval]")
        elif self.runner.adk_version and not version_is_supported(self.runner.adk_version):
            report.add("ENV002", "error", "environment",
                       f"ADK {self.runner.adk_version} found, but these skills target "
                       f"{ADK_TARGET} (2.x)",
                       fix="upgrade the project, or expect guidance here to differ from its API",
                       source="skills/adk/references/adk-1x-to-2x-migration.md")

        if self.backend.backend == "unknown":
            report.add("ENV003", "warn", "environment",
                       "no GenAI backend configured for this agent",
                       fix="ADK reads the first `.env` from the agent directory up to the "
                           "filesystem root; it " + BACKEND_REQUIREMENT,
                       source="skills/adk/references/environment.md")
        elif self.backend.missing:
            report.add("ENV004", "error", "environment",
                       f"backend is {self.backend.backend} but missing: {', '.join(self.backend.missing)}",
                       fix="add the missing variables to the agent's .env")

        if self.cost() == "vertex" and not self.backend.vertex_ready:
            report.add("ENV005", "error", "environment",
                       "the criteria include a metric served by the Vertex AI Gen AI "
                       "Evaluation Service, and no credential can reach it",
                       fix="it " + BACKEND_REQUIREMENT
                           + "; the facade takes the API key first. Otherwise move those "
                             "metrics to a nightly config")

    def _add_agent_findings(self, report: GapReport) -> None:
        if self.facts:
            report.extend(self.facts.findings)
            for error in self.facts.syntax_errors:
                report.add("AGT000", "error", "agent", f"could not parse: {error}")
            self._add_plugin_version_findings(report)
            if "evalsets" in self.focus:
                docs = [loaded.doc for loaded in self.evalsets.values()]
                report.extend(cache_suite_findings(self.facts, docs))
                report.extend(compaction_suite_findings(self.facts, docs))
            report.extend(skill_lifecycle_findings(
                self.facts, env_enabled=dotenv_flag(self.layout.agent_dir,
                                                    "ADK_ENABLE_SKILL_LIFECYCLE"),
                adk_version=self.adk_version))

    def _add_plugin_version_findings(self, report: GapReport) -> None:
        """AGT066: a plugin the project imports is newer than the runner."""
        version = self.runner.adk_version if self.runner else None
        if not self.facts or not version:
            return
        for name, since in sorted(PLUGIN_EXPORTS.items()):
            if since == "2.0" or version_at_least(version, since):
                continue
            if not any(item.endswith(f".{name}") for item in self.facts.imports):
                continue
            report.add(
                "AGT066", "error", "agent",
                f"`{name}` needs ADK {since} or later, and the runner is ADK {version}, "
                "so the import fails before the agent loads",
                path=self.layout.agent_module_file,
                fix=f"upgrade the project to google-adk>={since}, or drop the plugin; it is "
                    "lazily exported from `google.adk.plugins`",
                source="adk-python: plugins/__init__.py",
            )

    def _add_evalset_findings(self, report: GapReport) -> None:
        if "evalsets" not in self.focus:
            return
        expected = self.layout.expected_app_name()
        for loaded in self.evalsets.values():
            validate_evalset(
                loaded,
                report=report,
                expected_app_name=expected,
                tool_resolver=self.resolver,
                reference_metrics=self.has_reference_metrics(),
                trajectory_match_type=(
                    self.primary_config.trajectory_match_type() if self.primary_config else None
                ),
                adk_version=self.adk_version,
                registered_skills=self.facts.registered_skill_names if self.facts else None,
            )

    def _add_config_findings(self, report: GapReport) -> None:
        if "configs" not in self.focus:
            return
        for loaded in self.configs.values():
            validate_config(
                loaded,
                report=report,
                backend=self.backend,
                agent_dir=self.layout.agent_dir,
                known_tools=self.tool_names or None,
                adk_version=self.runner.adk_version if self.runner else None,
            )

    def _add_gaps(self, report: GapReport) -> None:
        if "evalsets" in self.focus and not self.evalsets:
            report.add_gap(
                "GAP-EVALSET", "No evalset",
                "nothing describes what this agent is supposed to do, so no change can be "
                "checked against anything",
                "capture a golden trace or write a first happy-path case with /adk:eval-create",
            )
        if "configs" in self.focus and self.uses_default_criteria and self.evalsets:
            report.add_gap(
                "GAP-CONFIG", "No eval config",
                "without one, ADK falls back to tool_trajectory_avg_score 1.0 and "
                "response_match_score 0.8, so wording differences fail the suite",
                "write a config with /adk:eval-criteria; for a trajectory-first suite set "
                "response_match_score to 0.0",
            )
        if "results" in self.focus and self.evalsets and not self.result_files:
            report.add_gap(
                "GAP-RUN", "Never run",
                "there are eval cases but no results on disk",
                "run them with /adk:eval-run",
            )
        coverage = self.coverage() if "coverage" in self.focus else None
        if coverage:
            shortfall = coverage["n_plus_one"]["shortfall"]
            if shortfall:
                report.add_gap(
                    "GAP-BRANCH", f"{shortfall} case(s) short of branch coverage",
                    f"the instruction has {coverage['branches']} conditional or prohibition "
                    f"branches, which needs at least {coverage['n_plus_one']['minimum_cases']} "
                    f"cases; there are {coverage['n_plus_one']['cases']}",
                    "add one case per uncovered branch with /adk:eval-create",
                )
            for note in coverage["shape"].get("notes", []):
                report.add_gap("GAP-SHAPE", "Suite shape", note,
                               "rebalance with /adk:eval-create (roughly 70% narrow, "
                               "20% multi-turn, 10% adversarial)")
            skills = coverage["skills"]
            if skills and skills["never_activated"] and self.case_count() > 0:
                report.add(
                    "EVS073", "warn", "coverage",
                    f"{len(skills['never_activated'])} registered skill(s) no case activates: "
                    f"{', '.join(skills['never_activated'])}",
                    fix="add an activation case per skill expecting `load_skill` with "
                        "{\"skill_name\": \"<name>\"} (gen_cases.py: {\"skill\": \"<name>\"} "
                        "in a turn)",
                    source="adk-python: tools/skill_toolset.py (LoadSkillTool)",
                )
            never = coverage["tools"]["never_exercised"]
            if never:
                report.add_gap(
                    "GAP-TOOLS", f"{len(never)} tool(s) never exercised",
                    f"no eval case expects a call to: {', '.join(never)}",
                    "add a case per unexercised tool, or remove the tool",
                )
        if "ci" in self.focus and self.evalsets and not _github_workflows(self.layout):
            report.add_gap(
                "GAP-CI", "No CI gate",
                "the suite only runs when someone remembers to run it",
                "generate a cost-tiered workflow with /adk:eval-ci",
            )


def _github_workflows(layout: LayoutInfo) -> list[str]:
    roots = {layout.project_root, layout.agents_dir, str(Path(layout.root))}
    found: list[str] = []
    for root in roots:
        if not root:
            continue
        directory = Path(root) / ".github" / "workflows"
        if directory.is_dir():
            found.extend(str(p) for p in sorted(directory.glob("*.y*ml")))
        parent = Path(root).parent / ".github" / "workflows"
        if parent.is_dir():
            found.extend(str(p) for p in sorted(parent.glob("*.y*ml")))
    return sorted(set(found))


def discover(
    target: Path | str,
    *,
    focus: Iterable[str] = ALL_FOCUS,
    probe_runner: bool = True,
    skill: str = "adk",
) -> tuple[Discovery, GapReport]:
    discovery = Discovery(target, focus=tuple(focus), probe_runner=probe_runner, skill=skill)
    return discovery, discovery.report()


def suggest_next_skills(discovery: Discovery) -> list[Option]:
    """Route to the skill that addresses the most pressing gap."""
    options: list[Option] = []
    key = 1

    def add(label: str, detail: str, *, cost: str = "free", recommended: bool = False,
            runs: list[str] | None = None) -> None:
        nonlocal key
        options.append(Option(key=str(key), label=label, detail=detail, cost=cost,
                              recommended=recommended, runs=runs or []))
        key += 1

    if not discovery.layout.is_agent:
        if discovery.plugin_layer:
            add("/adk:eval-review", "check the plugin's EVAL.yaml files "
                "(validate_eval_yaml.py; static, runs nothing)", recommended=True)
            add("/adk:agent-create", "scaffold the ADK agent a plugin skill describes")
            return options
        add("/adk:agent-create", "scaffold an eval-ready agent here", recommended=True)
        return options

    if not discovery.evalsets:
        add("/adk:eval-create", "write the first eval cases for this agent", recommended=True)
        add("/adk:agent-review", "check the agent for ADK 2.x problems first")
        add("/adk:eval-criteria", "decide which metrics to gate on")
        return options

    latest_failing = any(
        entry["failed"] or entry["not_evaluated"]
        for entry in (discovery.discovered().get("results", {}) or {}).get("latest", [])
    )
    coverage = discovery.coverage()
    short_on_cases = bool(coverage and coverage["n_plus_one"]["shortfall"])

    # Exactly one recommendation, picked by what blocks progress first:
    # a failing run beats a coverage gap, which beats never having run at all.
    if latest_failing:
        recommended = "analyze"
    elif short_on_cases:
        recommended = "create"
    elif not discovery.result_files:
        recommended = "run"
    elif discovery.uses_default_criteria:
        recommended = "criteria"
    else:
        recommended = "review"

    if latest_failing:
        add("/adk:eval-analyze", "explain the last failing run",
            recommended=recommended == "analyze")
        add("/adk:agent-debug", "triage the failure to a layer and a fix")
    if short_on_cases:
        add("/adk:eval-create", "close the branch-coverage gap",
            recommended=recommended == "create")
    if discovery.uses_default_criteria:
        add("/adk:eval-criteria", "write an explicit eval config",
            recommended=recommended == "criteria")
    add("/adk:eval-run",
        "run the suite" if not discovery.result_files else "run again, or repeat runs for pass@k",
        recommended=recommended == "run")
    add("/adk:eval-review", "validate the evalsets and config in place",
        recommended=recommended == "review")
    if not _github_workflows(discovery.layout):
        add("/adk:eval-ci", "add a cost-tiered CI gate")
    return options
