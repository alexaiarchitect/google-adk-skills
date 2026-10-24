"""The eval wrapper: summary parsing, result reconciliation, and real exit codes.

`adk eval` prints a summary and always returns 0, and it counts NOT_EVALUATED in
the failed column. These tests pin the behaviour that makes a CI gate honest:
the wrapper reads the result files it wrote, not just its stdout.
"""

import pytest

from adk_skills.adk_subprocess import (
    build_adk_cmd, describe_runner, parse_summary, run_adk_eval, run_command,
)
from adk_skills import ADK_TARGET
from adk_skills.env import RunnerSpec


@pytest.fixture
def fake_runner(fixtures):
    return RunnerSpec(
        kind="explicit",
        argv=["python3", str(fixtures / "bin" / "fake_adk.py")],
        source="fake",
        adk_version=f"{ADK_TARGET}.0",
        verified=True,
    )


def test_summary_parsing_totals_and_splits_by_eval_set():
    text = (
        "Eval Run Summary\n"
        "expense_claims:\n  Tests passed: 3\n  Tests failed: 1\n"
        "judge_suite:\n  Tests passed: 2\n  Tests failed: 0\n"
    )
    summary = parse_summary(text)
    assert summary["passed"] == 5 and summary["failed"] == 1
    assert summary["per_eval_set"]["expense_claims"] == {"passed": 3, "failed": 1}


def test_summary_parsing_reports_when_there_was_no_summary():
    assert parse_summary("Traceback (most recent call last):")["found_summary"] is False


def test_adk_command_goes_through_the_resolved_interpreter(fake_runner):
    argv = build_adk_cmd(fake_runner, ["eval", "agent", "cases.json"])
    assert argv[:4] == [*fake_runner.argv, "-m", "google.adk.cli"][:4]
    assert argv[-3:] == ["eval", "agent", "cases.json"]


def test_a_passing_run_is_clean_and_writes_a_result_file(tmp_agent, fake_runner, monkeypatch):
    agent = tmp_agent("flat_good")
    monkeypatch.setenv("FAKE_ADK_PASSED", "3")
    monkeypatch.setenv("FAKE_ADK_FAILED", "0")
    outcome = run_adk_eval(agent, ["cases.evalset.json"], runner=fake_runner)
    assert outcome.command.ok
    assert outcome.passed == 3 and outcome.failed == 0
    assert outcome.clean is True
    assert outcome.new_result_files, "the run's own results are what make the verdict trustworthy"


def test_a_failing_run_is_not_clean_even_though_adk_exits_zero(tmp_agent, fake_runner, monkeypatch):
    """Through 2.12.0 `adk eval` exits 0 on a failure; the verdict comes from the
    result files on every version."""
    agent = tmp_agent("flat_good")
    monkeypatch.setenv("FAKE_ADK_VERSION", "2.12.0")
    monkeypatch.setenv("FAKE_ADK_PASSED", "1")
    monkeypatch.setenv("FAKE_ADK_FAILED", "2")
    outcome = run_adk_eval(agent, ["cases.evalset.json"], runner=fake_runner)
    assert outcome.command.returncode == 0, "this is exactly why the wrapper exists"
    assert outcome.failed == 2 and outcome.clean is False
    assert len(outcome.failing_case_ids()) == 2


def test_not_evaluated_is_separated_from_failure(tmp_agent, fake_runner, monkeypatch):
    agent = tmp_agent("flat_good")
    monkeypatch.setenv("FAKE_ADK_PASSED", "1")
    monkeypatch.setenv("FAKE_ADK_FAILED", "0")
    monkeypatch.setenv("FAKE_ADK_NOT_EVALUATED", "2")
    outcome = run_adk_eval(agent, ["cases.evalset.json"], runner=fake_runner)
    assert outcome.not_evaluated == 2
    assert outcome.failed == 0, "the printed summary would have called these failures"
    assert outcome.clean is False
    assert len(outcome.not_evaluated_case_ids()) == 2


def test_case_selection_is_appended_to_the_evalset_path(tmp_agent, fake_runner, monkeypatch):
    agent = tmp_agent("flat_good")
    monkeypatch.setenv("FAKE_ADK_PASSED", "1")
    outcome = run_adk_eval(agent, ["cases.evalset.json"], runner=fake_runner, cases=["a", "b"])
    assert "cases.evalset.json:a,b" in " ".join(outcome.command.argv)


def test_config_and_flags_are_passed_through(tmp_agent, fake_runner, monkeypatch):
    agent = tmp_agent("flat_good")
    monkeypatch.setenv("FAKE_ADK_PASSED", "1")
    outcome = run_adk_eval(
        agent, ["cases.evalset.json"], runner=fake_runner,
        config="tests/eval/eval_config.json", detailed=True, storage_uri="gs://bucket/evals",
    )
    joined = " ".join(outcome.command.argv)
    assert "--config_file_path=tests/eval/eval_config.json" in joined
    assert "--print_detailed_results" in joined
    assert "--eval_storage_uri=gs://bucket/evals" in joined


def test_a_run_that_writes_nothing_is_not_clean(tmp_agent, fake_runner, monkeypatch):
    agent = tmp_agent("flat_good")
    monkeypatch.setenv("FAKE_ADK_NO_RESULT_FILE", "1")
    monkeypatch.setenv("FAKE_ADK_PASSED", "0")
    monkeypatch.setenv("FAKE_ADK_FAILED", "0")
    outcome = run_adk_eval(agent, ["cases.evalset.json"], runner=fake_runner)
    assert outcome.new_result_files == []
    assert outcome.clean is False


def test_a_crashing_command_is_reported(tmp_agent, monkeypatch, fixtures):
    agent = tmp_agent("flat_good")
    runner = RunnerSpec(kind="explicit", argv=["python3", str(fixtures / "bin" / "fake_adk.py")],
                        source="fake", verified=True)
    monkeypatch.setenv("FAKE_ADK_RETURNCODE", "1")
    monkeypatch.setenv("FAKE_ADK_PASSED", "0")
    outcome = run_adk_eval(agent, ["cases.evalset.json"], runner=runner)
    assert outcome.command.ok is False


def test_a_timeout_is_reported_rather_than_hanging():
    outcome = run_command(["python3", "-c", "import time; time.sleep(5)"], timeout=0.5)
    assert outcome.timed_out and outcome.ok is False


def test_describe_runner_says_when_adk_was_never_verified():
    unverified = RunnerSpec(kind="venv", argv=["python3"], source=".venv/bin/python")
    assert "unverified" in describe_runner(unverified)
    assert "missing" not in describe_runner(unverified)
