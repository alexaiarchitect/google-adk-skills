"""Shared test fixtures.

The plugin's library is stdlib-only, so tests import it directly. Scripts are
exercised as subprocesses, the way a skill runs them, so their argument parsing
and exit codes are covered too.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
LIB = PLUGIN_ROOT / "lib"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

sys.path.insert(0, str(LIB))


@dataclass
class ScriptRun:
    returncode: int
    stdout: str
    stderr: str

    def json(self) -> dict:
        return json.loads(self.stdout)

    @property
    def combined(self) -> str:
        return f"{self.stdout}\n{self.stderr}"


@pytest.fixture(scope="session")
def plugin_root() -> Path:
    return PLUGIN_ROOT


@pytest.fixture(scope="session")
def fixtures() -> Path:
    return FIXTURES


@pytest.fixture
def run_script():
    """Run a plugin script by its path relative to the plugin root."""

    def _run(relative: str, *args: str, cwd: Path | str | None = None) -> ScriptRun:
        script = PLUGIN_ROOT / relative
        assert script.is_file(), f"no such script: {script}"
        proc = subprocess.run(
            [sys.executable, str(script), *[str(a) for a in args]],
            capture_output=True, text=True, cwd=str(cwd) if cwd else None, timeout=300,
        )
        return ScriptRun(proc.returncode, proc.stdout, proc.stderr)

    return _run


@pytest.fixture
def agent_dir(fixtures: Path):
    """A fixture agent by name, e.g. `agent_dir("flat_good")`."""

    def _get(name: str) -> Path:
        path = fixtures / "agents" / name
        assert path.is_dir(), f"no such fixture agent: {path}"
        return path

    return _get


@pytest.fixture
def tmp_agent(tmp_path: Path, fixtures: Path):
    """A writable copy of a fixture agent, for tests that generate files."""
    import shutil

    def _copy(name: str) -> Path:
        source = fixtures / "agents" / name
        target = tmp_path / name
        shutil.copytree(source, target)
        return target

    return _copy
