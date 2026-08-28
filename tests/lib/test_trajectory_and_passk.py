"""Trajectory comparison, failure signatures, the quadrant, and pass@k."""

import pytest

from adk_skills.passk import aggregate, classify, pass_at_k, resolution_warning
from adk_skills.results import group_runs, find_result_files
from adk_skills.trajectory import (
    cascade_suspect, detect_signatures, diff, quadrant, tool_seq,
)

EXPECTED = [
    {"name": "lookup_policy", "args": {"req": {"category": "travel"}}},
    {"name": "check_cost_centre_funds", "args": {"req": {"cost_centre": "engineering"}}},
    {"name": "submit_claim", "args": {"req": {"claim_id": "CLM-000412"}}},
]


def test_identical_trajectories_match():
    assert diff(EXPECTED, EXPECTED).match is True


def test_a_skipped_validation_is_caught_at_the_point_it_happens():
    actual = [EXPECTED[0], EXPECTED[2]]
    result = diff(EXPECTED, actual)
    assert result.match is False
    assert result.divergence_index == 1
    assert result.divergence_kind == "wrong_tool"
    assert "check_cost_centre_funds" in result.explain()


def test_wrong_argument_with_the_right_tool_is_reported_as_an_argument_mismatch():
    actual = [{"name": "lookup_policy", "args": {"req": {"category": "meals"}}}, *EXPECTED[1:]]
    result = diff(EXPECTED, actual)
    assert result.divergence_kind == "arg_mismatch"
    assert result.arg_mismatches[0].differing_keys == ["req"]


def test_in_order_tolerates_an_extra_retry_between_expected_calls():
    actual = [EXPECTED[0], {"name": "lookup_policy", "args": {"req": {"category": "travel"}}}, *EXPECTED[1:]]
    assert diff(EXPECTED, actual, match_type="IN_ORDER").match is True
    assert diff(EXPECTED, actual, match_type="EXACT").match is False


def test_any_order_ignores_sequence_but_not_membership():
    shuffled = [EXPECTED[2], EXPECTED[0], EXPECTED[1]]
    assert diff(EXPECTED, shuffled, match_type="ANY_ORDER").match is True
    assert diff(EXPECTED, shuffled[:2], match_type="ANY_ORDER").match is False


def test_ignore_args_stops_argument_mistakes_from_failing():
    actual = [{"name": "lookup_policy", "args": {"req": {"category": "WRONG"}}}, *EXPECTED[1:]]
    assert diff(EXPECTED, actual).match is False
    assert diff(EXPECTED, actual, ignore_args=True).match is True


def test_numeric_arguments_compare_by_value():
    expected = [{"name": "t", "args": {"amount": 2499}}]
    assert diff(expected, [{"name": "t", "args": {"amount": 2499.0}}]).match is True
    assert diff(expected, [{"name": "t", "args": {"amount": "2499"}}]).match is False


def test_extra_trailing_call_is_reported():
    actual = [*EXPECTED, {"name": "submit_claim", "args": {"req": {"claim_id": "CLM-000412"}}}]
    result = diff(EXPECTED, actual)
    assert result.match is False and result.divergence_kind == "extra"


def test_signatures_match_each_trace_fixture(fixtures):
    from adk_skills.jsonl_trace import load_trace, model_texts, tool_calls

    known = {"lookup_policy", "check_cost_centre_funds", "submit_claim", "reject_claim"}
    expected = {
        "loop": "loop", "oscillation": "oscillation", "stall": "stall",
        "unknown_tool": "unknown_tool",
    }
    for name, kind in expected.items():
        events = load_trace(fixtures / "traces" / f"{name}.jsonl")
        signatures = detect_signatures(
            tool_calls(events), model_texts=model_texts(events), known_tools=known
        )
        assert kind in [s.kind for s in signatures], name


def test_the_happy_trace_produces_no_signature(fixtures):
    from adk_skills.jsonl_trace import load_trace, model_texts, tool_calls

    events = load_trace(fixtures / "traces/happy.jsonl")
    assert detect_signatures(
        tool_calls(events), model_texts=model_texts(events),
        known_tools={"lookup_policy", "check_cost_centre_funds", "submit_claim", "reject_claim"},
    ) == []


def test_each_signature_names_the_layer_to_fix():
    signatures = detect_signatures([{"name": "t", "args": {}}] * 4)
    assert signatures[0].layer in ("tool", "prompt", "model", "eval")
    assert signatures[0].fix


def test_cascade_is_reported_only_when_later_calls_build_on_the_mistake():
    early = [{"name": "lookup_policy", "args": {"req": {"category": "meals"}}}, *EXPECTED[1:]]
    assert cascade_suspect(diff(EXPECTED, early)) is not None
    late_only = [*EXPECTED[:2], {"name": "submit_claim", "args": {"req": {"claim_id": "WRONG"}}}]
    assert cascade_suspect(diff(EXPECTED, late_only)) is None


def test_the_dangerous_quadrant_is_a_good_answer_over_a_bad_trajectory():
    result = quadrant(0.5, 0.95)
    assert result["quadrant"] == "Q3"
    assert "skipped" in result["reading"] or "skipped" in result["action"]


def test_quadrant_needs_both_scores():
    assert quadrant(1.0, None)["quadrant"] is None


def test_tool_seq_flattens_names():
    assert tool_seq(EXPECTED) == ["lookup_policy", "check_cost_centre_funds", "submit_claim"]


@pytest.mark.parametrize("n,c,k,expected", [
    (5, 5, 1, 1.0), (5, 0, 1, 0.0), (5, 0, 5, 0.0),
    (5, 3, 1, 0.6), (5, 1, 5, 1.0), (1, 1, 1, 1.0),
])
def test_pass_at_k_values(n, c, k, expected):
    assert pass_at_k(n, c, k) == pytest.approx(expected)


def test_pass_at_k_never_decreases_with_k():
    values = [pass_at_k(10, 4, k) for k in range(1, 11)]
    assert values == sorted(values)


def test_classification_thresholds():
    assert classify(0.96) == "RELIABLE"
    assert classify(0.95) == "RELIABLE"
    assert classify(0.7) == "MARGINAL"
    assert classify(0.69) == "UNRELIABLE"


def test_run_count_resolution_is_explained():
    assert "cannot measure" in resolution_warning(1)
    assert resolution_warning(5)
    assert resolution_warning(50) is None


def test_five_run_series_separates_the_flaky_case(fixtures):
    runs = group_runs(find_result_files(fixtures / "eval_history"), eval_set_id="reliability")
    report = aggregate(runs)
    assert report.runs == 5
    by_id = {case.eval_id: case for case in report.cases}
    assert by_id["happy_travel_within_policy"].tag == "RELIABLE"
    flaky = by_id["edge_frozen_cost_centre_no_submit"]
    assert flaky.tag == "UNRELIABLE"
    assert flaky.passes == 2 and flaky.flaky is True
    assert flaky.fragility > 0.5, "retrying hides this failure, which is the point of the number"
    assert report.perfect_runs == 2
    assert "| case |" in report.to_md()
