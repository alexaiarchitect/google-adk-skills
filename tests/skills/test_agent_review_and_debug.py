"""agent-review and agent-debug scripts."""

import json


def test_a_clean_agent_reviews_clean(run_script, agent_dir):
    result = run_script(
        "skills/agent-review/scripts/lint_agent.py", agent_dir("recipes_good"), "--no-backend"
    )
    assert result.returncode == 0
    assert "0 error(s)" in result.stdout


def test_the_drift_agent_reports_every_class_of_problem(run_script, agent_dir):
    result = run_script(
        "skills/agent-review/scripts/lint_agent.py", agent_dir("flat_drift"),
        "--no-backend", "--json",
    )
    payload = json.loads(result.stdout)
    found = {f["id"] for f in payload["findings"]}
    assert "AGT003" in found, "generate_content_config carrying what ADK owns"
    assert "AGT005" in found, "a deprecated model id"
    assert "AGT011" in found, "a reserved agent name"
    assert "AGT013" in found, "a state write that never persists"
    assert "AGT016" in found, "a sampling kwarg on the constructor"
    assert "AGT001" in found, "a deprecated workflow shell"
    assert "AGT044" in found, "a tool that raises"
    assert result.returncode == 1


def test_construction_breakers_are_errors_and_deprecations_are_warnings(run_script, agent_dir):
    result = run_script(
        "skills/agent-review/scripts/lint_agent.py", agent_dir("flat_drift"),
        "--no-backend", "--json",
    )
    severity = {f["id"]: f["severity"] for f in json.loads(result.stdout)["findings"]}
    assert severity["AGT003"] == "error"
    assert severity["AGT016"] == "error"
    assert severity["AGT001"] == "warn"


def test_review_reports_what_it_found_about_the_agent(run_script, agent_dir):
    result = run_script(
        "skills/agent-review/scripts/lint_agent.py", agent_dir("recipes_good"),
        "--no-backend", "--json",
    )
    discovered = json.loads(result.stdout)["discovered"]
    assert discovered["export"] == "app"
    assert discovered["model_policy"] == "alias"
    assert discovered["model_from_env"] is True
    assert discovered["branches"] >= 5
    assert discovered["app_exported"] is True


def test_review_flags_an_app_name_that_disagrees_with_its_directory(run_script, agent_dir):
    result = run_script(
        "skills/agent-review/scripts/lint_agent.py", agent_dir("shim"), "--no-backend", "--json"
    )
    assert "ENV013" in {f["id"] for f in json.loads(result.stdout)["findings"]}


def test_review_flags_a_yaml_config_agent_as_deprecated(run_script, agent_dir):
    result = run_script(
        "skills/agent-review/scripts/lint_agent.py", agent_dir("yaml_agent"), "--no-backend"
    )
    assert "AGT050" in result.stdout
    assert result.returncode == 1


def test_review_notices_an_agent_with_no_evalset(run_script, agent_dir):
    result = run_script(
        "skills/agent-review/scripts/lint_agent.py", agent_dir("flat_good"),
        "--no-backend", "--json",
    )
    assert "GAP-EVALSET" in {g["id"] for g in json.loads(result.stdout)["gaps"]}


def test_strict_turns_warnings_into_a_failure(run_script, agent_dir):
    args = (agent_dir("flat_good"), "--no-backend")
    assert run_script("skills/agent-review/scripts/lint_agent.py", *args).returncode == 0
    strict = run_script("skills/agent-review/scripts/lint_agent.py", *args, "--strict")
    assert strict.returncode in (0, 1)


def test_review_points_at_a_single_agent_when_given_several(run_script, fixtures):
    result = run_script("skills/agent-review/scripts/lint_agent.py", fixtures / "agents")
    assert result.returncode == 2
    assert "pick one" in result.stderr


def test_load_check_goes_through_the_adk_loader_for_every_layout(run_script, agent_dir, fixtures, monkeypatch):
    monkeypatch.setenv("ADK_PYTHON", str(fixtures / "bin" / "fake_adk.py"))
    expectations = {
        "recipes_good": ("app", "claims_agent"),
        "flat_good": ("root_agent", "claims_agent"),
        "factory_app": ("app", None),
        "yaml_agent": ("root_agent.yaml", "legacy_claims_agent"),
    }
    for name, (export, agent_name) in expectations.items():
        result = run_script("skills/agent-review/scripts/load_agent.py", agent_dir(name), "--json")
        assert result.returncode == 0, (name, result.stderr)
        payload = result.json()
        assert payload["loaded"] is True, name
        assert payload["export"] == export, name
        if agent_name:
            assert payload["agent_name"] == agent_name, name


def test_load_check_reports_a_construction_error_with_a_hint(run_script, agent_dir, fixtures, monkeypatch):
    monkeypatch.setenv("ADK_PYTHON", str(fixtures / "bin" / "fake_adk.py"))
    monkeypatch.setenv("FAKE_ADK_LOAD_ERROR", "1 validation error for LlmAgent: name reserved")
    result = run_script("skills/agent-review/scripts/load_agent.py", agent_dir("flat_good"))
    assert result.returncode == 1
    assert "ValidationError" in result.stdout
    assert "reserved name" in result.stdout


def test_load_check_explains_a_missing_runner(run_script, agent_dir, monkeypatch):
    monkeypatch.setenv("ADK_PYTHON", "/nonexistent/python")
    result = run_script("skills/agent-review/scripts/load_agent.py", agent_dir("recipes_good"))
    assert result.returncode == 2
    assert "google-adk[eval]" in result.stderr


# -- agent-debug -----------------------------------------------------------

def test_each_trace_fixture_is_named_and_assigned_a_layer(run_script, fixtures, agent_dir):
    expected = {
        "loop": ("loop", "tool"),
        "oscillation": ("oscillation", "prompt"),
        "stall": ("stall", "prompt"),
        "unknown_tool": ("unknown_tool", "model"),
    }
    for name, (kind, layer) in expected.items():
        result = run_script(
            "skills/agent-debug/scripts/analyze_trace.py",
            "--trace", fixtures / "traces" / f"{name}.jsonl",
            "--agent-dir", agent_dir("flat_good"), "--json",
        )
        signatures = json.loads(result.stdout)["entries"][0]["signatures"]
        match = [s for s in signatures if s["kind"] == kind]
        assert match, f"{name}: expected a {kind} signature, got {[s['kind'] for s in signatures]}"
        assert match[0]["layer"] == layer
        assert match[0]["fix"]
        assert result.returncode == 1


def test_the_happy_trace_produces_no_false_positive(run_script, fixtures, agent_dir):
    result = run_script(
        "skills/agent-debug/scripts/analyze_trace.py",
        "--trace", fixtures / "traces/happy.jsonl",
        "--agent-dir", agent_dir("flat_good"), "--json",
    )
    assert json.loads(result.stdout)["entries"][0]["signatures"] == []
    assert result.returncode == 0


def test_tool_errors_are_surfaced_because_they_do_not_raise(run_script, fixtures, agent_dir):
    result = run_script(
        "skills/agent-debug/scripts/analyze_trace.py",
        "--trace", fixtures / "traces/loop.jsonl",
        "--agent-dir", agent_dir("flat_good"),
    )
    assert "tool error from `check_cost_centre_funds`" in result.stdout
    assert "returned as a result, not raised" in result.stdout


def test_analysis_from_an_eval_result_finds_the_divergence(run_script, fixtures, agent_dir):
    result = run_script(
        "skills/agent-debug/scripts/analyze_trace.py",
        "--result", fixtures / "eval_history/app_expense_claims_1730000000.evalset_result.json",
        "--agent-dir", agent_dir("flat_good"), "--json",
    )
    entries = json.loads(result.stdout)["entries"]
    failing = [e for e in entries if e["eval_id"] == "happy_travel_within_policy"][0]
    assert failing["divergences"][0]["divergence_kind"] == "wrong_tool"
    assert failing["status"] == "FAILED"


def test_a_never_scored_case_is_attributed_to_the_evaluation(run_script, fixtures):
    result = run_script(
        "skills/agent-debug/scripts/analyze_trace.py",
        "--result", fixtures / "eval_history/app_judge_suite_1730000100.evalset_result.json",
        "--json",
    )
    signatures = json.loads(result.stdout)["entries"][0]["signatures"]
    not_evaluated = [s for s in signatures if s["kind"] == "not_evaluated"]
    assert not_evaluated and not_evaluated[0]["layer"] == "eval"


def test_an_empty_trace_explains_the_jsonl_flag(run_script, tmp_path):
    empty = tmp_path / "t.jsonl"
    empty.write_text("")
    result = run_script("skills/agent-debug/scripts/analyze_trace.py", "--trace", empty)
    assert result.returncode == 2 and "--jsonl" in result.stderr


def test_one_of_trace_or_result_is_required(run_script):
    result = run_script("skills/agent-debug/scripts/analyze_trace.py")
    assert result.returncode == 2 and "--trace" in result.stderr
