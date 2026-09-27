"""The metric catalog and eval-config validation."""

from adk_skills import metrics as m
from adk_skills.env import detect_backend
from adk_skills.eval_config import load_config, validate_config
from adk_skills.report import GapReport


def ids(report: GapReport) -> set[str]:
    return {f.id for f in report.findings}


def test_catalog_covers_every_prebuilt_metric():
    # 13 registered in ADK 2.9.2, plus the four efficiency metrics added in 2.10.
    assert len(m.METRIC_CATALOG) == 17
    for name, spec in m.METRIC_CATALOG.items():
        assert spec.name == name
        assert spec.value_range[0] < spec.value_range[1]
        assert spec.ci_tier in ("pr", "nightly")


def test_the_efficiency_metrics_are_catalogued_as_informational_and_2_10_only():
    assert set(m.informational_metrics()) == {
        "tool_call_count_v1", "inference_call_count_v1",
        "token_usage_v1", "invocation_duration_v1",
    }
    for name in m.informational_metrics():
        spec = m.METRIC_CATALOG[name]
        assert spec.since == "2.10"
        assert spec.requires_threshold is False, "the evaluator rejects a threshold"
        assert spec.cost == "free" and spec.reference_based is False
        assert spec.requires_rubrics is False and spec.user_sim_compatible is True
        assert spec.value_range[1] == m.INFINITY, "no Interval, so no ceiling"


def test_informational_metrics_are_not_a_gate():
    """Deterministic, free and always on - but they never fail a case, so they
    are not something a suite can gate on."""
    assert not set(m.informational_metrics()) & set(m.DETERMINISTIC_METRICS)
    assert m.cost_tier(m.informational_metrics()) == "free"
    assert m.informational_metrics(["safety_v1", "token_usage_v1"]) == ["token_usage_v1"]


def test_only_deterministic_metrics_are_pr_tier():
    for name in m.METRIC_NAMES:
        spec = m.METRIC_CATALOG[name]
        assert (spec.ci_tier == "pr") == (not spec.needs_judge)


def test_reference_based_metrics_are_exactly_the_ones_user_simulation_excludes():
    assert set(m.REFERENCE_BASED) == {n for n in m.METRIC_NAMES
                                      if not m.METRIC_CATALOG[n].user_sim_compatible}


def test_vertex_only_metrics_are_named_correctly():
    assert set(m.VERTEX_METRICS) == {
        "response_evaluation_score", "safety_v1",
        "multi_turn_task_success_v1", "multi_turn_trajectory_quality_v1",
        "multi_turn_tool_use_quality_v1",
    }


def test_response_evaluation_score_is_the_only_one_to_five_metric():
    ranges = {
        n: s.value_range for n, s in m.METRIC_CATALOG.items()
        if s.value_range != (0.0, 1.0) and not s.informational
    }
    assert ranges == {"response_evaluation_score": (1.0, 5.0)}


def test_cost_tier_takes_the_most_expensive_metric():
    assert m.cost_tier(["tool_trajectory_avg_score"]) == "free"
    assert m.cost_tier(["tool_trajectory_avg_score", "hallucinations_v1"]) == "judge"
    assert m.cost_tier(["hallucinations_v1", "safety_v1"]) == "vertex"


def test_suggest_helps_with_a_near_miss():
    assert m.suggest("tool_trajectory_score") == "tool_trajectory_avg_score"
    assert m.suggest("completely_unrelated") is None


def test_adk_default_criteria_are_reference_based():
    assert m.DEFAULT_CRITERIA == {"tool_trajectory_avg_score": 1.0, "response_match_score": 0.8}
    assert all(m.METRIC_CATALOG[n].reference_based for n in m.DEFAULT_CRITERIA)


def test_clean_config_passes(fixtures):
    report = validate_config(load_config(fixtures / "eval_configs/pr_trajectory.json"))
    assert report.count("error") == 0


def test_rubric_config_passes_and_costs_judge_calls(fixtures):
    loaded = load_config(fixtures / "eval_configs/judge_rubrics.json")
    assert loaded.cost() == "judge"
    report = validate_config(loaded, known_tools={"submit_claim", "check_cost_centre_funds", "lookup_policy"})
    assert report.count("error") == 0


def test_bad_config_reports_each_planted_problem(fixtures):
    report = validate_config(load_config(fixtures / "eval_configs/bad_config.json"))
    found = ids(report)
    assert "CFG010" in found, "unknown metric name"
    assert "CFG014" in found, "0.5 is below response_evaluation_score's 1-5 range"
    assert "CFG016" in found, "criterion field ADK accepts and ignores"
    assert "CFG023" in found, "hallucinations without intermediate responses"
    assert "CFG028" in found, "deprecated judge model"
    assert "CFG033" in found, "num_samples that multiplies cost"
    assert "CFG051" in found, "custom metric never listed in criteria"
    assert "RUB003" in found, "rubric that asks for a 1-5 rating"


def test_unknown_metric_suggests_the_intended_one(fixtures):
    report = validate_config(load_config(fixtures / "eval_configs/bad_config.json"))
    message = [f.message for f in report.findings if f.id == "CFG010"][0]
    assert "tool_trajectory_avg_score" in message


def test_vertex_metric_without_a_project_is_an_error(fixtures, tmp_path):
    backend = detect_backend(tmp_path, include_os_environ=False)
    report = validate_config(load_config(fixtures / "eval_configs/vertex_safety.json"), backend=backend)
    assert "CFG021" in ids(report)


def test_vertex_metric_with_a_project_is_accepted(fixtures, tmp_path):
    (tmp_path / ".env").write_text(
        "GOOGLE_GENAI_USE_VERTEXAI=TRUE\nGOOGLE_CLOUD_PROJECT=p\nGOOGLE_CLOUD_LOCATION=us-central1\n"
    )
    backend = detect_backend(tmp_path, include_os_environ=False)
    report = validate_config(load_config(fixtures / "eval_configs/vertex_safety.json"), backend=backend)
    assert "CFG021" not in ids(report)


def test_yaml_config_is_rejected_because_adk_parses_json(tmp_path):
    config = tmp_path / "eval_config.yaml"
    config.write_text("metrics_to_run:\n  - custom_quality\n")
    report = validate_config(load_config(config))
    assert "CFG002" in ids(report)


def test_missing_judge_options_warn_about_the_library_defaults(tmp_path):
    config = tmp_path / "eval_config.json"
    config.write_text('{"criteria": {"final_response_match_v2": 0.8}}')
    report = validate_config(load_config(config))
    message = [f.message for f in report.findings if f.id == "CFG024"][0]
    assert m.JUDGE_DEFAULTS["judge_model"] in message


def test_user_simulation_with_a_reference_metric_is_an_error(tmp_path):
    config = tmp_path / "eval_config.json"
    config.write_text(
        '{"criteria": {"tool_trajectory_avg_score": 1.0},'
        ' "user_simulator_config": {"type": "llm_backed", "model": "gemini-flash-latest",'
        '  "max_allowed_invocations": 6}}'
    )
    report = validate_config(load_config(config))
    assert "CFG030" in ids(report)


def test_threshold_of_zero_is_reported_as_a_disabled_gate(tmp_path):
    config = tmp_path / "eval_config.json"
    config.write_text('{"criteria": {"response_match_score": 0.0}}')
    report = validate_config(load_config(config))
    assert "CFG015" in ids(report)


def test_ignore_args_is_flagged_because_wrong_arguments_stop_failing(tmp_path):
    config = tmp_path / "eval_config.json"
    config.write_text(
        '{"criteria": {"tool_trajectory_avg_score": {"threshold": 1.0, "ignore_args": true}}}'
    )
    assert "CFG018" in ids(validate_config(load_config(config)))


def test_bad_match_type_is_rejected_and_case_variants_are_not(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"criteria": {"tool_trajectory_avg_score": {"threshold": 1.0, "match_type": "SLOPPY"}}}')
    assert "CFG017" in ids(validate_config(load_config(bad)))
    good = tmp_path / "good.json"
    good.write_text('{"criteria": {"tool_trajectory_avg_score": {"threshold": 1.0, "match_type": "in-order"}}}')
    assert "CFG017" not in ids(validate_config(load_config(good)))


def test_an_efficiency_metric_in_criteria_is_an_error_on_both_releases(tmp_path):
    """Measured: `EvalConfig.model_validate` accepts the name on 2.9.2 and 2.10.0
    alike, and the run then raises - ValueError on 2.10 ("does not support a
    threshold"), NotFoundError on 2.9. So the config is wrong either way."""
    config = tmp_path / "eval_config.json"
    config.write_text(
        '{"criteria": {"tool_trajectory_avg_score": 1.0, "token_usage_v1": {"threshold": 100}}}'
    )
    for version in (None, "2.9.2", "2.10.0"):
        report = validate_config(load_config(config), adk_version=version)
        assert "CFG070" in ids(report), version
        finding = [f for f in report.findings if f.id == "CFG070"][0]
        assert finding.severity == "error"
        assert "remove it from `criteria`" in (finding.fix or "")
        # Not also reported as an out-of-range threshold: there is no range.
        assert "CFG014" not in ids(report)


def test_the_efficiency_note_is_written_once_and_only_for_2_10(tmp_path):
    config = tmp_path / "eval_config.json"
    config.write_text('{"criteria": {"tool_trajectory_avg_score": 1.0}}')

    on_2_10 = validate_config(load_config(config), adk_version="2.10.0")
    notes = [f for f in on_2_10.findings if f.id == "CFG071"]
    assert len(notes) == 1, "once per config, not once per metric"
    assert notes[0].severity == "info"
    assert "token_usage_v1" in notes[0].message

    assert "CFG071" not in ids(validate_config(load_config(config), adk_version="2.9.2"))
    # An unprobed runner gets the note: the plugin installs the version it targets.
    assert "CFG071" in ids(validate_config(load_config(config), adk_version=None))


def test_an_empty_criteria_map_gets_no_efficiency_note(tmp_path):
    config = tmp_path / "eval_config.json"
    config.write_text('{"criteria": {}}')
    found = ids(validate_config(load_config(config)))
    assert "CFG007" in found and "CFG071" not in found
