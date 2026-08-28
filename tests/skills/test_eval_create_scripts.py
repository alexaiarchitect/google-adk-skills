"""eval-create scripts: authoring, capture, migration."""

import json


def test_new_cases_are_added_with_their_categories(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    spec = json.dumps([{
        "eval_id": "edge_over_limit_no_submit",
        "turns": [{
            "user": "Claim CLM-000600 for 5000 dollars of meals on sales.",
            "tools": [
                {"name": "lookup_policy", "args": {"req": {"category": "meals"}}},
                {"name": "reject_claim", "args": {"req": {"claim_id": "CLM-000600",
                                                          "reason": "over the meals limit"}}},
            ],
        }],
    }])
    result = run_script(
        "skills/eval-create/scripts/gen_cases.py", agent, "--spec", spec,
        "--evalset", agent / "tests/eval/evalsets/edge.evalset.json", "--json",
    )
    payload = json.loads(result.stdout)
    assert payload["summary"]["added"][0]["category"] == "edge"
    assert payload["summary"]["total_cases"] == 3
    assert payload["validation"]["summary"]["errors"] == 0


def test_an_existing_case_is_never_overwritten(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    evalset = agent / "tests/eval/evalsets/happy.evalset.json"
    before = evalset.read_text()
    result = run_script(
        "skills/eval-create/scripts/gen_cases.py", agent,
        "--spec", json.dumps([{"eval_id": "happy_travel_within_policy",
                               "turns": [{"user": "x", "tools": []}]}]),
        "--evalset", evalset,
    )
    assert result.returncode == 1
    assert "already exist" in result.stderr
    assert evalset.read_text() == before, "a golden trace is a specification"


def test_multi_turn_cases_are_built_from_several_turns(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    spec = json.dumps([{
        "eval_id": "memory_recalls_cost_centre",
        "turns": [
            {"user": "I work in engineering.", "tools": []},
            {"user": "Submit CLM-000901 for 900 dollars of travel.",
             "tools": [{"name": "check_cost_centre_funds",
                        "args": {"req": {"cost_centre": "engineering", "amount_usd": 900.0}}}]},
        ],
    }])
    result = run_script(
        "skills/eval-create/scripts/gen_cases.py", agent, "--spec", spec, "--new", "memory", "--json"
    )
    payload = json.loads(result.stdout)
    assert payload["summary"]["added"][0]["turns"] == 2
    assert payload["summary"]["added"][0]["category"] == "memory"


def test_a_new_evalset_lands_beside_the_existing_ones(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    result = run_script(
        "skills/eval-create/scripts/gen_cases.py", agent,
        "--spec", json.dumps([{"eval_id": "adversarial_urgency", "turns": [{"user": "now!", "tools": []}]}]),
        "--new", "adversarial", "--json",
    )
    path = json.loads(result.stdout)["summary"]["evalset"]
    assert path.endswith("tests/eval/evalsets/adversarial.evalset.json")


def test_a_flat_agent_gets_its_evalset_beside_the_agent(run_script, tmp_agent):
    """Where `adk eval` can auto-discover `test_config.json`."""
    agent = tmp_agent("flat_good")
    result = run_script(
        "skills/eval-create/scripts/gen_cases.py", agent,
        "--spec", json.dumps([{"eval_id": "happy_first", "turns": [{"user": "hi", "tools": []}]}]),
        "--new", "smoke", "--json",
    )
    path = json.loads(result.stdout)["summary"]["evalset"]
    assert path.endswith("flat_good/smoke.test.json")


def test_cases_must_declare_what_they_expect(run_script, agent_dir):
    result = run_script(
        "skills/eval-create/scripts/gen_cases.py", agent_dir("recipes_good"),
        "--spec", json.dumps([{"eval_id": "no_turns_here"}]), "--new", "x",
    )
    assert result.returncode == 2 and "turns" in result.stderr


def test_capture_turns_a_trace_into_a_case(run_script, tmp_agent, fixtures):
    agent = tmp_agent("recipes_good")
    out = agent / "tests/eval/evalsets/captured.evalset.json"
    result = run_script(
        "skills/eval-create/scripts/session_to_evalset.py",
        "--trace", fixtures / "traces/happy.jsonl",
        "--eval-id", "happy_captured_travel_claim",
        "--evalset", out, "--agent-dir", agent / "app",
        "--drop-final-response", "--json",
    )
    payload = json.loads(result.stdout)
    assert payload["captured"]["tool_calls"] == [
        "lookup_policy", "check_cost_centre_funds", "submit_claim"
    ]
    assert payload["captured"]["app_name"] == "app"
    assert payload["validation"]["summary"]["errors"] == 0
    assert out.exists()


def test_capture_refuses_to_replace_an_existing_case(run_script, tmp_agent, fixtures):
    agent = tmp_agent("recipes_good")
    result = run_script(
        "skills/eval-create/scripts/session_to_evalset.py",
        "--trace", fixtures / "traces/happy.jsonl",
        "--eval-id", "happy_travel_within_policy",
        "--evalset", agent / "tests/eval/evalsets/happy.evalset.json",
        "--agent-dir", agent / "app",
    )
    assert result.returncode == 2
    assert "already exists" in result.stderr


def test_capture_reports_an_empty_trace_rather_than_writing_nothing(run_script, tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    result = run_script(
        "skills/eval-create/scripts/session_to_evalset.py", "--trace", empty,
        "--eval-id", "x", "--evalset", tmp_path / "out.evalset.json",
    )
    assert result.returncode == 2
    assert "--jsonl" in result.stderr


def test_legacy_migration_keeps_every_expected_call(run_script, fixtures, tmp_path):
    import shutil

    source = tmp_path / "legacy.test.json"
    shutil.copy(fixtures / "evalsets/legacy.test.json", source)
    result = run_script(
        "skills/eval-create/scripts/migrate_legacy.py", source, "--app-name", "app", "--json"
    )
    payload = json.loads(result.stdout)
    assert payload["eval_set_id"] == "legacy"
    assert payload["stats"]["cases"] == 1
    assert payload["validation"]["summary"]["errors"] == 0
    migrated = json.loads((tmp_path / "legacy.evalset.json").read_text())
    tools = migrated["eval_cases"][0]["conversation"][0]["intermediate_data"]["tool_uses"]
    assert [t["name"] for t in tools] == ["lookup_policy", "check_cost_centre_funds"]
    assert tools[0]["args"] == {"req": {"category": "equipment"}}


def test_migrating_an_already_current_file_is_refused(run_script, agent_dir):
    result = run_script(
        "skills/eval-create/scripts/migrate_legacy.py",
        agent_dir("recipes_good") / "tests/eval/evalsets/happy.evalset.json",
    )
    assert result.returncode == 2 and "already in the EvalSet schema" in result.stderr
