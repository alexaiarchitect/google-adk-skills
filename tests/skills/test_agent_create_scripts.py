"""agent-create: the scaffold, and whether what it produces survives review."""

import json


def scaffold(run_script, target, **extra):
    args = [
        "--name", extra.get("name", "claims_agent"),
        "--dir", str(target),
        "--layout", extra.get("layout", "recipes"),
        "--description", "an agent that approves or rejects expense claims.",
        "--tools",
        "lookup_policy:category=literal(travel|meals);"
        "check_cost_centre_funds:cost_centre=literal(engineering|sales),amount_usd=float;"
        "submit_claim:claim_id=pattern(^CLM-[0-9]{6}$),amount_usd=float",
        "--json",
    ]
    return run_script("skills/agent-create/scripts/scaffold_agent.py", *args)


def test_recipes_scaffold_writes_a_complete_project(run_script, tmp_path):
    target = tmp_path / "claims"
    payload = json.loads(scaffold(run_script, target).stdout)
    written = {p.split(str(target) + "/")[-1] for p in payload["written"]}
    assert {"app/agent.py", "app/tools.py", "app/schemas.py", "app/__init__.py",
            "tests/eval/eval_config.json", "tests/eval/test_eval.py",
            "tests/eval/evalsets/happy.evalset.json", ".env.example"} <= written


def test_the_scaffolded_agent_is_discovered_as_a_recipes_project(run_script, tmp_path):
    target = tmp_path / "claims"
    scaffold(run_script, target)
    payload = json.loads(
        run_script("skills/adk/scripts/discover.py", target, "--json", "--no-probe").stdout
    )
    layout = payload["discovered"]["layout"]
    assert layout["style"] == "recipes"
    assert layout["export"] == "app", "the loader prefers an App"
    assert payload["discovered"]["agent"]["model_from_env"] is True
    assert payload["discovered"]["criteria"]["cost_tier"] == "free"


def test_the_scaffolded_agent_passes_its_own_review(run_script, tmp_path):
    target = tmp_path / "claims"
    scaffold(run_script, target)
    payload = json.loads(
        run_script("skills/adk/scripts/discover.py", target, "--json", "--no-probe").stdout
    )
    errors = [f for f in payload["findings"] if f["severity"] == "error"
              and f["id"].startswith("AGT")]
    assert errors == [], errors


def test_the_starter_evalset_matches_the_generated_tool_contracts(run_script, tmp_path):
    target = tmp_path / "claims"
    scaffold(run_script, target)
    result = run_script(
        "skills/eval-review/scripts/crosscheck_tools.py",
        target / "tests/eval/evalsets/happy.evalset.json",
        "--agent-dir", target / "app", "--json",
    )
    assert json.loads(result.stdout)["summary"]["errors"] == 0
    assert result.returncode == 0


def test_literal_pattern_and_bound_constraints_reach_the_schemas(run_script, tmp_path):
    target = tmp_path / "claims"
    scaffold(run_script, target)
    schemas = (target / "app/schemas.py").read_text()
    assert 'Literal["travel", "meals"]' in schemas
    assert 'pattern=r"^CLM-[0-9]{6}$"' in schemas
    assert "ge=0.0" in schemas


def test_tools_return_validation_errors_rather_than_raising(run_script, tmp_path):
    """An exception ends the invocation; a returned error lets the model correct
    itself and retry."""
    import ast

    target = tmp_path / "claims"
    scaffold(run_script, target)
    source = (target / "app/tools.py").read_text()
    assert "except ValidationError" in source
    assert '"status": "error"' in source
    raises = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Raise)]
    assert raises == [], "no raise statement should survive in a generated tool"


def test_flat_scaffold_puts_the_config_beside_the_evalset(run_script, tmp_path):
    target = tmp_path / "claims"
    payload = json.loads(scaffold(run_script, target, layout="flat").stdout)
    written = {p.split(str(target) + "/")[-1] for p in payload["written"]}
    assert {"claims/agent.py", "claims/__init__.py", "claims/test_config.json",
            "claims/happy.test.json"} <= written


def test_a_reserved_agent_name_is_refused(run_script, tmp_path):
    result = run_script(
        "skills/agent-create/scripts/scaffold_agent.py",
        "--name", "user", "--dir", tmp_path / "x", "--tools", "t:a=str",
    )
    assert result.returncode == 1 and "user" in result.stderr


def test_an_agent_with_no_tools_is_refused(run_script, tmp_path):
    result = run_script(
        "skills/agent-create/scripts/scaffold_agent.py",
        "--name", "a", "--dir", tmp_path / "x", "--tools", "",
    )
    assert result.returncode == 2 and "no trajectory to evaluate" in result.stderr


def test_dry_run_writes_nothing(run_script, tmp_path):
    target = tmp_path / "claims"
    result = run_script(
        "skills/agent-create/scripts/scaffold_agent.py",
        "--name", "a", "--dir", target, "--tools", "t:a=str", "--dry-run",
    )
    assert result.returncode == 0
    assert not target.exists()


def test_existing_files_are_not_clobbered(run_script, tmp_path):
    target = tmp_path / "claims"
    scaffold(run_script, target)
    (target / "app/agent.py").write_text("# hand-edited\n")
    scaffold(run_script, target)
    assert (target / "app/agent.py").read_text() == "# hand-edited\n"
