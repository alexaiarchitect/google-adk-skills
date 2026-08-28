"""Run ADK commands through a resolved interpreter, and gate on the outcome.

The important detail: **`adk eval` does not set an exit code.** It prints

    Eval Run Summary
    <eval_set_id>:
      Tests passed: 3
      Tests failed: 1

and returns 0 whether or not anything failed, and it counts `NOT_EVALUATED` in
the failed column. So a CI step that runs the bare command passes forever. This
module parses the summary, finds the result files the run wrote, and produces an
exit code that means something.

`AgentEvaluator` under pytest is the other honest gate: it raises an
`AssertionError` on failure, so pytest's own exit code is usable directly.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Sequence

from .env import RunnerSpec, resolve_runner
from .results import RESULT_SUFFIX, RunResult, parse_result

SUMMARY_PASSED = re.compile(r"Tests?\s+passed:\s*(\d+)", re.I)
SUMMARY_FAILED = re.compile(r"Tests?\s+failed:\s*(\d+)", re.I)
SUMMARY_SET = re.compile(r"^\s*([A-Za-z0-9_./:-]+):\s*$")

MISSING_EVAL_EXTRA = re.compile(r"google-adk\[eval\]|eval dependencies|MISSING_EVAL", re.I)

DEFAULT_TIMEOUT = 1800.0


@dataclass
class CommandOutcome:
    """One subprocess invocation."""

    argv: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    duration: float = 0.0
    timed_out: bool = False
    cwd: str | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    @property
    def combined(self) -> str:
        return f"{self.stdout}\n{self.stderr}".strip()

    def tail(self, lines: int = 20) -> str:
        return "\n".join(self.combined.splitlines()[-lines:])

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": " ".join(self.argv),
            "returncode": self.returncode,
            "duration_seconds": round(self.duration, 2),
            "timed_out": self.timed_out,
            "cwd": self.cwd,
            "stdout_tail": "\n".join(self.stdout.splitlines()[-30:]),
            "stderr_tail": "\n".join(self.stderr.splitlines()[-15:]),
        }


def run_command(
    argv: Sequence[str],
    *,
    cwd: Path | str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    env: dict[str, str] | None = None,
) -> CommandOutcome:
    started = time.monotonic()
    merged = {**os.environ, **(env or {})}
    try:
        proc = subprocess.run(
            list(argv),
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=merged,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandOutcome(
            argv=list(argv), returncode=124,
            stdout=exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            stderr=f"timed out after {timeout:g}s",
            duration=time.monotonic() - started, timed_out=True,
            cwd=str(cwd) if cwd else None,
        )
    except OSError as exc:
        return CommandOutcome(argv=list(argv), returncode=127, stderr=str(exc),
                              duration=time.monotonic() - started,
                              cwd=str(cwd) if cwd else None)
    return CommandOutcome(
        argv=list(argv), returncode=proc.returncode, stdout=proc.stdout or "",
        stderr=proc.stderr or "", duration=time.monotonic() - started,
        cwd=str(cwd) if cwd else None,
    )


def build_adk_cmd(runner: RunnerSpec, args: Sequence[str]) -> list[str]:
    """`adk <args>` through the resolved interpreter (`python -m google.adk.cli`)."""
    return [*runner.adk_argv(), *args]


def parse_summary(text: str) -> dict[str, Any]:
    """Read `Tests passed/failed` out of `adk eval` output.

    Note that `NOT_EVALUATED` cases land in the failed count, so a failure here
    is not necessarily a score regression - check the result file to tell them
    apart.
    """
    passed = sum(int(m.group(1)) for m in SUMMARY_PASSED.finditer(text))
    failed = sum(int(m.group(1)) for m in SUMMARY_FAILED.finditer(text))
    per_set: dict[str, dict[str, int]] = {}
    current: str | None = None
    for line in text.splitlines():
        heading = SUMMARY_SET.match(line)
        if heading and "Summary" not in heading.group(1):
            current = heading.group(1)
            per_set.setdefault(current, {"passed": 0, "failed": 0})
            continue
        if current:
            hit_pass = SUMMARY_PASSED.search(line)
            hit_fail = SUMMARY_FAILED.search(line)
            if hit_pass:
                per_set[current]["passed"] = int(hit_pass.group(1))
            if hit_fail:
                per_set[current]["failed"] = int(hit_fail.group(1))
    return {
        "passed": passed,
        "failed": failed,
        "per_eval_set": per_set,
        "found_summary": bool(SUMMARY_PASSED.search(text) or SUMMARY_FAILED.search(text)),
    }


@dataclass
class EvalOutcome:
    """An `adk eval` invocation, reconciled with the result files it wrote."""

    command: CommandOutcome
    passed: int = 0
    failed: int = 0
    not_evaluated: int = 0
    new_result_files: list[str] = field(default_factory=list)
    runs: list[RunResult] = field(default_factory=list)
    missing_eval_extra: bool = False

    @property
    def cases(self) -> int:
        return self.passed + self.failed

    @property
    def clean(self) -> bool:
        return self.command.ok and self.failed == 0 and self.not_evaluated == 0 and self.cases > 0

    def failing_case_ids(self) -> list[str]:
        ids: list[str] = []
        for run in self.runs:
            ids.extend(case.eval_id for case in run.cases if case.status == "FAILED")
        return ids

    def not_evaluated_case_ids(self) -> list[str]:
        ids: list[str] = []
        for run in self.runs:
            ids.extend(case.eval_id for case in run.cases if case.status == "NOT_EVALUATED")
        return ids

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command.to_dict(),
            "passed": self.passed,
            "failed": self.failed,
            "not_evaluated": self.not_evaluated,
            "failing_cases": self.failing_case_ids(),
            "not_evaluated_cases": self.not_evaluated_case_ids(),
            "result_files": self.new_result_files,
            "clean": self.clean,
            "missing_eval_extra": self.missing_eval_extra,
        }


def _snapshot_results(agent_dir: Path) -> dict[str, float]:
    history = agent_dir / ".adk" / "eval_history"
    if not history.is_dir():
        return {}
    return {
        str(path): path.stat().st_mtime
        for path in history.glob(f"*{RESULT_SUFFIX}")
    }


def run_adk_eval(
    agent_dir: Path | str,
    evalsets: Sequence[str],
    *,
    runner: RunnerSpec | None = None,
    config: str | None = None,
    cases: Sequence[str] = (),
    detailed: bool = False,
    storage_uri: str | None = None,
    cwd: Path | str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    env: dict[str, str] | None = None,
) -> EvalOutcome:
    """Run `adk eval` once and reconcile stdout with the result files.

    `cases` selects individual eval ids, which ADK expresses by appending
    ``:id1,id2`` to the eval set path.
    """
    agent = Path(agent_dir).resolve()
    runner = runner or resolve_runner(agent, agent.parent)
    work_dir = Path(cwd).resolve() if cwd else agent.parent

    targets: list[str] = []
    for evalset in evalsets:
        target = str(evalset)
        if cases:
            target = f"{target}:{','.join(cases)}"
        targets.append(target)

    args = ["eval", str(agent), *targets]
    if config:
        args.append(f"--config_file_path={config}")
    if detailed:
        args.append("--print_detailed_results")
    if storage_uri:
        args.append(f"--eval_storage_uri={storage_uri}")

    before = _snapshot_results(agent)
    command = run_command(build_adk_cmd(runner, args), cwd=work_dir, timeout=timeout, env=env)
    after = _snapshot_results(agent)

    outcome = EvalOutcome(command=command)
    outcome.missing_eval_extra = bool(MISSING_EVAL_EXTRA.search(command.combined)) and not command.ok

    summary = parse_summary(command.combined)
    outcome.passed = summary["passed"]
    outcome.failed = summary["failed"]

    fresh = [
        path for path, mtime in sorted(after.items(), key=lambda kv: kv[1])
        if path not in before or mtime > before[path]
    ]
    outcome.new_result_files = fresh
    for path in fresh:
        run = parse_result(path)
        if run.ok:
            outcome.runs.append(run)

    if outcome.runs:
        # Result files distinguish a real failure from a metric that never ran;
        # the printed summary does not.
        outcome.passed = sum(run.passed for run in outcome.runs)
        outcome.failed = sum(run.failed for run in outcome.runs)
        outcome.not_evaluated = sum(run.not_evaluated for run in outcome.runs)
    return outcome


def run_pytest(
    args: Sequence[str],
    *,
    runner: RunnerSpec | None = None,
    cwd: Path | str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    env: dict[str, str] | None = None,
) -> CommandOutcome:
    """Run pytest through the resolved interpreter.

    `AgentEvaluator` asserts on failure, so pytest's exit code is a real gate.
    """
    runner = runner or resolve_runner(cwd)
    return run_command([*runner.python_argv(), "-m", "pytest", *args], cwd=cwd, timeout=timeout, env=env)


def run_adk_run_jsonl(
    agent_dir: Path | str,
    prompt: str,
    *,
    runner: RunnerSpec | None = None,
    cwd: Path | str | None = None,
    timeout: float = 600.0,
    extra_args: Sequence[str] = (),
) -> CommandOutcome:
    """Reproduce one interaction with structured output.

    Without `--jsonl` the command prints only text, so tool calls and tool
    errors never appear.
    """
    agent = Path(agent_dir).resolve()
    runner = runner or resolve_runner(agent, agent.parent)
    args = ["run", str(agent), "--jsonl", *extra_args, prompt]
    return run_command(build_adk_cmd(runner, args), cwd=Path(cwd) if cwd else agent.parent, timeout=timeout)


def run_adk(
    args: Sequence[str],
    *,
    runner: RunnerSpec | None = None,
    cwd: Path | str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> CommandOutcome:
    """Any other `adk` subcommand (eval_set, conformance, ...)."""
    runner = runner or resolve_runner(cwd)
    return run_command(build_adk_cmd(runner, args), cwd=cwd, timeout=timeout)


def describe_runner(runner: RunnerSpec) -> str:
    if not runner.available:
        return runner.note or "no interpreter with google.adk"
    if runner.adk_version:
        return f"{runner.kind}: {runner.source} (ADK {runner.adk_version})"
    if not runner.verified:
        return f"{runner.kind}: {runner.source} (unverified - google.adk not checked)"
    return f"{runner.kind}: {runner.source}"
