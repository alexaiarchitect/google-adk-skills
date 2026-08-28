"""The scripts added in round 2: suite management, pytest gate generation,
conformance wrapping, scenario generation, tool rendering, workflow scaffolds,
inventory, registry init, and the custom-metric scaffold.

Every ADK-backed path is exercised with --dry-run or against the fake runner, so
nothing here needs a network or credentials.
"""

from __future__ import annotations

import ast
import json
import os


def payload_of(result):
    return json.loads(result.stdout)


# -- manage_cases.py -------------------------------------------------------

def test_list_reports_every_case_by_category(run_script, agent_dir):
    result = run_script(
        "skills/eval-create/scripts/manage_cases.py", "list", agent_dir("recipes_good"),
        "--by-category", "--json",
    )
    assert result.returncode == 0
    text = result.stdout
    assert "happy_travel_within_policy" in text
    assert "edge_frozen_cost_centre_no_submit" in text


def test_rename_moves_the_id_and_refuses_a_clash(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    evalset = agent / "tests/eval/evalsets/happy.evalset.json"
    result = run_script(
        "skills/eval-create/scripts/manage_cases.py", "rename",
        "happy_travel_within_policy", "happy_travel_inside_policy", agent, "--evalset", evalset,
    )
    assert result.returncode == 0, result.stderr
    doc = json.loads(evalset.read_text())
    assert [c["eval_id"] for c in doc["eval_cases"]] == ["happy_travel_inside_policy"]

    edge = agent / "tests/eval/evalsets/edge.evalset.json"
    clash = run_script(
        "skills/eval-create/scripts/manage_cases.py", "rename",
        "edge_frozen_cost_centre_no_submit", "edge_restricted_category_no_funds_check",
        agent, "--evalset", edge,
    )
    assert clash.returncode == 1, "an existing eval_id is never silently replaced"


def test_remove_and_split_go_through_a_dry_run_first(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    edge = agent / "tests/eval/evalsets/edge.evalset.json"
    before = edge.read_text()
    dry = run_script(
        "skills/eval-create/scripts/manage_cases.py", "remove",
        "edge_frozen_cost_centre_no_submit", agent, "--evalset", edge, "--dry-run",
    )
    assert dry.returncode == 0
    assert edge.read_text() == before, "--dry-run must not write"

    real = run_script(
        "skills/eval-create/scripts/manage_cases.py", "remove",
        "edge_frozen_cost_centre_no_submit", agent, "--evalset", edge,
    )
    assert real.returncode == 0
    ids = [c["eval_id"] for c in json.loads(edge.read_text())["eval_cases"]]
    assert "edge_frozen_cost_centre_no_submit" not in ids


def test_split_turns_a_multi_turn_case_into_one_per_turn(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    spec = json.dumps([{
        "eval_id": "memory_two_turns",
        "turns": [
            {"user": "I work in engineering.", "tools": []},
            {"user": "What is the travel limit?",
             "tools": [{"name": "lookup_policy", "args": {"req": {"category": "travel"}}}]},
        ],
    }])
    run_script("skills/eval-create/scripts/gen_cases.py", agent, "--spec", spec, "--new", "memory")
    evalset = agent / "tests/eval/evalsets/memory.evalset.json"
    result = run_script(
        "skills/eval-create/scripts/manage_cases.py", "split", "memory_two_turns",
        agent, "--evalset", evalset,
    )
    assert result.returncode == 0, result.stderr
    ids = [c["eval_id"] for c in json.loads(evalset.read_text())["eval_cases"]]
    assert any(i.startswith("memory_two_turns_turn_") for i in ids)


def test_dedupe_finds_identical_cases(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    happy = agent / "tests/eval/evalsets/happy.evalset.json"
    doc = json.loads(happy.read_text())
    clone = json.loads(json.dumps(doc["eval_cases"][0]))
    clone["eval_id"] = "happy_travel_within_policy_copy"
    doc["eval_cases"].append(clone)
    happy.write_text(json.dumps(doc, indent=2))
    result = run_script(
        "skills/eval-create/scripts/manage_cases.py", "dedupe", agent, "--evalset", happy, "--json"
    )
    assert result.returncode == 1, "duplicates found is a finding"
    assert "happy_travel_within_policy_copy" in result.stdout


def test_rebase_app_name_rewrites_every_session_input(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    happy = agent / "tests/eval/evalsets/happy.evalset.json"
    result = run_script(
        "skills/eval-create/scripts/manage_cases.py", "rebase-app-name", "claims",
        agent, "--evalset", happy,
    )
    assert result.returncode == 0, result.stderr
    doc = json.loads(happy.read_text())
    assert all(c["session_input"]["app_name"] == "claims" for c in doc["eval_cases"])


def test_a_legacy_file_is_refused_by_the_manager(run_script, fixtures, tmp_path):
    import shutil

    legacy = tmp_path / "legacy.test.json"
    shutil.copy(fixtures / "evalsets/legacy.test.json", legacy)
    listed = run_script(
        "skills/eval-create/scripts/manage_cases.py", "list", tmp_path, "--evalset", legacy
    )
    assert listed.returncode == 0, "listing is read-only, so a legacy file is a note"
    assert "migrate_legacy" in listed.stdout

    before = legacy.read_text()
    renamed = run_script(
        "skills/eval-create/scripts/manage_cases.py", "rename", "a", "b", tmp_path, "--evalset", legacy
    )
    assert renamed.returncode == 2, "a legacy file is never rewritten in place"
    assert "migrate_legacy" in renamed.stderr + renamed.stdout
    assert legacy.read_text() == before


# -- gen_test_eval.py ------------------------------------------------------

def test_gen_test_eval_writes_a_gate_that_passes_the_config_explicitly(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    target = agent / "tests/eval/test_eval.py"
    if target.exists():
        target.unlink()
    result = run_script("skills/eval-run/scripts/gen_test_eval.py", agent, "--json")
    assert result.returncode == 0, result.stderr
    source = target.read_text()
    assert "evaluate_eval_set" in source
    assert "eval_config" in source
    assert "ADK_EVAL_NUM_RUNS" in source
    ast.parse(source)


def test_gen_test_eval_can_target_a_sub_agent(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    target = agent / "tests/eval/test_eval.py"
    if target.exists():
        target.unlink()
    run_script("skills/eval-run/scripts/gen_test_eval.py", agent, "--agent-name", "claims_agent")
    assert "claims_agent" in target.read_text()


def test_gen_test_eval_refuses_to_overwrite(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    target = agent / "tests/eval/test_eval.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# mine\n")
    result = run_script("skills/eval-run/scripts/gen_test_eval.py", agent)
    assert target.read_text() == "# mine\n"
    assert result.returncode == 1


# -- conformance.py --------------------------------------------------------

def test_conformance_spec_shape_is_checked(run_script, fixtures, tmp_path):
    case = tmp_path / "tests" / "conformance" / "travel"
    case.mkdir(parents=True)
    (case / "spec.yaml").write_text(
        'description: bad shape\nagent: app\nuser_messages:\n  - "a bare string"\n'
    )
    result = run_script(
        "skills/eval-run/scripts/conformance.py", "record", case, "--dry-run", "--no-port-check"
    )
    assert result.returncode == 1
    assert "CNF013" in result.stdout
    assert "- text:" in result.stdout, "the fix names the object form"


def test_conformance_record_dry_run_shows_the_positional_mode(run_script, fixtures, tmp_path):
    import shutil

    case = tmp_path / "tests" / "conformance" / "travel"
    case.mkdir(parents=True)
    shutil.copy(fixtures / "conformance/spec.yaml", case / "spec.yaml")
    result = run_script(
        "skills/eval-run/scripts/conformance.py", "record", case,
        "--dry-run", "--no-port-check", "--json",
    )
    assert result.returncode == 0, result.stderr
    assert "conformance" in result.stdout and "record" in result.stdout


def test_conformance_record_explains_the_missing_dev_server(run_script, fixtures, tmp_path):
    import shutil

    case = tmp_path / "tests" / "conformance" / "travel"
    case.mkdir(parents=True)
    shutil.copy(fixtures / "conformance/spec.yaml", case / "spec.yaml")
    result = run_script(
        "skills/eval-run/scripts/conformance.py", "record", case, "--port", "65432"
    )
    assert result.returncode == 2
    assert "adk web" in result.stdout + result.stderr


# -- generate_eval_cases.py -------------------------------------------------

def test_generation_config_is_validated_before_anything_is_spent(run_script, agent_dir, tmp_path):
    config = tmp_path / "gen.json"
    config.write_text(json.dumps({"count": 3, "model_name": "gemini-flash-latest",
                                  "generation_instructions": "typo'd key"}))
    result = run_script(
        "skills/eval-simulation/scripts/generate_eval_cases.py", agent_dir("recipes_good"),
        "generated", "--config", config, "--dry-run",
    )
    assert result.returncode in (1, 2)
    assert "generation_instructions" in result.stdout + result.stderr


def test_generation_dry_run_prints_the_command_and_the_cost(run_script, agent_dir, tmp_path):
    config = tmp_path / "gen.json"
    config.write_text(json.dumps({"count": 3, "model_name": "gemini-flash-latest",
                                  "generation_instruction": "cover refusals"}))
    result = run_script(
        "skills/eval-simulation/scripts/generate_eval_cases.py", agent_dir("recipes_good"),
        "generated", "--config", config, "--dry-run",
    )
    assert "generate_eval_cases" in result.stdout + result.stderr


# -- render_tool.py / scaffold --workflow -----------------------------------

def test_render_tool_adds_a_contracted_tool_to_an_existing_agent(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    result = run_script(
        "skills/agent-create/scripts/render_tool.py", agent / "app",
        "--tool", "escalate_claim:claim_id=pattern(^CLM-[0-9]{6}$),reason=str", "--json",
    )
    assert result.returncode == 0, result.stderr
    source = (agent / "app/agent.py").read_text()
    assert "def escalate_claim(" in source
    assert "class EscalateClaimRequest" in source
    assert "escalate_claim" in source.split("tools=", 1)[1], "the new tool is bound"
    ast.parse(source)
    plan = payload_of(result)
    assert plan.get("write_plan") or plan.get("plan") or "agent.py" in json.dumps(plan)


def test_render_tool_refuses_an_existing_name(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    result = run_script(
        "skills/agent-create/scripts/render_tool.py", agent / "app",
        "--tool", "submit_claim:claim_id=str",
    )
    assert result.returncode == 1


def test_workflow_scaffold_produces_a_graph_that_parses_and_binds_an_app(run_script, tmp_path):
    target = tmp_path / "graph"
    result = run_script(
        "skills/agent-create/scripts/scaffold_agent.py", "--name", "claims_flow",
        "--dir", target, "--workflow", "--tools", "lookup_policy:category=str", "--json",
    )
    assert result.returncode == 0, result.stderr
    sources = list(target.rglob("*.py"))
    assert sources
    joined = "\n".join(p.read_text() for p in sources)
    assert "Workflow(" in joined
    assert "App(" in joined
    for path in sources:
        ast.parse(path.read_text())


# -- inventory.py / run_registry --init -------------------------------------

def test_inventory_lists_every_fixture_agent_with_a_maturity_level(run_script, fixtures):
    result = run_script("skills/adk/scripts/inventory.py", fixtures / "agents", "--json")
    assert result.returncode == 0, result.stderr
    payload = payload_of(result)
    rows = payload.get("agents") or payload.get("rows") or payload
    text = json.dumps(rows)
    for name in ("flat_good", "recipes_good", "flat_drift"):
        assert name in text
    assert '"level"' in text or '"maturity"' in text


def test_registry_init_builds_a_manifest_by_discovery(run_script, tmp_agent, tmp_path):
    agent = tmp_agent("recipes_good")
    out = tmp_path / "eval_registry.json"
    result = run_script(
        "skills/eval-run/scripts/run_registry.py", "--init", "--root", agent, "--out", out, "--json"
    )
    assert result.returncode == 0, result.stderr
    manifest = json.loads(out.read_text())
    assert manifest["schema"] == "adk-skills.registry/1"
    assert manifest["suites"], "the recipes fixture has evalsets, so it becomes a suite"
    assert manifest["suites"][0]["tier"] == "pr", "a deterministic config gates pull requests"


# -- gen_custom_metric.py ---------------------------------------------------

def test_custom_metric_scaffold_reads_the_criterion_threshold(run_script, tmp_path):
    out = tmp_path / "claim_amount_accuracy.py"
    result = run_script(
        "skills/eval-criteria/scripts/gen_custom_metric.py",
        "--name", "claim_amount_accuracy", "--out", out, "--json",
    )
    assert result.returncode == 0, result.stderr
    source = out.read_text()
    assert "criterion.threshold" in source
    assert "PerInvocationResult" in source
    assert "EvaluationResult" in source
    ast.parse(source)


def test_custom_metric_scaffold_has_an_async_variant_and_refuses_overwrite(run_script, tmp_path):
    out = tmp_path / "m.py"
    first = run_script(
        "skills/eval-criteria/scripts/gen_custom_metric.py",
        "--name", "m", "--out", out, "--kind", "async",
    )
    assert first.returncode == 0
    assert "async def" in out.read_text()
    second = run_script(
        "skills/eval-criteria/scripts/gen_custom_metric.py", "--name", "m", "--out", out
    )
    assert second.returncode == 1


# -- run_jsonl.py ----------------------------------------------------------

def test_run_jsonl_dry_run_states_cost_and_writes_nothing(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    result = run_script(
        "skills/agent-debug/scripts/run_jsonl.py", agent, "Submit claim CLM-000412.", "--dry-run"
    )
    assert result.returncode == 0, result.stderr
    assert "--jsonl" in result.stdout
    assert not (agent / ".adk").exists()


# -- fixture sanity ---------------------------------------------------------

def test_the_trap_fixture_is_a_valid_project(run_script, agent_dir):
    result = run_script(
        "skills/eval-review/scripts/validate_evalset.py", agent_dir("flat_trap"), "--json"
    )
    assert payload_of(result)["summary"]["errors"] == 0
    review = run_script(
        "skills/agent-review/scripts/lint_agent.py", agent_dir("flat_trap"), "--no-backend", "--json"
    )
    assert payload_of(review)["summary"]["errors"] == 0
