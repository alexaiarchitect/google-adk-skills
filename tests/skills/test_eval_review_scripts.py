"""eval-review scripts, run as a skill runs them."""

import json


def test_a_clean_suite_passes(run_script, agent_dir):
    result = run_script("skills/eval-review/scripts/validate_evalset.py", agent_dir("recipes_good"))
    assert result.returncode == 0
    assert "0 error(s)" in result.stdout


def test_planted_problems_are_all_reported(run_script, fixtures, agent_dir):
    result = run_script(
        "skills/eval-review/scripts/validate_evalset.py",
        fixtures / "evalsets/broken.evalset.json",
        "--agent-dir", agent_dir("flat_good"), "--json",
    )
    found = {f["id"] for f in json.loads(result.stdout)["findings"]}
    assert {"EVS003", "EVS012", "EVS014", "EVS036"} <= found
    assert result.returncode == 1


def test_strict_makes_warnings_fail(run_script, fixtures, agent_dir):
    args = (fixtures / "evalsets/legacy.test.json", "--agent-dir", agent_dir("flat_good"))
    assert run_script("skills/eval-review/scripts/validate_evalset.py", *args).returncode == 0
    strict = run_script("skills/eval-review/scripts/validate_evalset.py", *args, "--strict")
    assert strict.returncode == 1


def test_a_trajectory_first_config_does_not_demand_reference_answers(run_script, agent_dir):
    result = run_script(
        "skills/eval-review/scripts/validate_evalset.py", agent_dir("recipes_good"), "--json"
    )
    payload = json.loads(result.stdout)
    assert payload["discovered"]["reference_metrics_configured"] is False
    assert not [f for f in payload["findings"] if f["id"] == "EVS025"]


def test_missing_target_exits_two(run_script, tmp_path):
    result = run_script("skills/eval-review/scripts/validate_evalset.py", tmp_path / "nope")
    assert result.returncode == 2


def test_crosscheck_catches_schema_violations_in_expected_calls(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    evalset = agent / "tests/eval/evalsets/happy.evalset.json"
    doc = json.loads(evalset.read_text())
    calls = doc["eval_cases"][0]["conversation"][0]["intermediate_data"]["tool_uses"]
    calls[0]["args"]["req"]["category"] = "stationery"        # not in the Literal
    calls[2]["args"]["req"]["claim_id"] = "CLM-42"            # fails the pattern
    calls[2]["args"]["req"]["amount_usd"] = -5                # breaks ge=0
    calls.append({"name": "notify_finance", "args": {}})      # no such tool
    evalset.write_text(json.dumps(doc, indent=2))

    result = run_script(
        "skills/eval-review/scripts/crosscheck_tools.py", evalset,
        "--agent-dir", agent / "app", "--json",
    )
    found = {f["id"] for f in json.loads(result.stdout)["findings"]}
    assert "EVS044" in found, "value outside a Literal"
    assert "EVS045" in found, "value failing a regex pattern"
    assert "EVS046" in found, "value breaking a numeric bound"
    assert "EVS036" in found, "call to a tool that does not exist"
    assert result.returncode == 1


def test_crosscheck_requires_nested_args_for_model_parameters(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    evalset = agent / "tests/eval/evalsets/happy.evalset.json"
    doc = json.loads(evalset.read_text())
    calls = doc["eval_cases"][0]["conversation"][0]["intermediate_data"]["tool_uses"]
    calls[0]["args"] = {"category": "travel"}                 # flat, should be {"req": {...}}
    evalset.write_text(json.dumps(doc, indent=2))

    result = run_script(
        "skills/eval-review/scripts/crosscheck_tools.py", evalset,
        "--agent-dir", agent / "app", "--json",
    )
    assert "EVS039" in {f["id"] for f in json.loads(result.stdout)["findings"]}


def test_crosscheck_passes_a_correct_suite(run_script, agent_dir):
    result = run_script(
        "skills/eval-review/scripts/crosscheck_tools.py",
        agent_dir("recipes_good") / "tests/eval/evalsets",
        "--agent-dir", agent_dir("recipes_good") / "app",
    )
    assert result.returncode == 0


def test_coverage_reports_branches_shape_and_uncovered_rules(run_script, agent_dir):
    result = run_script(
        "skills/eval-review/scripts/coverage_matrix.py", agent_dir("recipes_good"), "--json"
    )
    payload = json.loads(result.stdout)
    assert payload["discovered"]["branches"] >= 5
    assert payload["discovered"]["minimum_cases"] == payload["discovered"]["branches"] + 1
    assert payload["n_plus_one"]["shortfall"] > 0
    gap_ids = {g["id"] for g in payload["gaps"]}
    assert "GAP-BRANCH" in gap_ids
    assert any(g.startswith("GAP-RULE-") for g in gap_ids)
    assert result.returncode == 0, "gaps are advice; only --strict turns them into a failure"


def test_coverage_gaps_fail_only_under_strict(run_script, agent_dir):
    strict = run_script(
        "skills/eval-review/scripts/coverage_matrix.py", agent_dir("recipes_good"), "--strict"
    )
    assert strict.returncode == 1


def test_coverage_markdown_includes_the_rule_table(run_script, agent_dir):
    result = run_script("skills/eval-review/scripts/coverage_matrix.py", agent_dir("recipes_good"))
    assert "## Rule coverage" in result.stdout
    assert "| # | kind | rule | covered by |" in result.stdout


def test_a_single_evalset_file_is_validated_against_its_own_project(run_script, agent_dir):
    """Given only the file, the validator still finds the agent and its config
    three levels up, so a trajectory-only case is not reported as missing a
    reference response."""
    evalset = agent_dir("recipes_good") / "tests/eval/evalsets/happy.evalset.json"
    result = run_script("skills/eval-review/scripts/validate_evalset.py", evalset, "--json")
    payload = result.json()
    assert payload["summary"]["errors"] == 0, payload
    assert "known_tools" in json.dumps(payload) and "lookup_policy" in json.dumps(payload)


def test_restraint_cases_under_in_order_matching_are_flagged(run_script, tmp_agent):
    """IN_ORDER lets an agent submit and then reject and still pass a refusal
    case, so the validator says so; EXACT is quiet."""
    agent = tmp_agent("recipes_good")
    config = agent / "tests/eval/eval_config.json"
    config.write_text(config.read_text().replace('"EXACT"', '"IN_ORDER"'))
    result = run_script("skills/eval-review/scripts/validate_evalset.py", agent, "--json")
    payload = result.json()
    ids = [f["id"] for f in payload["findings"]]
    assert "EVS062" in ids
    hit = next(f for f in payload["findings"] if f["id"] == "EVS062")
    assert "edge_frozen_cost_centre_no_submit" in hit["message"]
    assert payload["discovered"]["trajectory_match_type"] == "IN_ORDER"

    config.write_text(config.read_text().replace('"IN_ORDER"', '"EXACT"'))
    clean = run_script("skills/eval-review/scripts/validate_evalset.py", agent, "--json")
    assert "EVS062" not in [f["id"] for f in clean.json()["findings"]]
