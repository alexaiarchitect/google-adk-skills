"""Scripts for the rules and gates added on 2026-10-29 (ADK main after 2.13.0)."""

import json

HISTORY = "eval_history"
GOOD = "app_reliability_1730001001.evalset_result.json"     # 2/2 passed
WORSE = "app_reliability_1730001000.evalset_result.json"    # 1/2 passed


def _findings(result):
    return json.loads(result.stdout)["findings"]


def test_lint_flags_a_streaming_tool_on_a_text_model(run_script, agent_dir):
    result = run_script("skills/agent-review/scripts/lint_agent.py",
                        agent_dir("live_agent_broken"), "--no-backend", "--json")
    [finding] = [f for f in _findings(result) if f["id"] == "AGT081"]
    assert finding["severity"] == "warn" and "track_claim_progress" in finding["message"]
    assert result.returncode == 0          # a warning, not an error
    strict = run_script("skills/agent-review/scripts/lint_agent.py",
                        agent_dir("live_agent_broken"), "--no-backend", "--strict")
    assert strict.returncode == 1


def test_the_live_fixture_lints_clean(run_script, agent_dir):
    result = run_script("skills/agent-review/scripts/lint_agent.py", agent_dir("live_agent"),
                        "--no-backend", "--json")
    assert result.returncode == 0
    assert [f["id"] for f in _findings(result)] == ["AGT014"]


def test_validate_config_reports_cfg076_with_the_agent(run_script, agent_dir):
    agent = agent_dir("search_agent")
    on_213 = run_script("skills/eval-criteria/scripts/validate_config.py",
                        agent / "test_config.json", "--agent-dir", agent, "--no-backend",
                        "--adk-version", "2.13.0", "--json")
    [finding] = [f for f in _findings(on_213) if f["id"] == "CFG076"]
    assert finding["severity"] == "warn" and on_213.returncode == 0
    newer = run_script("skills/eval-criteria/scripts/validate_config.py",
                       agent / "test_config.json", "--agent-dir", agent, "--no-backend",
                       "--adk-version", "2.14.0", "--json")
    assert [f["severity"] for f in _findings(newer) if f["id"] == "CFG076"] == ["info"]


def test_validate_config_reports_cfg075_and_the_compound_rubric(run_script, fixtures):
    action = run_script("skills/eval-criteria/scripts/validate_config.py",
                        fixtures / "eval_configs/action_rubric_reply_judge.json",
                        "--no-backend", "--no-probe", "--json")
    assert {f["id"] for f in _findings(action)} == {"CFG075", "CFG071"}
    compound = run_script("skills/eval-criteria/scripts/validate_config.py",
                          fixtures / "eval_configs/compound_rubric.json",
                          "--no-backend", "--no-probe", "--json")
    assert "RUB006" in {f["id"] for f in _findings(compound)}


def test_templates_and_older_fixtures_stay_clear_of_the_new_rules(run_script, plugin_root,
                                                                  fixtures):
    configs = sorted((plugin_root / "skills/eval-criteria/assets/templates/configs").glob("*.json"))
    configs += [fixtures / "eval_configs/judge_rubrics.json",
                fixtures / "eval_configs/pr_trajectory.json"]
    assert configs
    for config in configs:
        result = run_script("skills/eval-criteria/scripts/validate_config.py", config,
                            "--no-backend", "--no-probe", "--json")
        ids = {f["id"] for f in _findings(result)}
        assert not ids & {"RUB006", "CFG075", "CFG076"}, (config.name, ids)


def test_mean_drop_gate(run_script, fixtures):
    history = fixtures / HISTORY
    held = run_script("skills/eval-analyze/scripts/compare_runs.py", history / WORSE,
                      history / GOOD, "--max-mean-drop", "0.05", "--json")
    assert held.returncode == 0
    payload = json.loads(held.stdout)
    gate = [g for g in payload["gates"] if g["gate"] == "max_mean_drop"][0]
    assert gate["passed"] and gate["checked"]
    assert payload["metric_means"]["tool_trajectory_avg_score"] == {
        "baseline": 0.75, "candidate": 1.0, "delta": 0.25, "cases": 2}

    dropped = run_script("skills/eval-analyze/scripts/compare_runs.py", history / GOOD,
                         history / WORSE, "--max-mean-drop", "0.05",
                         "--max-regressions", "5")
    assert dropped.returncode == 1
    assert "FAIL metric means" in dropped.stdout
    assert "## Metric means (shared cases)" in dropped.stdout
    tolerant = run_script("skills/eval-analyze/scripts/compare_runs.py", history / GOOD,
                          history / WORSE, "--max-mean-drop", "1.5", "--max-regressions", "5")
    assert tolerant.returncode == 0
    off = run_script("skills/eval-analyze/scripts/compare_runs.py", history / GOOD,
                     history / WORSE, "--max-regressions", "5", "--json")
    assert off.returncode == 0
    assert not [g for g in json.loads(off.stdout)["gates"] if g["gate"] == "max_mean_drop"]
    bad = run_script("skills/eval-analyze/scripts/compare_runs.py", history / GOOD,
                     history / WORSE, "--max-mean-drop", "-1")
    assert bad.returncode == 2


def test_the_nightly_workflow_alerts_on_a_drop_and_lints_clean(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    run_script("skills/eval-ci/scripts/gen_workflows.py", agent, "--tier", "nightly",
               "--baseline-dir", "evals/baseline")
    text = (agent / ".github/workflows/adk-evals-nightly.yml").read_text()
    assert "compare_runs.py" in text and "--max-mean-drop 0.05" in text
    assert "alert on a drop, not on a low absolute" in text.lower()
    assert "hashFiles('evals/baseline/**/*.evalset_result.json')" in text
    result = run_script("skills/eval-ci/scripts/lint_workflows.py", agent, "--json")
    assert result.returncode == 0
    assert "CI007" not in {f["id"] for f in _findings(result)}


def test_a_scheduled_run_without_a_drift_alert_is_noted(run_script, tmp_path):
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "nightly.yml").write_text(
        "name: nightly\non:\n  schedule:\n    - cron: '0 3 * * *'\njobs:\n  e:\n"
        "    runs-on: ubuntu-latest\n    steps:\n"
        "      - run: python3 run_evals.py app --not-evaluated fail\n"
        "      - uses: actions/upload-artifact@v4\n")
    result = run_script("skills/eval-ci/scripts/lint_workflows.py", tmp_path, "--json")
    [finding] = [f for f in _findings(result) if f["id"] == "CI007"]
    assert finding["severity"] == "info"


def test_discover_reports_a_src_layout_and_agents_cli_datasets(run_script, fixtures):
    result = run_script("skills/adk/scripts/discover.py", fixtures / "projects/src_layout",
                        "--json")
    layout = json.loads(result.stdout)["discovered"]["layout"]
    assert layout["style"] == "src"
    assert layout["agent_dir"] == "src/claims_agent"
    assert layout["app_name"] == "claims_agent" and layout["eval_app_name"] == "claims"
    assert layout["agents_cli_datasets"] == ["tests/eval/datasets/limits-dataset.json"]
    assert any("agents-cli" in note for note in layout["notes"])


def test_storage_help_is_version_aware(run_script):
    for script in ("skills/eval-run/scripts/run_evals.py",
                   "skills/eval-simulation/scripts/generate_eval_cases.py"):
        text = " ".join(run_script(script, "--help").stdout.split())
        assert "through 2.13.0" in text and "file://" in text and "ADK_EVAL_STORAGE_URI" in text
