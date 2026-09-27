"""eval-run and eval-analyze scripts.

The eval-run tests exist mainly to pin one behaviour: `adk eval` exits 0 even
when cases fail, so the wrapper's exit code must come from the result files.
"""

import json

import pytest


@pytest.fixture
def fake_adk_env(fixtures, monkeypatch):
    monkeypatch.setenv("ADK_PYTHON", str(fixtures / "bin" / "fake_adk.py"))
    monkeypatch.setenv("FAKE_ADK_EVAL_SET", "happy")


def run_with(run_script, agent, **env):
    import os

    previous = {k: os.environ.get(k) for k in env}
    os.environ.update({k: str(v) for k, v in env.items()})
    try:
        return run_script(
            "skills/eval-run/scripts/run_evals.py", agent,
            "--evalset", agent / "tests/eval/evalsets/happy.evalset.json", "--json",
        )
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_dry_run_needs_no_adk_and_passes_the_config_explicitly(run_script, agent_dir):
    result = run_script("skills/eval-run/scripts/run_evals.py", agent_dir("recipes_good"), "--dry-run")
    assert result.returncode == 0
    assert "adk eval" in result.stdout
    assert "--config_file_path=" in result.stdout, "the recipes layout never auto-discovers it"


def test_a_passing_run_exits_zero(run_script, tmp_agent, fake_adk_env):
    agent = tmp_agent("recipes_good")
    result = run_with(run_script, agent, FAKE_ADK_PASSED=3, FAKE_ADK_FAILED=0)
    payload = json.loads(result.stdout)
    assert payload["totals"]["passed"] == 3
    assert result.returncode == 0


def test_failures_exit_one_even_though_adk_eval_exits_zero(run_script, tmp_agent, fake_adk_env):
    agent = tmp_agent("recipes_good")
    result = run_with(run_script, agent, FAKE_ADK_PASSED=1, FAKE_ADK_FAILED=2)
    payload = json.loads(result.stdout)
    assert payload["totals"]["failed"] == 2
    assert len(payload["failing_cases"]) == 2
    assert result.returncode == 1, "this wrapper exists precisely for this"


def test_never_scored_cases_are_kept_apart_from_failures(run_script, tmp_agent, fake_adk_env):
    agent = tmp_agent("recipes_good")
    result = run_with(run_script, agent, FAKE_ADK_PASSED=1, FAKE_ADK_FAILED=0,
                      FAKE_ADK_NOT_EVALUATED=2)
    payload = json.loads(result.stdout)
    assert payload["totals"]["not_evaluated"] == 2
    assert payload["totals"]["failed"] == 0
    assert result.returncode == 1, "the default policy fails on an unscored case"


def test_never_scored_can_be_treated_as_inconclusive(run_script, tmp_agent, fixtures):
    import os

    agent = tmp_agent("recipes_good")
    os.environ.update({"ADK_PYTHON": str(fixtures / "bin" / "fake_adk.py"),
                       "FAKE_ADK_PASSED": "1", "FAKE_ADK_FAILED": "0",
                       "FAKE_ADK_NOT_EVALUATED": "1"})
    try:
        result = run_script(
            "skills/eval-run/scripts/run_evals.py", agent,
            "--evalset", agent / "tests/eval/evalsets/happy.evalset.json",
            "--not-evaluated", "warn",
        )
        assert result.returncode == 3
    finally:
        for key in ("ADK_PYTHON", "FAKE_ADK_PASSED", "FAKE_ADK_FAILED", "FAKE_ADK_NOT_EVALUATED"):
            os.environ.pop(key, None)


def test_repeated_runs_are_kept_separate_for_pass_at_k(run_script, tmp_agent, fake_adk_env):
    agent = tmp_agent("recipes_good")
    import os

    os.environ.update({"FAKE_ADK_PASSED": "2", "FAKE_ADK_FAILED": "0"})
    try:
        result = run_script(
            "skills/eval-run/scripts/run_evals.py", agent,
            "--evalset", agent / "tests/eval/evalsets/happy.evalset.json",
            "--runs", "3", "--json",
        )
    finally:
        os.environ.pop("FAKE_ADK_PASSED", None)
        os.environ.pop("FAKE_ADK_FAILED", None)
    payload = json.loads(result.stdout)
    assert payload["runs"] == 3
    assert len(payload["result_files"]) == 3, "one file per run, not an average"


def test_always_on_efficiency_metrics_do_not_change_the_verdict(run_script, tmp_agent, fake_adk_env):
    """ADK 2.10 appends four informational metric results to every case. They
    carry `eval_status` 4, so they must not be counted as passes or as skips."""
    agent = tmp_agent("recipes_good")
    result = run_with(run_script, agent, FAKE_ADK_PASSED=1, FAKE_ADK_FAILED=1,
                      FAKE_ADK_EFFICIENCY="1")
    payload = json.loads(result.stdout)
    assert payload["totals"] == {"passed": 1, "failed": 1, "not_evaluated": 0}
    assert result.returncode == 1

    analyzed = run_script(
        "skills/eval-analyze/scripts/summarize_results.py", agent / "app", "--json",
    )
    cases = json.loads(analyzed.stdout)[0]["cases"]
    for case in cases:
        assert [m["name"] for m in case["metrics"]] == ["tool_trajectory_avg_score"]
        assert len(case["efficiency"]) == 4
        assert case["unevaluated_metrics"] == []


def test_a_missing_runner_is_reported_with_the_fix(run_script, agent_dir, monkeypatch):
    monkeypatch.setenv("ADK_PYTHON", "/nonexistent/python")
    result = run_script(
        "skills/eval-run/scripts/run_evals.py", agent_dir("recipes_good"),
        "--evalset", agent_dir("recipes_good") / "tests/eval/evalsets/happy.evalset.json",
    )
    assert result.returncode == 2
    assert "google-adk[eval]" in result.stderr


def test_a_project_with_no_evalsets_says_so(run_script, tmp_agent):
    agent = tmp_agent("flat_good")
    result = run_script("skills/eval-run/scripts/run_evals.py", agent, "--dry-run")
    assert result.returncode == 2
    assert "eval-create" in result.stderr


def test_registry_dry_run_lists_every_suite_command(run_script, tmp_agent, plugin_root):
    import json as _json
    import shutil

    agent = tmp_agent("recipes_good")
    registry = agent / "eval_registry.json"
    shutil.copy(plugin_root / "skills/eval-run/assets/templates/eval_registry.json", registry)
    manifest = _json.loads(registry.read_text())
    manifest["suites"] = [s for s in manifest["suites"] if s["tier"] == "pr"]
    registry.write_text(_json.dumps(manifest))

    result = run_script("skills/eval-run/scripts/run_registry.py", "--registry", registry, "--dry-run")
    assert result.returncode == 0
    assert "trajectory_pr" in result.stdout


def test_registry_rejects_an_unknown_schema(run_script, tmp_path):
    registry = tmp_path / "r.json"
    registry.write_text('{"schema": "something-else/1", "suites": []}')
    result = run_script("skills/eval-run/scripts/run_registry.py", "--registry", registry)
    assert result.returncode == 2 and "schema" in result.stderr


# -- eval-analyze ----------------------------------------------------------

def test_summary_finds_the_dangerous_quadrant(run_script, fixtures):
    result = run_script(
        "skills/eval-analyze/scripts/summarize_results.py",
        fixtures / "eval_history/app_expense_claims_1730000000.evalset_result.json", "--json",
    )
    cases = {c["eval_id"]: c for c in json.loads(result.stdout)[0]["cases"]}
    failing = cases["happy_travel_within_policy"]
    assert failing["quadrant"]["quadrant"] == "Q3"
    assert result.returncode == 1


def test_summary_locates_the_first_divergence(run_script, fixtures):
    result = run_script(
        "skills/eval-analyze/scripts/summarize_results.py",
        fixtures / "eval_history/app_expense_claims_1730000000.evalset_result.json", "--json",
    )
    cases = {c["eval_id"]: c for c in json.loads(result.stdout)[0]["cases"]}
    divergence = cases["happy_travel_within_policy"]["divergences"][0]
    assert divergence["divergence_index"] == 1
    assert divergence["divergence_kind"] == "wrong_tool"
    assert "check_cost_centre_funds" in divergence["explanation"]


def test_summary_reports_never_scored_as_inconclusive(run_script, fixtures):
    result = run_script(
        "skills/eval-analyze/scripts/summarize_results.py",
        fixtures / "eval_history/app_judge_suite_1730000100.evalset_result.json",
    )
    assert result.returncode == 3
    assert "Never scored" in result.stdout
    assert "not a regression" in result.stdout


def test_summary_names_failed_rubrics(run_script, fixtures):
    result = run_script(
        "skills/eval-analyze/scripts/summarize_results.py",
        fixtures / "eval_history/app_reliability_1730001000.evalset_result.json",
    )
    assert "names_the_failed_constraint" in result.stdout


def test_pass_at_k_separates_the_flaky_case(run_script, fixtures):
    result = run_script(
        "skills/eval-analyze/scripts/pass_at_k.py",
        "--dir", fixtures / "eval_history", "--evalset", "reliability", "--json",
    )
    payload = json.loads(result.stdout)
    by_id = {c["eval_id"]: c for c in payload["cases"]}
    assert by_id["edge_frozen_cost_centre_no_submit"]["tag"] == "UNRELIABLE"
    assert by_id["edge_frozen_cost_centre_no_submit"]["pass_at"]["1"] == 0.4
    assert by_id["happy_travel_within_policy"]["tag"] == "RELIABLE"
    assert payload["perfect_runs"] == 2
    assert result.returncode == 1


def test_pass_at_k_asks_which_eval_set_when_several_exist(run_script, fixtures):
    result = run_script("skills/eval-analyze/scripts/pass_at_k.py", "--dir", fixtures / "eval_history")
    assert result.returncode == 2
    assert "--evalset" in result.stderr


def test_calibration_sample_is_repeatable(run_script, fixtures):
    args = ("--dir", fixtures / "eval_history", "--n", "2", "--seed", "7", "--json")
    first = run_script("skills/eval-analyze/scripts/calibration_sample.py", *args)
    second = run_script("skills/eval-analyze/scripts/calibration_sample.py", *args)
    assert json.loads(first.stdout)["sampled"] == json.loads(second.stdout)["sampled"]


def test_calibration_only_samples_passing_cases(run_script, fixtures):
    result = run_script(
        "skills/eval-analyze/scripts/calibration_sample.py",
        "--dir", fixtures / "eval_history", "--n", "5", "--json",
    )
    payload = json.loads(result.stdout)
    assert payload["sampled"], "there are passing cases in the fixtures"
    assert all(entry["eval_id"] for entry in payload["sampled"])


def test_efficiency_metrics_get_their_own_table_and_no_verdict(run_script, fixtures):
    """ADK 2.10 reports four informational metrics on every run. They must not
    read as extra passing metrics, and the case must stay a clean pass."""
    path = fixtures / "eval_history/app_efficiency_1790000000.123456.evalset_result.json"
    result = run_script("skills/eval-analyze/scripts/summarize_results.py", path)
    assert result.returncode == 0, result.stderr
    assert "1 passed, 0 failed, 0 never scored" in result.stdout
    assert "Efficiency (informational, lower is better)" in result.stdout
    assert "| `token_usage_v1` | 312 | tokens |" in result.stdout
    assert "| `invocation_duration_v1` | 2.5 | seconds |" in result.stdout
    assert "cached_tokens 40" in result.stdout
    assert "Never scored" not in result.stdout


def test_efficiency_metrics_are_separated_in_the_json_too(run_script, fixtures):
    path = fixtures / "eval_history/app_efficiency_1790000000.123456.evalset_result.json"
    result = run_script("skills/eval-analyze/scripts/summarize_results.py", path, "--json")
    payload = json.loads(result.stdout)[0]
    case = payload["cases"][0]
    assert [m["name"] for m in case["metrics"]] == ["tool_trajectory_avg_score"]
    assert [m["name"] for m in case["efficiency"]] == [
        "tool_call_count_v1", "inference_call_count_v1",
        "token_usage_v1", "invocation_duration_v1",
    ]
    assert all(m["informational"] for m in case["efficiency"])
    assert case["unevaluated_metrics"] == [], "status 4 is not NOT_EVALUATED"
    assert case["inference_failed"] is False
    assert payload["run"]["efficiency"]["token_usage_v1"]["mean"] == 312.0


def test_calibration_ignores_informational_metrics(run_script, fixtures):
    result = run_script(
        "skills/eval-analyze/scripts/calibration_sample.py",
        "--dir", fixtures / "eval_history", "--n", "20", "--json",
    )
    payload = json.loads(result.stdout)
    names = [m["name"] for entry in payload["sampled"] for m in entry["metrics"]]
    assert names, "the sampled cases do carry gate metrics"
    assert "token_usage_v1" not in names


def test_a_run_that_never_scored_is_not_read_as_a_regression(run_script, fixtures):
    """Captured from a real `adk eval` run whose inference failed: the case is
    FAILED with an empty `overall_eval_metric_results`."""
    result = run_script(
        "skills/eval-analyze/scripts/summarize_results.py",
        fixtures / "eval_history/app_inference_failed_1730002000.evalset_result.json",
    )
    assert "failed before any metric could score it" in result.stdout
    assert "not a score regression" in result.stdout
    assert "check_env.py" in result.stdout, "the fix is the environment, not the agent"
    assert result.returncode == 1


def test_the_inference_failure_flag_is_in_the_json(run_script, fixtures):
    import json as _json

    result = run_script(
        "skills/eval-analyze/scripts/summarize_results.py",
        fixtures / "eval_history/app_inference_failed_1730002000.evalset_result.json", "--json",
    )
    case = _json.load(__import__("io").StringIO(result.stdout))[0]["cases"][0]
    assert case["inference_failed"] is True
    assert case["metrics"] == []
