"""Result-file parsing and trace normalization."""

from adk_skills.jsonl_trace import (
    final_text, invocations, load_trace, to_eval_invocations, tool_calls, tool_errors,
)
from adk_skills.results import (
    find_result_files, latest_per_evalset, parse_result, parse_result_name,
    result_identity, status_name,
)


def test_status_names_accept_ints_enums_and_strings():
    assert status_name(1) == "PASSED"
    assert status_name(2) == "FAILED"
    assert status_name(3) == "NOT_EVALUATED"
    assert status_name("EvalStatus.PASSED") == "PASSED"
    assert status_name(None) == "UNKNOWN"


def test_result_file_name_needs_the_eval_set_id_to_split_safely():
    name = "my_app_expense_claims_1730000000.evalset_result.json"
    bare = parse_result_name(name)
    assert bare["timestamp"] == 1730000000.0
    assert "app_name" not in bare, "both ids can contain underscores"
    resolved = parse_result_name(name, eval_set_id="expense_claims")
    assert resolved["app_name"] == "my_app"


def test_identity_reads_the_eval_set_id_from_the_file(fixtures):
    path = fixtures / "eval_history/app_expense_claims_1730000000.evalset_result.json"
    identity = result_identity(path)
    assert identity["eval_set_id"] == "expense_claims"
    assert identity["app_name"] == "app"


def test_parsing_a_run_gives_per_case_and_per_metric_detail(fixtures):
    run = parse_result(fixtures / "eval_history/app_expense_claims_1730000000.evalset_result.json")
    assert run.ok and run.passed == 1 and run.failed == 1 and run.not_evaluated == 0
    case = run.case("happy_travel_within_policy")
    assert case.status == "FAILED"
    trajectory = case.metric("tool_trajectory_avg_score")
    assert trajectory.status == "FAILED" and trajectory.margin < 0
    assert case.metric("response_match_score").passed, "the answer read fine; the work did not"


def test_expected_and_actual_trajectories_survive_parsing(fixtures):
    run = parse_result(fixtures / "eval_history/app_expense_claims_1730000000.evalset_result.json")
    invocation = run.case("happy_travel_within_policy").invocations[0]
    assert [t["name"] for t in invocation.expected_tools] == [
        "lookup_policy", "check_cost_centre_funds", "submit_claim"
    ]
    assert [t["name"] for t in invocation.actual_tools] == ["lookup_policy", "submit_claim"]


def test_not_evaluated_is_kept_apart_from_failure(fixtures):
    run = parse_result(fixtures / "eval_history/app_judge_suite_1730000100.evalset_result.json")
    assert run.not_evaluated == 1 and run.failed == 0
    assert run.is_clean is False


def test_rubric_scores_and_their_failures_are_available(fixtures):
    files = [p for p in find_result_files(fixtures / "eval_history") if "reliability_1730001000" in p]
    run = parse_result(files[0])
    metric = run.case("edge_frozen_cost_centre_no_submit").metric(
        "rubric_based_final_response_quality_v1"
    )
    assert metric.failed_rubrics() == ["names_the_failed_constraint"]


def test_latest_per_evalset_picks_the_newest_of_each(fixtures):
    latest = latest_per_evalset(find_result_files(fixtures / "eval_history"))
    assert {"expense_claims", "judge_suite", "reliability"} <= set(latest)
    assert latest["reliability"].endswith("1730001004.evalset_result.json")


def test_a_corrupt_result_file_is_reported_not_raised(tmp_path):
    bad = tmp_path / "a_b_1.evalset_result.json"
    bad.write_text("{oops")
    run = parse_result(bad)
    assert run.ok is False and "invalid JSON" in run.error


def test_jsonl_trace_yields_calls_final_text_and_errors(fixtures):
    events = load_trace(fixtures / "traces/happy.jsonl")
    assert [c["name"] for c in tool_calls(events)] == [
        "lookup_policy", "check_cost_centre_funds", "submit_claim"
    ]
    assert final_text(events).startswith("Submitted CLM-000412")
    assert tool_errors(events) == []


def test_a_tool_error_is_visible_even_though_the_run_continued(fixtures):
    errors = tool_errors(load_trace(fixtures / "traces/loop.jsonl"))
    assert errors and errors[0]["tool"] == "check_cost_centre_funds"


def test_a_trace_converts_into_eval_case_invocations(fixtures):
    events = load_trace(fixtures / "traces/happy.jsonl")
    converted = to_eval_invocations(events)
    assert len(converted) == 1
    invocation = converted[0]
    assert invocation["user_content"]["role"] == "user"
    assert invocation["final_response"]["parts"][0]["text"].startswith("Submitted")
    assert [t["name"] for t in invocation["intermediate_data"]["tool_uses"]] == [
        "lookup_policy", "check_cost_centre_funds", "submit_claim"
    ]
    assert invocation["intermediate_data"]["tool_uses"][0]["args"] == {"req": {"category": "travel"}}


def test_invocations_group_by_turn(fixtures):
    assert len(invocations(load_trace(fixtures / "traces/oscillation.jsonl"))) == 1


def test_log_noise_between_json_lines_is_skipped(tmp_path):
    trace = tmp_path / "t.jsonl"
    trace.write_text(
        'WARNING: something\n'
        '{"author": "user", "content": {"role": "user", "parts": [{"text": "hi"}]}}\n'
        'not json at all\n'
    )
    assert len(load_trace(trace)) == 1
