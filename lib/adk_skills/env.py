"""Backend, `.env`, model-policy and Python-runner detection.

ADK looks for an agent's `.env` starting at the agent directory and walking up
to the filesystem root, taking the first one it finds
(``cli/utils/envs.py::_walk_to_root_until_found``). A repository-root `.env` is
therefore loaded for every agent below it, which is how most real projects are
arranged. Two backends exist:

* **Vertex AI** — ``GOOGLE_GENAI_USE_VERTEXAI=TRUE`` plus
  ``GOOGLE_CLOUD_PROJECT`` and ``GOOGLE_CLOUD_LOCATION``, authenticated by
  Application Default Credentials.
* **Gemini API (AI Studio)** — ``GOOGLE_API_KEY`` (or ``GEMINI_API_KEY``).

The metrics served by the Vertex AI Gen AI Evaluation Service accept *either*:
``vertex_ai_eval_facade.py`` reads ``GOOGLE_API_KEY`` first and only falls back
to project + location, so an API key alone can run ``safety_v1`` and the
``multi_turn_*`` metrics. They still bill differently, which is why the `vertex`
cost tier stays.

Scripts that need `google.adk` itself never assume it is importable in the
interpreter running this plugin. `resolve_runner` finds one that has it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from . import ADK_TARGET, DEFAULT_MODEL, DEPRECATED_MODELS

#: Pin used when the runner has to install ADK on the fly.
ADK_REQUIREMENT = f'google-adk[eval]=={ADK_TARGET}.*'

_ENV_LINE = re.compile(r"""^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$""")

#: Model ids that are alias-style and therefore never go stale.
ALIAS_MODELS = ("gemini-flash-latest", "gemini-pro-latest", "gemini-flash-lite-latest")


def read_dotenv(path: Path | str) -> dict[str, str]:
    """Parse a `.env` file. Deliberately small: no interpolation, no plugins."""
    values: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _ENV_LINE.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if value and value[0] in "\"'" and value[-1:] == value[0]:
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        values[key] = value
    return values


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class BackendInfo:
    """Which GenAI backend an agent is configured for, and what is missing."""

    backend: str = "unknown"          # vertex | api_key | unknown
    source: str | None = None          # the .env file the values came from
    project: str | None = None
    location: str | None = None
    has_api_key: bool = False
    use_vertex: bool = False
    model_name: str | None = None
    missing: list[str] = field(default_factory=list)
    #: `.env` files that are actually loadable, nearest first.
    env_files: list[str] = field(default_factory=list)
    #: `.env.example` and friends: documentation, never configuration.
    example_files: list[str] = field(default_factory=list)
    values: dict[str, str] = field(default_factory=dict)

    @property
    def vertex_ready(self) -> bool:
        """Can the Vertex-served metrics (`safety_v1`, `multi_turn_*`, …) run?

        `vertex_ai_eval_facade.py` takes `GOOGLE_API_KEY` if it is set and only
        then falls back to `GOOGLE_CLOUD_PROJECT` + `GOOGLE_CLOUD_LOCATION`
        with ADC, so either credential is enough.
        """
        return bool((self.project and self.location) or self.has_api_key)

    @property
    def judge_ready(self) -> bool:
        """Can judge-model metrics run at all?"""
        return self.vertex_ready or self.has_api_key

    @property
    def vertex_credential(self) -> str | None:
        """Which credential the Vertex facade would pick, in its own order."""
        if self.has_api_key:
            return "GOOGLE_API_KEY"
        if self.project and self.location:
            return "GOOGLE_CLOUD_PROJECT + GOOGLE_CLOUD_LOCATION (ADC)"
        return None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("values", None)
        data["vertex_ready"] = self.vertex_ready
        data["judge_ready"] = self.judge_ready
        data["vertex_credential"] = self.vertex_credential
        return data


def detect_backend(agent_dir: Path | str | None, *, include_os_environ: bool = True) -> BackendInfo:
    """Read backend configuration for an agent from its `.env`, then the process
    environment. Values in `.env` win, because that is what ADK loads."""
    info = BackendInfo()
    merged: dict[str, str] = {}

    if include_os_environ:
        for key in (
            "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION",
            "GOOGLE_API_KEY", "GEMINI_API_KEY", "MODEL_NAME",
            "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_GENAI_USE_ENTERPRISE",
        ):
            if os.environ.get(key):
                merged[key] = os.environ[key]

    if agent_dir:
        # Mirror `cli/utils/envs.py`: start at the agent directory, walk to the
        # filesystem root, first `.env` wins. Anything farther up is shadowed.
        directory = Path(agent_dir).resolve()
        for folder in (directory, *directory.parents):
            example = folder / ".env.example"
            if example.is_file():
                info.example_files.append(str(example))
            candidate = folder / ".env"
            if candidate.is_file() and info.source is None:
                info.env_files.append(str(candidate))
                info.source = str(candidate)
                values = read_dotenv(candidate)
                if values:
                    merged.update(values)

    info.values = merged
    info.use_vertex = _truthy(merged.get("GOOGLE_GENAI_USE_VERTEXAI")) or _truthy(
        merged.get("GOOGLE_GENAI_USE_ENTERPRISE")
    )
    info.project = merged.get("GOOGLE_CLOUD_PROJECT") or None
    info.location = merged.get("GOOGLE_CLOUD_LOCATION") or None
    info.has_api_key = bool(merged.get("GOOGLE_API_KEY") or merged.get("GEMINI_API_KEY"))
    info.model_name = merged.get("MODEL_NAME") or None

    if info.use_vertex:
        info.backend = "vertex"
        if not info.project:
            info.missing.append("GOOGLE_CLOUD_PROJECT")
        if not info.location:
            info.missing.append("GOOGLE_CLOUD_LOCATION")
    elif info.has_api_key:
        info.backend = "api_key"
    else:
        info.backend = "unknown"
        info.missing.append(
            "either GOOGLE_API_KEY, or GOOGLE_CLOUD_PROJECT + GOOGLE_CLOUD_LOCATION "
            "with GOOGLE_GENAI_USE_VERTEXAI=TRUE and ADC"
        )
    return info


#: One sentence every script uses when it has to say what is missing, so the
#: instruction the user reads is the same wherever they meet it.
BACKEND_REQUIREMENT = (
    "needs either GOOGLE_API_KEY, or GOOGLE_CLOUD_PROJECT + GOOGLE_CLOUD_LOCATION with ADC"
)


def classify_model_id(model: str | None) -> str:
    """Grade a model id against this plugin's model policy.

    ``alias`` future-proof (``gemini-flash-latest``), ``deprecated`` retired or
    retiring, ``preview`` a preview id that will disappear, ``pinned`` a dated
    or versioned id that will age, ``env`` resolved at runtime, ``unknown``.
    """
    if model is None or model == "":
        return "unknown"
    if model in ALIAS_MODELS or model.endswith("-latest"):
        return "alias"
    if model in DEPRECATED_MODELS:
        return "deprecated"
    if "preview" in model or "-exp" in model:
        return "preview"
    if model.startswith(("gemini-", "claude-", "publishers/")):
        return "pinned"
    return "unknown"


@dataclass
class RunnerSpec:
    """How to invoke a Python that can import `google.adk`."""

    kind: str                      # explicit | venv | system | uv | missing
    argv: list[str] = field(default_factory=list)
    source: str | None = None
    adk_version: str | None = None
    note: str | None = None
    #: True when we actually imported google.adk with this interpreter. A
    #: candidate found by path alone may not have ADK installed at all.
    verified: bool = False

    @property
    def available(self) -> bool:
        return self.kind != "missing"

    def python_argv(self) -> list[str]:
        return list(self.argv)

    def adk_argv(self) -> list[str]:
        """Argv prefix that runs the `adk` CLI through this runner."""
        return [*self.argv, "-m", "google.adk.cli"]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["available"] = self.available
        return data


def _has_adk(argv: list[str], *, timeout: float = 90.0) -> str | None:
    """Return the ADK version importable by `argv`, else None."""
    code = "import google.adk as a; print(a.version.__version__)"
    try:
        proc = subprocess.run(
            [*argv, "-c", code],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def candidate_pythons(agent_dir: Path | str | None, project_root: Path | str | None = None) -> list[tuple[str, list[str], str]]:
    """Interpreters worth probing, most specific first."""
    candidates: list[tuple[str, list[str], str]] = []

    explicit = os.environ.get("ADK_PYTHON")
    if explicit:
        candidates.append(("explicit", [explicit], "$ADK_PYTHON"))

    roots: list[Path] = []
    for base in (agent_dir, project_root):
        if not base:
            continue
        path = Path(base).resolve()
        roots.extend([path, *path.parents][:4])
    seen: set[Path] = set()
    for root in roots:
        if root in seen:
            continue
        seen.add(root)
        for venv in (".venv", "venv", ".env"):
            python = root / venv / "bin" / "python"
            if python.is_file():
                candidates.append(("venv", [str(python)], str(python)))
            win = root / venv / "Scripts" / "python.exe"
            if win.is_file():
                candidates.append(("venv", [str(win)], str(win)))

    adk_cli = shutil.which("adk")
    if adk_cli:
        candidates.append(("system", [os.environ.get("PYTHON", "python3")], f"PATH ({adk_cli})"))
    return candidates


def resolve_runner(
    agent_dir: Path | str | None = None,
    project_root: Path | str | None = None,
    *,
    probe: bool = True,
    allow_uv: bool = True,
) -> RunnerSpec:
    """Find a Python that can import ADK, or say how to get one.

    Order: ``$ADK_PYTHON`` → a virtualenv at or above the agent → the
    interpreter that owns an ``adk`` on PATH → ``uv run --with`` (which
    installs ADK into a throwaway environment) → missing.
    """
    for kind, argv, source in candidate_pythons(agent_dir, project_root):
        if not probe:
            return RunnerSpec(
                kind=kind, argv=argv, source=source, verified=False,
                note="not verified: run without --no-probe to confirm google.adk imports",
            )
        version = _has_adk(argv)
        if version:
            return RunnerSpec(
                kind=kind, argv=argv, source=source, adk_version=version, verified=True
            )

    uv = shutil.which("uv")
    if allow_uv and uv:
        argv = [uv, "run", "--with", ADK_REQUIREMENT, "python"]
        return RunnerSpec(
            kind="uv",
            argv=argv,
            source=f"{uv} run --with {ADK_REQUIREMENT}",
            verified=True,
            note="first run downloads ADK into a temporary environment",
        )

    return RunnerSpec(
        kind="missing",
        argv=[],
        source=None,
        note=(
            "No interpreter with google.adk. Install uv (`brew install uv`) for "
            f'`uv run --with "{ADK_REQUIREMENT}"`, or create a venv: '
            f'`python3 -m venv .venv && .venv/bin/pip install "{ADK_REQUIREMENT}"`, '
            "then re-run with ADK_PYTHON=.venv/bin/python."
        ),
    )


def adk_version(runner: RunnerSpec) -> str | None:
    if runner.adk_version:
        return runner.adk_version
    if not runner.available:
        return None
    return _has_adk(runner.argv)


def _version_tuple(version: str) -> tuple[int, ...]:
    """`"2.10.0rc1"` -> `(2, 10, 0)`. Non-numeric tails are dropped rather than
    guessed at, so a pre-release compares equal to its release."""
    parts: list[int] = []
    for chunk in version.strip().split("."):
        digits = ""
        for char in chunk:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def version_is_supported(version: str | None) -> bool:
    """This plugin targets ADK 2.x: 2.9 through 2.11 are all supported."""
    if not version:
        return False
    return version.split(".", 1)[0] == ADK_TARGET.split(".", 1)[0]


def version_at_least(version: str | None, minimum: str = ADK_TARGET) -> bool:
    """Is `version` at least `minimum`, comparing numerically per component?

    `"2.9.2"` is **older** than `"2.10.0"`, which a string comparison gets
    wrong, and that pair is exactly the one every 2.10-only behaviour turns on.
    An unknown version is not assumed to be new enough.
    """
    if not version:
        return False
    return _version_tuple(version) >= _version_tuple(minimum)


def default_judge_model(backend: BackendInfo | None = None) -> str:
    """The judge model generated configs should name explicitly."""
    if backend and backend.model_name:
        return backend.model_name
    return DEFAULT_MODEL
