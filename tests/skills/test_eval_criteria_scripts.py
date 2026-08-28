"""eval-criteria scripts: the catalog, config generation, validation, rubrics."""

import json


def test_metric_info_lists_the_whole_catalog(run_script):
    result = run_script("skills/eval-criteria/scripts/metric_info.py", "--json")
    payload = json.loads(result.stdout)
    assert len(payload["metrics"]) == 13
    assert payload["defaults_when_no_config"]["response_match_score"] == 0.8


def test_metric_info_explains_one_metric(run_script):
    result = run_script("skills/eval-criteria/scripts/metric_info.py", "hallucinations_v1")
    assert "groundedness" in result.stdout.lower()
    assert "evaluate_intermediate_nl_responses" in result.stdout


def test_metric_info_suggests_a_correction(run_script):
    result = run_script("skills/eval-criteria/scripts/metric_info.py", "hallucination_v1")
    assert result.returncode == 2
    assert "hallucinations_v1" in result.stderr


def test_pr_tier_is_free_and_deterministic(run_script):
    result = run_script("skills/eval-criteria/scripts/gen_config.py", "--tier", "pr", "--json")
    payload = json.loads(result.stdout)
    assert payload["cost_tier"] == "free"
    criteria = payload["config"]["criteria"]
    assert criteria["tool_trajectory_avg_score"]["threshold"] == 1.0
    assert criteria["response_match_score"] == 0.0


def test_nightly_tier_fills_in_judge_options_and_groundedness_settings(run_script):
    result = run_script(
        "skills/eval-criteria/scripts/gen_config.py", "--tier", "nightly",
        "--judge-model", "gemini-flash-latest", "--json",
    )
    criteria = json.loads(result.stdout)["config"]["criteria"]
    halluc = criteria["hallucinations_v1"]
    assert halluc["judge_model_options"]["judge_model"] == "gemini-flash-latest"
    assert halluc["judge_model_options"]["num_samples"] == 3
    assert halluc["evaluate_intermediate_nl_responses"] is True


def test_generated_configs_are_validated_before_they_are_written(run_script, tmp_path):
    out = tmp_path / "eval_config.json"
    result = run_script(
        "skills/eval-criteria/scripts/gen_config.py", "--tier", "pr", "--out", out, "--json"
    )
    payload = json.loads(result.stdout)
    assert payload["validation"]["summary"]["errors"] == 0
    assert out.exists()


def test_an_existing_config_is_not_overwritten_without_force(run_script, tmp_path):
    out = tmp_path / "eval_config.json"
    out.write_text('{"criteria": {}}')
    run_script("skills/eval-criteria/scripts/gen_config.py", "--tier", "pr", "--out", out)
    assert out.read_text() == '{"criteria": {}}'
    run_script("skills/eval-criteria/scripts/gen_config.py", "--tier", "pr", "--out", out, "--force")
    assert "tool_trajectory_avg_score" in out.read_text()


def test_dry_run_writes_nothing(run_script, tmp_path):
    out = tmp_path / "eval_config.json"
    run_script("skills/eval-criteria/scripts/gen_config.py", "--tier", "pr", "--out", out, "--dry-run")
    assert not out.exists()


def test_custom_tier_accepts_a_match_type(run_script):
    result = run_script(
        "skills/eval-criteria/scripts/gen_config.py", "--tier", "custom",
        "--metrics", "tool_trajectory_avg_score=1.0:ANY_ORDER", "--json",
    )
    criteria = json.loads(result.stdout)["config"]["criteria"]
    assert criteria["tool_trajectory_avg_score"]["match_type"] == "ANY_ORDER"


def test_unknown_metric_is_refused_with_a_suggestion(run_script):
    result = run_script(
        "skills/eval-criteria/scripts/gen_config.py", "--tier", "custom",
        "--metrics", "tool_trajectory_score=1.0",
    )
    assert result.returncode == 2 and "tool_trajectory_avg_score" in result.stderr


def test_user_simulation_refuses_a_reference_only_selection(run_script):
    result = run_script(
        "skills/eval-criteria/scripts/gen_config.py", "--tier", "pr", "--user-simulation",
        "--judge-model", "gemini-flash-latest",
    )
    assert result.returncode == 1
    assert "simulation-compatible" in result.stderr


def test_camel_style_is_available_for_projects_that_use_it(run_script):
    result = run_script(
        "skills/eval-criteria/scripts/gen_config.py", "--tier", "nightly",
        "--style", "camel", "--judge-model", "gemini-flash-latest", "--json",
    )
    criteria = json.loads(result.stdout)["config"]["criteria"]
    assert "judgeModelOptions" in criteria["hallucinations_v1"]


def test_every_shipped_config_template_validates(run_script, plugin_root):
    for template in sorted((plugin_root / "skills/eval-criteria/assets/templates/configs").glob("*.json")):
        result = run_script(
            "skills/eval-criteria/scripts/validate_config.py", template, "--no-backend", "--json"
        )
        summary = json.loads(result.stdout)["summary"]
        assert summary["errors"] == 0, f"{template.name}: {result.stdout}"


def test_every_shipped_rubric_template_lints_clean(run_script, plugin_root):
    for template in sorted((plugin_root / "skills/eval-criteria/assets/templates/rubrics").glob("*.json")):
        result = run_script("skills/eval-criteria/scripts/gen_rubrics.py", "--lint", template, "--json")
        assert json.loads(result.stdout)["lint"]["summary"]["errors"] == 0, template.name


def test_restraint_rubric_is_generated_in_the_gradeable_shape(run_script, agent_dir):
    result = run_script(
        "skills/eval-criteria/scripts/gen_rubrics.py", "--kind", "restraint",
        "--tool", "submit_claim", "--condition", "the cost centre is frozen",
        "--agent-dir", agent_dir("flat_good"), "--json",
    )
    payload = json.loads(result.stdout)
    rubric = payload["rubrics"][0]
    assert rubric["type"] == "TOOL_USE_QUALITY"
    assert "does NOT call `submit_claim`" in rubric["rubric_content"]["text_property"]
    assert payload["lint"]["summary"]["errors"] == 0
    assert payload["lint"]["discovered"]["suggested_threshold"] == 1.0


def test_rubric_lint_rejects_a_graded_scale(run_script, tmp_path):
    rubrics = tmp_path / "r.json"
    rubrics.write_text(json.dumps([
        {"rubric_id": "q", "rubric_content": {"text_property": "Rate the reply from 1 to 5."}}
    ]))
    result = run_script("skills/eval-criteria/scripts/gen_rubrics.py", "--lint", rubrics)
    assert result.returncode == 1 and "RUB003" in result.stdout
