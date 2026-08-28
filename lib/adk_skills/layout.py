"""Detect how an ADK project is laid out, without importing it.

`adk web`, `adk run` and `adk eval` all resolve an agent through
`cli/utils/agent_loader.py`, which accepts four shapes under an *agents
directory*:

1. ``{agents_dir}/{name}/agent.py``      → module ``{name}.agent``
2. ``{agents_dir}/{name}.py``            → module ``{name}``
3. ``{agents_dir}/{name}/__init__.py``   → package ``{name}``
4. ``{agents_dir}/{name}/root_agent.yaml`` → YAML config agent

In each case the loader looks for ``app`` (an ``App`` instance) first, then
``root_agent``, then ``get_agent_async``. ``{name}`` — the directory or module
name — becomes the ``app_name`` the CLI passes to the runner, which is why it
has to agree with ``session_input.app_name`` in every eval case.

On top of those, two project conventions exist in the wild:

* **recipes** — a project directory whose agent package is ``app/`` and whose
  evaluation files live in ``tests/eval/`` (``evalsets/*.evalset.json`` plus
  ``eval_config.json``). ``adk eval`` is pointed at ``<project>/app``.
* **flat** — what ``adk create`` scaffolds: ``<agent>/__init__.py`` +
  ``<agent>/agent.py``, with ``*.test.json`` and ``test_config.json`` beside
  the agent.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

# Directories never worth walking into when hunting for eval files.
SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__", ".adk",
    "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist",
    "build", ".tox", "site-packages", ".idea", ".claude",
}

EVALSET_SUFFIXES = (".evalset.json", ".test.json")

#: Configs `adk eval` can actually read. `EvalConfig.model_validate_json` is
#: what reads them, so only JSON counts.
ADK_CONFIG_NAMES = ("eval_config.json", "test_config.json")
#: Eval-shaped files some project templates ship that ADK cannot read. They are
#: worth mentioning and must never be counted as the config a run would use.
FOREIGN_CONFIG_NAMES = ("eval_config.yaml", "eval_config.yml")
CONFIG_NAMES = ADK_CONFIG_NAMES + FOREIGN_CONFIG_NAMES

#: ``App.name`` and agent directory names must match this (``apps/app.py``).
APP_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")


@dataclass
class ExportInfo:
    """What a candidate agent module exports, read statically."""

    path: str
    has_app: bool = False
    has_root_agent: bool = False
    has_get_agent_async: bool = False
    declared_app_name: str | None = None
    root_agent_call: str | None = None
    imports_adk: bool = False

    @property
    def loadable(self) -> bool:
        return self.has_app or self.has_root_agent or self.has_get_agent_async

    @property
    def preferred_export(self) -> str | None:
        """Mirror the loader's precedence: app, then root_agent, then factory."""
        if self.has_app:
            return "app"
        if self.has_root_agent:
            return "root_agent"
        if self.has_get_agent_async:
            return "get_agent_async"
        return None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, False)} | {
            "path": self.path,
            "preferred_export": self.preferred_export,
        }


@dataclass
class LayoutInfo:
    """Everything a skill needs to know about where things are."""

    root: str
    style: str = "unknown"
    agent_dir: str | None = None
    agents_dir: str | None = None
    project_root: str | None = None
    app_name: str | None = None
    declared_app_name: str | None = None
    agent_module_file: str | None = None
    module_path: str | None = None
    exports: ExportInfo | None = None
    python_files: list[str] = field(default_factory=list)
    evalsets: list[str] = field(default_factory=list)
    legacy_candidates: list[str] = field(default_factory=list)
    configs: list[str] = field(default_factory=list)
    foreign_configs: list[str] = field(default_factory=list)
    eval_dirs: list[str] = field(default_factory=list)
    pytest_eval_files: list[str] = field(default_factory=list)
    conformance_dirs: list[str] = field(default_factory=list)
    eval_history_dirs: list[str] = field(default_factory=list)
    env_files: list[str] = field(default_factory=list)
    workflow_files: list[str] = field(default_factory=list)
    sibling_agents: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def is_agent(self) -> bool:
        return self.agent_dir is not None or self.style == "module_file"

    def expected_app_name(self) -> str | None:
        """The ``app_name`` eval cases should declare.

        ``Runner.app_name = app_name or app.name`` (``runners.py``), so when the
        module exports an ``App`` that declares a name, that name is what the
        run resolves to and what eval cases must match. Only when no App
        declares one does the directory name decide it, which is the case for
        the flat layout ``adk create`` scaffolds.
        """
        if self.exports and self.exports.has_app and self.declared_app_name:
            return self.declared_app_name
        return self.app_name

    def app_name_mismatch(self) -> tuple[str, str] | None:
        """``(App.name, directory)`` when an exported App disagrees with its
        directory. Not fatal, but it makes every scope and every error message
        ambiguous."""
        if self.declared_app_name and self.app_name and self.declared_app_name != self.app_name:
            return (self.declared_app_name, self.app_name)
        return None

    def eval_command_needs_explicit_config(self) -> bool:
        """Must ``--config_file_path`` be passed for the config to be used?

        ADK auto-discovers ``test_config.json`` only beside a single evalset
        *file*. Point it at several and the discovery does not happen, so the
        run silently falls back to the default criteria - which is the quiet
        version of "the gate you wrote is not the gate that ran".
        """
        if not self.configs or not self.evalsets:
            return False
        if len(self.evalsets) > 1:
            return True
        sibling = Path(self.evalsets[0]).parent / "test_config.json"
        return str(sibling) not in self.configs

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["exports"] = self.exports.to_dict() if self.exports else None
        data["expected_app_name"] = self.expected_app_name()
        return data


# ---------------------------------------------------------------------------
# static export scanning
# ---------------------------------------------------------------------------

def _literal(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def _call_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def scan_exports(path: Path) -> ExportInfo:
    """Read a Python file and report which ADK entry points it defines."""
    info = ExportInfo(path=str(path))
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return info

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            names.extend(alias.name for alias in node.names)
            if any(n.startswith("google.adk") or n == "google.adk" for n in names):
                info.imports_adk = True
            # A package `__init__.py` usually exposes the agent by re-exporting
            # it (`from .agent import app`). The loader imports the package and
            # finds the name, so a re-export is an export.
            for alias in node.names:
                exported = alias.asname or alias.name
                if exported == "app":
                    info.has_app = True
                elif exported == "root_agent":
                    info.has_root_agent = True
                elif exported == "get_agent_async":
                    info.has_get_agent_async = True

    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "get_agent_async":
                info.has_get_agent_async = True
            continue
        else:
            continue

        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id == "app":
                info.has_app = True
                if value is not None and _call_name(value) == "App":
                    info.declared_app_name = _app_name_kwarg(value)
            elif target.id == "root_agent":
                info.has_root_agent = True
                if value is not None:
                    info.root_agent_call = _call_name(value)

    if info.declared_app_name is None:
        # `app = _build_app()` with `return App(name=...)` inside the factory is
        # how most non-trivial projects build their App. The name is declared
        # all the same, and it is what the runner falls back to.
        for node in ast.walk(tree):
            if isinstance(node, ast.Return) and _call_name(node.value) == "App":
                declared = _app_name_kwarg(node.value)
                if declared:
                    info.declared_app_name = declared
                    info.has_app = info.has_app or _module_exports_app(tree)
                    break
    return info


def _app_name_kwarg(call: ast.AST) -> str | None:
    for kw in getattr(call, "keywords", []):
        if kw.arg == "name":
            value = _literal(kw.value)
            return value if isinstance(value, str) else None
    return None


def _module_exports_app(tree: ast.Module) -> bool:
    """Is there a module-level `app = ...` of any kind?"""
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        if any(isinstance(t, ast.Name) and t.id == "app" for t in targets):
            return True
    return False


# ---------------------------------------------------------------------------
# file discovery
# ---------------------------------------------------------------------------

def _walk(root: Path, *, max_depth: int = 6, enter: frozenset[str] = frozenset()):
    """Yield files under `root`, skipping vendor/cache directories.

    `enter` names directories that are in SKIP_DIRS but still worth descending
    into - `.adk`, which holds the eval history every result report reads.
    """
    root = root.resolve()
    stack = [(root, 0)]
    while stack:
        directory, depth = stack.pop()
        try:
            entries = sorted(directory.iterdir())
        except (OSError, PermissionError):
            continue
        for entry in entries:
            if entry.is_dir():
                if depth >= max_depth:
                    continue
                if entry.name in SKIP_DIRS and entry.name not in enter:
                    continue
                stack.append((entry, depth + 1))
            else:
                yield entry


def _classify_eval_files(root: Path, info: LayoutInfo) -> None:
    eval_dirs: set[str] = set()
    history_dirs: set[str] = set()
    resolved_root = root.resolve()
    for file in _walk(root, enter=frozenset({".adk"})):
        name = file.name
        rel = str(file)
        parents = file.parent.parts
        if ".adk" in parents:
            # Inside `.adk` the only thing worth recording is the eval history.
            if "eval_history" in parents:
                index = parents.index("eval_history")
                history_dirs.add(str(Path(*parents[: index + 1])))
            continue
        if name.endswith(EVALSET_SUFFIXES):
            info.evalsets.append(rel)
            eval_dirs.add(str(file.parent))
            if name.endswith(".test.json"):
                info.legacy_candidates.append(rel)
        elif name in ADK_CONFIG_NAMES:
            info.configs.append(rel)
            eval_dirs.add(str(file.parent))
        elif name in FOREIGN_CONFIG_NAMES:
            info.foreign_configs.append(rel)
        elif name == ".env" or name.startswith(".env."):
            info.env_files.append(rel)
        elif name == "spec.yaml" and file.parent.name != "evalsets":
            info.conformance_dirs.append(str(file.parent))
        elif name.startswith("test_") and name.endswith(".py") \
                and _has_eval_component(file, resolved_root):
            info.pytest_eval_files.append(rel)
    info.eval_dirs = sorted(eval_dirs)
    info.evalsets.sort()
    info.configs.sort()
    info.foreign_configs.sort()
    info.conformance_dirs = sorted(set(info.conformance_dirs))
    info.pytest_eval_files.sort()
    info.env_files.sort()
    info.eval_history_dirs = sorted(history_dirs)


def _has_eval_component(file: Path, root: Path) -> bool:
    """Does an `eval` directory sit between the project root and this file?

    Matching "eval" anywhere in the absolute path is wrong: a checkout under
    `/home/me/adk-eval-work/` would classify every test file in the repository
    as an eval gate.
    """
    try:
        relative = file.resolve().relative_to(root)
    except (ValueError, OSError):
        return False
    return any(part == "eval" or part.startswith("eval") for part in relative.parent.parts)


# ---------------------------------------------------------------------------
# layout detection
# ---------------------------------------------------------------------------

def _agent_entry_file(directory: Path) -> tuple[Path, str] | None:
    """Return (file, module_suffix) for a directory the loader would accept."""
    agent_py = directory / "agent.py"
    if agent_py.is_file():
        return agent_py, ".agent"
    init_py = directory / "__init__.py"
    if init_py.is_file():
        exports = scan_exports(init_py)
        if exports.loadable:
            return init_py, ""
    if (directory / "root_agent.yaml").is_file():
        return directory / "root_agent.yaml", ""
    return None


def is_agent_dir(directory: Path) -> bool:
    return _agent_entry_file(directory) is not None


def find_agent_dirs(root: Path | str, *, max_depth: int = 3) -> list[str]:
    """Every directory under `root` the ADK loader could load as an agent."""
    root = Path(root).resolve()
    found: list[str] = []
    if is_agent_dir(root):
        found.append(str(root))
    stack = [(root, 0)]
    while stack:
        directory, depth = stack.pop()
        if depth >= max_depth:
            continue
        try:
            entries = sorted(p for p in directory.iterdir() if p.is_dir())
        except (OSError, PermissionError):
            continue
        for entry in entries:
            if entry.name in SKIP_DIRS:
                continue
            if is_agent_dir(entry):
                found.append(str(entry))
            stack.append((entry, depth + 1))
    return sorted(set(found))


def detect_layout(path: Path | str) -> LayoutInfo:
    """Work out what `path` is and where its agent and eval files live.

    `path` may be the agent directory itself, a project root whose agent
    package is a subdirectory, a bare ``<name>.py`` module, or a directory
    holding several agents.
    """
    target = Path(path).expanduser()
    info = LayoutInfo(root=str(target.resolve()) if target.exists() else str(target))

    if not target.exists():
        info.notes.append("target does not exist")
        return info

    resolved = target.resolve()

    # A single-file agent module.
    if resolved.is_file() and resolved.suffix == ".py":
        exports = scan_exports(resolved)
        info.style = "module_file"
        info.agents_dir = str(resolved.parent)
        info.app_name = resolved.stem
        info.agent_module_file = str(resolved)
        info.module_path = resolved.stem
        info.exports = exports
        info.declared_app_name = exports.declared_app_name
        info.project_root = str(resolved.parent)
        _classify_eval_files(resolved.parent, info)
        return info

    if resolved.is_file():
        info.notes.append("target is a file, not a Python module or directory")
        return info

    # The directory itself is loadable.
    entry = _agent_entry_file(resolved)
    if entry is not None:
        _fill_agent_dir(info, resolved, entry)
        # A loadable dir named `app` inside a project is the recipes layout,
        # where `tests/eval/` is a sibling of the agent package.
        search_root = resolved
        if resolved.name == "app" and (resolved.parent / "tests").is_dir():
            info.style = "recipes"
            info.project_root = str(resolved.parent)
            search_root = resolved.parent
        else:
            # Flat agents keep their eval files beside the agent; scanning the
            # parent would pull in every sibling agent's evalsets.
            info.project_root = str(resolved)
        _classify_eval_files(search_root, info)
        _collect_workflow_files(info, resolved)
        return info

    # A project root whose agent package sits one level down.
    candidates = [
        child
        for child in sorted(resolved.iterdir())
        if child.is_dir() and child.name not in SKIP_DIRS and is_agent_dir(child)
    ]
    if len(candidates) == 1:
        agent_dir = candidates[0]
        entry = _agent_entry_file(agent_dir)
        assert entry is not None
        _fill_agent_dir(info, agent_dir, entry)
        info.project_root = str(resolved)
        info.style = "recipes" if agent_dir.name == "app" else "shim"
        _classify_eval_files(resolved, info)
        _collect_workflow_files(info, agent_dir)
        return info
    if len(candidates) > 1:
        info.style = "collection"
        info.agents_dir = str(resolved)
        info.project_root = str(resolved)
        # Agents may sit deeper than the immediate children (a project whose
        # package is `app/`), so walk rather than listing one level.
        info.sibling_agents = [p for p in find_agent_dirs(resolved) if p != str(resolved)]
        _classify_eval_files(resolved, info)
        info.notes.append(
            f"{len(candidates)} agents found; pick one before running eval commands"
        )
        return info

    # Nothing loadable: still report eval files so skills can explain.
    info.project_root = str(resolved)
    _classify_eval_files(resolved, info)
    info.notes.append("no ADK agent found (no agent.py, loadable __init__.py or root_agent.yaml)")
    return info


def _fill_agent_dir(info: LayoutInfo, agent_dir: Path, entry: tuple[Path, str]) -> None:
    entry_file, module_suffix = entry
    info.agent_dir = str(agent_dir)
    info.agents_dir = str(agent_dir.parent)
    info.project_root = str(agent_dir.parent)
    info.app_name = agent_dir.name
    info.agent_module_file = str(entry_file)
    info.module_path = f"{agent_dir.name}{module_suffix}"

    if entry_file.suffix == ".yaml":
        info.style = "yaml"
        info.notes.append(
            "YAML config agent: `BaseAgentConfig` is deprecated in ADK 2.x; plan a migration to code"
        )
        return

    exports = scan_exports(entry_file)
    # `__init__.py` commonly re-exports from `agent.py`; merge both views.
    init_py = agent_dir / "__init__.py"
    if init_py.is_file() and init_py != entry_file:
        init_exports = scan_exports(init_py)
        exports.has_app = exports.has_app or init_exports.has_app
        exports.has_root_agent = exports.has_root_agent or init_exports.has_root_agent
        exports.declared_app_name = exports.declared_app_name or init_exports.declared_app_name
    if exports.has_app and not exports.declared_app_name:
        # The App is built in a submodule; find its declared name there.
        for candidate in sorted(agent_dir.rglob("*.py")):
            if set(candidate.parts) & SKIP_DIRS:
                continue
            found = scan_exports(candidate)
            if found.declared_app_name:
                exports.declared_app_name = found.declared_app_name
                break
    info.exports = exports
    info.declared_app_name = exports.declared_app_name
    info.style = "flat" if info.style == "unknown" else info.style
    info.python_files = sorted(
        str(p) for p in agent_dir.rglob("*.py") if not set(p.parts) & SKIP_DIRS
    )


def _collect_workflow_files(info: LayoutInfo, agent_dir: Path) -> None:
    for py in agent_dir.rglob("*.py"):
        if set(py.parts) & SKIP_DIRS:
            continue
        try:
            # The imports and the `Workflow(` call are at the top of the file;
            # reading a 2 MB generated module to find them is pure waste.
            with py.open(encoding="utf-8", errors="ignore") as handle:
                text = handle.read(4096)
        except OSError:
            continue
        if "google.adk.workflow" in text or "Workflow(" in text:
            info.workflow_files.append(str(py))
    info.workflow_files.sort()
