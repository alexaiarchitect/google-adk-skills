"""Scripts for the round-8 rules (ADK main through 2026-11-02): CFG077 in both
config validators, ENV015 in discover and lint, AGT082 in lint and discover,
the agents-cli config note and the version wording."""

import json
import shutil

import pytest

VALIDATE = "skills/eval-criteria/scripts/validate_config.py"
VALIDATE_SIM = "skills/eval-simulation/scripts/validate_sim_config.py"
LINT = "skills/agent-review/scripts/lint_agent.py"
DISCOVER = "skills/adk/scripts/discover.py"


def _by_id(result, fid):
    return [f for f in json.loads(result.stdout)["findings"] if f["id"] == fid]


# -- CFG077 --------------------------------------------------------------------

@pytest.mark.parametrize("script", [VALIDATE, VALIDATE_SIM])
@pytest.mark.parametrize("version, severity", [("2.13.0", "warn"), ("2.9.2", "warn"),
                                               ("2.14.0", "info")])
def test_parallelism_fixture(run_script, fixtures, script, version, severity):
    config = fixtures / "eval_configs/parallelism.json"
    result = run_script(script, config, "--no-backend" if script == VALIDATE else "--json",
                        "--adk-version", version, "--json")
    [finding] = _by_id(result, "CFG077")
    assert finding["severity"] == severity and finding["path"].endswith("parallelism.json")
    assert set(finding) >= {"id", "severity", "area", "message", "path", "fix", "source"}
    assert not _by_id(result, "CFG004")
    assert result.returncode == 0


def test_unprobed_sim_validator_warns(run_script, fixtures):
    result = run_script(VALIDATE_SIM, fixtures / "eval_configs/parallelism.json", "--json")
    assert [f["severity"] for f in _by_id(result, "CFG077")] == ["warn"]


@pytest.mark.parametrize("script", [VALIDATE, VALIDATE_SIM])
@pytest.mark.parametrize("version", ["2.9.2", "2.13.0", "2.14.0"])
def test_parallelism_zero_is_an_error_everywhere(run_script, fixtures, script, version):
    config = fixtures / "eval_configs/parallelism_zero.json"
    result = run_script(script, config, "--adk-version", version, "--json",
                        *(["--no-backend"] if script == VALIDATE else []))
    [finding] = _by_id(result, "CFG077")
    assert finding["severity"] == "error"
    assert "Input should be greater than or equal to 1" in finding["message"]
    assert result.returncode == 1


def test_strict_turns_the_warning_into_a_failure(run_script, fixtures):
    result = run_script(VALIDATE, fixtures / "eval_configs/parallelism.json", "--no-backend",
                        "--adk-version", "2.13.0", "--strict", "--json")
    assert result.returncode == 1


def test_config_templates_validate_without_cfg077(run_script, plugin_root):
    for template in sorted((plugin_root / "skills/eval-criteria/assets/templates/configs")
                           .glob("*.json")):
        result = run_script(VALIDATE, template, "--no-backend", "--adk-version", "2.13.0",
                            "--json")
        assert not _by_id(result, "CFG077") and not _by_id(result, "CFG004"), template


@pytest.mark.parametrize("args", [("--tier", "pr"), ("--tier", "nightly")])
def test_gen_config_never_emits_parallelism(run_script, args):
    result = run_script("skills/eval-criteria/scripts/gen_config.py", *args, "--json")
    assert result.returncode == 0 and '"parallelism"' not in result.stdout


def test_gen_sim_config_never_emits_parallelism(run_script):
    result = run_script("skills/eval-simulation/scripts/gen_sim_config.py", "--kind", "user",
                        "--model", "gemini-flash-latest", "--json")
    assert result.returncode == 0 and '"parallelism"' not in result.stdout


def test_discover_reports_cfg077_for_a_project_config(run_script, tmp_path, agent_dir):
    agent = tmp_path / "flat_good"
    shutil.copytree(agent_dir("flat_good"), agent)
    (agent / "test_config.json").write_text(json.dumps(
        {"criteria": {"tool_trajectory_avg_score": 1.0}, "parallelism": 0}))
    result = run_script(DISCOVER, agent, "--json", "--no-probe")
    assert [f["severity"] for f in _by_id(result, "CFG077")] == ["error"]


# -- ENV015 --------------------------------------------------------------------

@pytest.mark.parametrize("script, extra", [(DISCOVER, ("--no-probe",)),
                                           (LINT, ("--no-backend",))])
def test_kebab_app_is_an_error(run_script, fixtures, script, extra):
    for target in ("projects/kebab_app", "projects/kebab_app/my-agent"):
        result = run_script(script, fixtures / target, "--json", *extra)
        [finding] = _by_id(result, "ENV015")
        assert finding["severity"] == "error" and finding["area"] == "layout"
        assert "Invalid agent name: 'my-agent'" in finding["message"]
        assert "'my_agent'" in finding["fix"]
        assert result.returncode == 1


@pytest.mark.parametrize("script, extra", [(DISCOVER, ("--no-probe",)),
                                           (LINT, ("--no-backend",))])
def test_keyword_app_is_an_info(run_script, fixtures, script, extra):
    result = run_script(script, fixtures / "projects/keyword_app", "--json", *extra)
    [finding] = _by_id(result, "ENV015")
    assert finding["severity"] == "info" and "Python keyword" in finding["message"]


def test_src_layout_under_a_kebab_project_is_clean(run_script, fixtures, tmp_path):
    project = tmp_path / "claims-project"
    shutil.copytree(fixtures / "projects/src_layout", project)
    for script, extra in ((DISCOVER, "--no-probe"), (LINT, "--no-backend")):
        assert not _by_id(run_script(script, project, "--json", extra), "ENV015")


def test_no_other_fixture_trips_env015(run_script, fixtures):
    targets = [*sorted((fixtures / "agents").iterdir()), fixtures / "projects/src_layout"]
    for target in targets:
        result = run_script(DISCOVER, target, "--json", "--no-probe", "--focus", "layout")
        assert not _by_id(result, "ENV015"), target


# -- AGT082 --------------------------------------------------------------------

def test_lint_warns_on_memory_inside_an_agent_tool(run_script, agent_dir):
    result = run_script(LINT, agent_dir("agent_tool_memory"), "--no-backend", "--json")
    [finding] = _by_id(result, "AGT082")
    assert finding["severity"] == "warn"
    assert "'policy_lookup'" in finding["message"] and "4e0ac64e3" in finding["message"]
    assert finding["path"].endswith("agent.py")
    assert result.returncode == 0
    strict = run_script(LINT, agent_dir("agent_tool_memory"), "--no-backend", "--strict")
    assert strict.returncode == 1


@pytest.mark.parametrize("version, severity", [("2.9.2", "warn"), ("2.13.0", "warn"),
                                               ("2.14.0", "info")])
def test_lint_agt082_follows_adk_version(run_script, agent_dir, version, severity):
    result = run_script(LINT, agent_dir("agent_tool_memory"), "--no-backend", "--json",
                        "--adk-version", version)
    assert [f["severity"] for f in _by_id(result, "AGT082")] == [severity]


def test_discover_reports_agt082(run_script, agent_dir):
    result = run_script(DISCOVER, agent_dir("agent_tool_memory"), "--json", "--no-probe")
    assert [f["severity"] for f in _by_id(result, "AGT082")] == ["warn"]


def test_existing_agent_tool_fixture_stays_clean(run_script, agent_dir):
    result = run_script(LINT, agent_dir("wrapped_tools"), "--no-backend", "--json")
    assert not _by_id(result, "AGT082")


def test_lint_docstring_counts_fourteen_dimensions(run_script):
    text = run_script(LINT, "--help").stdout
    assert "Fourteen dimensions:" in text and "AGT082" in text


# -- notes and wording ---------------------------------------------------------

def test_discover_names_several_agents_cli_configs(run_script, tmp_path, fixtures):
    project = tmp_path / "voice"
    shutil.copytree(fixtures / "projects/src_layout", project)
    eval_dir = project / "tests/eval"
    (eval_dir / "eval_config.yaml").rename(eval_dir / "eval_config_system1.yaml")
    (eval_dir / "eval_config_system2.yaml").write_text("metrics_to_run: []\n")
    (eval_dir / "datasets/system2.json").write_text("{}")
    result = run_script(DISCOVER, project, "--json", "--no-probe")
    layout = json.loads(result.stdout)["discovered"]["layout"]
    assert layout["agents_cli_configs"] == ["tests/eval/eval_config_system1.yaml",
                                            "tests/eval/eval_config_system2.yaml"]
    [note] = [n for n in layout["notes"] if "agents-cli eval dataset" in n]
    assert note.startswith("2 agents-cli eval dataset(s)")
    assert "one of 2 configs" in note


def test_dry_run_notes_parallelism(run_script, agent_dir):
    result = run_script("skills/eval-run/scripts/run_evals.py", agent_dir("recipes_good"),
                        "--dry-run")
    text = " ".join(result.stderr.split())
    assert result.returncode == 0
    assert ("`adk eval --parallelism N` and a top-level `parallelism` key exist on main after "
            "2.13.0 (`e9e8f6156`: flag or argument > config key > default 4); through 2.13.0 "
            "cases run 4 at a time and the key is ignored") in text


def test_help_texts_carry_the_unreleased_rules(run_script):
    run_help = " ".join(run_script("skills/eval-run/scripts/run_evals.py", "--help")
                        .stdout.split())
    assert "--parallelism" in run_help and "e9e8f6156" in run_help
    summary_help = " ".join(run_script("skills/eval-analyze/scripts/summarize_results.py",
                                       "--help").stdout.split())
    assert "d1f2add4f" in summary_help and "averaged" in summary_help
    sim_help = " ".join(run_script(VALIDATE_SIM, "--help").stdout.split())
    assert "--adk-version" in sim_help and "CFG077" in sim_help
