"""Scripts for the ADK 2.13 sync: version-aware exit-code wording, the CI lint,
relative paths in run_evals.py, and the new findings."""

from __future__ import annotations

import json

import pytest

PR_WORKFLOW = (
    "name: evals\non: [pull_request]\njobs:\n  e:\n    runs-on: ubuntu-latest\n"
    "    steps:\n      - run: {install}\n      - run: adk eval app cases.evalset.json\n"
)


def _workflow(tmp_path, install="pip install ruff"):
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "eval.yml").write_text(PR_WORKFLOW.format(install=install))
    return tmp_path


def _ci001(result):
    payload = json.loads(result.stdout)
    return [f for f in payload["findings"] if f["id"] == "CI001"], payload


def test_bare_adk_eval_without_a_pin_is_an_error(run_script, tmp_path):
    root = _workflow(tmp_path)
    result = run_script("skills/eval-ci/scripts/lint_workflows.py", root, "--json")
    [finding], _ = _ci001(result)
    assert finding["severity"] == "error" and "through 2.12.0" in finding["message"]
    assert result.returncode == 1


@pytest.mark.parametrize("version", ["2.9", "2.12.0"])
def test_bare_adk_eval_below_2_13_is_an_error(run_script, tmp_path, version):
    root = _workflow(tmp_path)
    result = run_script("skills/eval-ci/scripts/lint_workflows.py", root, "--json",
                        "--adk-version", version)
    [finding], _ = _ci001(result)
    assert finding["severity"] == "error"


def test_bare_adk_eval_on_2_13_is_a_warning(run_script, tmp_path):
    root = _workflow(tmp_path)
    result = run_script("skills/eval-ci/scripts/lint_workflows.py", root, "--json",
                        "--adk-version", "2.13")
    [finding], _ = _ci001(result)
    assert finding["severity"] == "warn"
    assert finding["message"] == (
        "runs `adk eval` directly: from ADK 2.13.0 it exits 1 on a failure, but it cannot "
        "gate on a pass rate, regressions or budgets and does not read the result files; "
        "prefer run_evals.py")
    assert result.returncode == 0


def test_the_pin_in_the_workflow_itself_counts(run_script, tmp_path):
    root = _workflow(tmp_path, install='pip install "google-adk[eval]==2.13.*"')
    [finding], payload = _ci001(run_script("skills/eval-ci/scripts/lint_workflows.py", root,
                                           "--json"))
    assert finding["severity"] == "warn"
    assert payload["discovered"]["adk_versions"] == {"eval.yml": "2.13"}


def test_the_project_pyproject_counts(run_script, tmp_path):
    root = _workflow(tmp_path)
    (root / "pyproject.toml").write_text('dependencies = ["google-adk[eval]>=2.13,<3"]\n')
    [finding], _ = _ci001(run_script("skills/eval-ci/scripts/lint_workflows.py", root, "--json"))
    assert finding["severity"] == "warn"


def test_the_lowest_pin_across_requirements_and_workflow_wins(run_script, tmp_path):
    root = _workflow(tmp_path, install='pip install "google-adk[eval]==2.13.*"')
    (root / "requirements-dev.txt").write_text("google-adk==2.12.0\n")
    [finding], _ = _ci001(run_script("skills/eval-ci/scripts/lint_workflows.py", root, "--json"))
    assert finding["severity"] == "error" and "pinned: 2.12.0" in finding["message"]


def test_the_flag_overrides_the_pins(run_script, tmp_path):
    root = _workflow(tmp_path)
    (root / "requirements.txt").write_text("google-adk==2.12.0\n")
    [finding], _ = _ci001(run_script("skills/eval-ci/scripts/lint_workflows.py", root, "--json",
                                     "--adk-version", "2.13.0"))
    assert finding["severity"] == "warn"


def test_maturity_and_generator_wording_is_version_aware(plugin_root):
    for relative in ("skills/eval-ci/scripts/assess_maturity.py",
                     "skills/eval-ci/scripts/gen_workflows.py",
                     "skills/eval-run/scripts/run_evals.py"):
        text = (plugin_root / relative).read_text()
        assert "always exits 0" not in text, relative
        assert "2.13.0" in text, relative


def test_the_dry_run_note_is_version_aware(run_script, agent_dir):
    dry = run_script("skills/eval-run/scripts/run_evals.py", agent_dir("recipes_good"), "--dry-run")
    assert ("note: bare `adk eval` exits 0 on every version through 2.12.0 and exits 1 on a "
            "failure from 2.13.0; this wrapper reads the result files and gates "
            "(`--min-pass-rate`), so its exit code is the one to use") in dry.stderr


def test_relative_evalset_and_config_paths_are_resolved(run_script, tmp_agent, fixtures,
                                                        monkeypatch):
    """`adk eval` runs from the agent's parent directory, so a path relative to
    the caller's directory used to be read as an eval-set id (exit 2)."""
    agent = tmp_agent("recipes_good")
    monkeypatch.setenv("ADK_PYTHON", str(fixtures / "bin" / "fake_adk.py"))
    monkeypatch.setenv("FAKE_ADK_CHECK_PATHS", "1")
    monkeypatch.setenv("FAKE_ADK_EVAL_SET", "happy")
    monkeypatch.setenv("FAKE_ADK_PASSED", "3")
    caller = agent.parent
    result = run_script(
        "skills/eval-run/scripts/run_evals.py", "recipes_good",
        "--evalset", "recipes_good/tests/eval/evalsets/happy.evalset.json",
        "--config", "recipes_good/tests/eval/eval_config.json", "--json", cwd=caller,
    )
    assert result.returncode == 0, result.stderr
    dry = run_script(
        "skills/eval-run/scripts/run_evals.py", "recipes_good",
        "--evalset", "recipes_good/tests/eval/evalsets/happy.evalset.json",
        "--config", "recipes_good/tests/eval/eval_config.json", "--dry-run", cwd=caller,
    )
    assert f"--config_file_path={agent / 'tests/eval/eval_config.json'}" in dry.stdout
    assert str(agent / "tests/eval/evalsets/happy.evalset.json") in dry.stdout


@pytest.mark.parametrize("version, expected", [("2.12.0", 0), ("2.13.0", 1)])
def test_the_fake_runner_exits_like_the_version_it_reports(fixtures, tmp_agent, version,
                                                            expected, monkeypatch):
    """The stand-in mirrors the measured exit code, so the wrapper is shown to
    give the same verdict whichever release runs underneath."""
    import subprocess
    import sys

    agent = tmp_agent("recipes_good")
    monkeypatch.setenv("FAKE_ADK_VERSION", version)
    monkeypatch.setenv("FAKE_ADK_FAILED", "1")
    proc = subprocess.run([sys.executable, str(fixtures / "bin/fake_adk.py"), "-m",
                           "google.adk.cli", "eval", str(agent / "app"), "x.evalset.json"],
                          capture_output=True, text=True)
    assert proc.returncode == expected


# -- repeated runs in one result file ----------------------------------------------

import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
VENVS = PLUGIN_ROOT.parent / ".venvs"


def venv_python(name: str) -> Path:
    python = VENVS / name / "bin" / "python"
    if not python.is_file():
        pytest.skip(f"no ADK interpreter at {python}")
    return python


def _result(path, statuses, *, repeat_index=True):
    cases = []
    for eval_id, runs in statuses.items():
        for index, status in enumerate(runs):
            entry = {"eval_set_id": "happy", "eval_id": eval_id, "final_eval_status": status,
                     "overall_eval_metric_results": [
                         {"metric_name": "tool_trajectory_avg_score", "threshold": 1.0,
                          "score": 1.0 if status == 1 else 0.0, "eval_status": status},
                         {"metric_name": "tool_call_count_v1", "score": float(index + 1),
                          "eval_status": 4}],
                     "eval_metric_result_per_invocation": []}
            if repeat_index and len(runs) > 1:
                entry["repetition_index"] = index
            cases.append(entry)
    path.write_text(json.dumps({"eval_set_id": "happy", "eval_case_results": cases}))
    return path


def test_summary_labels_repeated_runs(run_script, tmp_path):
    path = _result(tmp_path / "app_happy_1790000001.evalset_result.json",
                   {"happy_a": [1, 2, 1], "happy_b": [1]})
    md = run_script("skills/eval-analyze/scripts/summarize_results.py", path)
    assert "`happy_a` (run 2/3)" in md.stdout and "[FAIL]" in md.stdout
    assert "repeated runs (2 distinct case(s))" in md.stdout
    payload = json.loads(run_script("skills/eval-analyze/scripts/summarize_results.py", path,
                                    "--json").stdout)
    labels = [c.get("run_label") for c in payload[0]["cases"]]
    assert labels == ["run 1/3", "run 2/3", "run 3/3", None]


def test_compare_uses_the_per_case_mean_of_repeated_runs(run_script, tmp_path):
    baseline = _result(tmp_path / "app_happy_1790000001.evalset_result.json",
                       {"happy_a": [1, 1, 1], "happy_b": [1]})
    candidate = _result(tmp_path / "app_happy_1790000002.evalset_result.json",
                        {"happy_a": [1, 2, 1], "happy_b": [1]})
    result = run_script("skills/eval-analyze/scripts/compare_runs.py", baseline, candidate,
                        "--json")
    payload = json.loads(result.stdout)
    candidate_side = payload["candidate"]
    assert candidate_side["cases"] == 2, "a repeated case counts once"
    assert candidate_side["figures"]["pass_rate"] == round((2 / 3 + 1) / 2, 4)
    assert candidate_side["repeated_cases"] == {"happy_a": {"runs": 3, "pass_mean": 0.6667}}
    assert candidate_side["figures"]["tool_calls"] == round((2.0 + 1.0) / 2, 4), \
        "per-case mean of the runs (1, 2, 3 -> 2), then across cases"
    assert payload["regressed_cases"] == ["happy_a"]
    assert result.returncode == 1


def test_pass_at_k_points_to_the_upstream_multi_run_config(plugin_root):
    text = (plugin_root / "skills/eval-analyze/scripts/pass_at_k.py").read_text()
    assert "multi_run_config" in text and "future replacement" in text


# -- config, simulation and voice --------------------------------------------------

def test_validate_config_reports_multi_run_as_info_only(run_script, tmp_path):
    config = tmp_path / "eval_config.json"
    config.write_text(json.dumps({"criteria": {"tool_trajectory_avg_score": 1.0},
                                  "multi_run_config": {"num_runs": 3, "cross_run_criteria": [
                                      {"method": "pass_rate", "threshold": 0.8}]}}))
    result = run_script("skills/eval-criteria/scripts/validate_config.py", config,
                        "--no-backend", "--adk-version", "2.13.0", "--json")
    ids = {f["id"]: f["severity"] for f in json.loads(result.stdout)["findings"]}
    assert ids.get("CFG072") == "info" and "CFG073" not in ids and "CFG004" not in ids
    assert result.returncode == 0


def test_gen_config_never_emits_multi_run_config(run_script):
    for tier in ("pr", "nightly"):
        result = run_script("skills/eval-criteria/scripts/gen_config.py", "--tier", tier, "--json")
        assert "multi_run_config" not in result.stdout


def _voice(run_script, *extra):
    result = run_script("skills/eval-simulation/scripts/gen_sim_config.py", "--kind", "user",
                        "--model", "gemini-flash-latest", "--json", *extra)
    return result, (json.loads(result.stdout)["config"] if result.returncode == 0 else None)


def test_audio_flags_write_the_exact_nesting(run_script):
    result, config = _voice(run_script, "--audio", "--audio-model",
                            "gemini-2.5-flash-preview-tts", "--voice", "Kore",
                            "--language", "en-GB", "--live-timeout", "120")
    simulator = config["user_simulator_config"]
    assert simulator["type"] == "llm_audio"
    assert simulator["audio_model"] == "gemini-2.5-flash-preview-tts"
    speech = simulator["audio_model_configuration"]["speech_config"]
    assert speech["voice_config"]["prebuilt_voice_config"]["voice_name"] == "Kore"
    assert speech["language_code"] == "en-GB"
    assert config["live_model_config"] == {"timeout_seconds": 120}


def test_audio_defaults_are_adks_defaults(run_script):
    _, config = _voice(run_script, "--audio")
    simulator = config["user_simulator_config"]
    assert simulator["audio_model"] == "cloud_tts"
    speech = simulator["audio_model_configuration"]["speech_config"]
    assert speech["voice_config"]["prebuilt_voice_config"]["voice_name"] == "en-US-Studio-O"
    assert speech["language_code"] == "en-US"
    assert "live_model_config" not in config
    _, text = _voice(run_script)
    assert "audio_model" not in text["user_simulator_config"]


@pytest.mark.parametrize("extra", [("--voice", "Kore"), ("--audio", "--live-timeout", "0")])
def test_voice_flags_are_refused_out_of_place(run_script, extra):
    result, _ = _voice(run_script, *extra)
    assert result.returncode == 2


@pytest.mark.parametrize("venv", ["adk292", "adk213"])
def test_the_voice_config_validates_with_the_real_class(run_script, tmp_path, venv):
    python = venv_python(venv)
    _, config = _voice(run_script, "--audio", "--voice", "Kore", "--live-timeout", "120")
    config["criteria"] = {"hallucinations_v1": 0.5}
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(config))
    code = ("import sys, pathlib\n"
            "from google.adk.evaluation.eval_config import EvalConfig\n"
            "c = EvalConfig.model_validate_json(pathlib.Path(sys.argv[1]).read_text())\n"
            "u = c.user_simulator_config\n"
            "v = u.audio_model_configuration.speech_config.voice_config.prebuilt_voice_config\n"
            "print(type(u).__name__, v.voice_name, c.live_model_config.timeout_seconds,"
            " u.model_extra)\n")
    proc = subprocess.run([str(python), "-I", "-c", code, str(path)], capture_output=True,
                          text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-800:]
    assert proc.stdout.split() == ["LlmAudioUserSimulatorConfig", "Kore", "120", "{}"]


def test_validate_sim_config_reports_the_live_and_audio_findings(run_script, tmp_agent):
    agent = tmp_agent("stateful_agent")
    config = agent / "test_config.json"
    config.write_text(json.dumps({
        "criteria": {"hallucinations_v1": 0.5},
        "user_simulator_config": {**AUDIO_SIM, "audio_modle": "cloud_tts"},
        "live_model_config": {"timeout_seconds": 0},
    }))
    result = run_script("skills/eval-simulation/scripts/validate_sim_config.py", agent,
                        "--config", config, "--json")
    ids = {f["id"]: f["severity"] for f in json.loads(result.stdout)["findings"]}
    assert ids["SIM032"] == "warn" and ids["SIM033"] == "warn" and ids["SIM034"] == "warn"


AUDIO_SIM = {"type": "llm_audio", "model": "gemini-flash-latest", "max_allowed_invocations": 6}


# -- resume cases --------------------------------------------------------------------

RESUME = PLUGIN_ROOT / "skills/eval-create/assets/templates/evalsets/resume.evalset.json"
ADVERSARIAL = PLUGIN_ROOT / "skills/eval-create/assets/templates/evalsets/adversarial.evalset.json"


def test_the_resume_template_holds_then_proceeds():
    doc = json.loads(RESUME.read_text())
    hold, proceed = doc["eval_cases"]
    assert set(hold["session_input"]) == {"app_name", "user_id", "state"}
    assert hold["session_input"]["state"]["claim_step"] == "receipt_requested"
    assert hold["conversation"][0]["intermediate_data"]["tool_uses"] == []
    assert "waiting for the receipt" in hold["conversation"][0]["final_response"]["parts"][0]["text"]
    tools = proceed["conversation"][0]["intermediate_data"]["tool_uses"]
    assert [t["name"] for t in tools] == ["submit_claim", "notify_approver"]
    claimant = proceed["session_input"]["state"]["claimant"]
    assert tools[0]["args"]["req"]["employee_id"] == claimant["employee_id"]
    assert tools[1]["args"]["req"]["claimant_name"] == claimant["name"]


@pytest.mark.parametrize("venv", ["adk292", "adk213"])
def test_the_new_templates_validate_with_the_real_models(venv):
    python = venv_python(venv)
    code = ("import sys, pathlib\nfrom google.adk.evaluation.eval_set import EvalSet\n"
            "for p in sys.argv[1:]:\n"
            "    s = EvalSet.model_validate_json(pathlib.Path(p).read_text())\n"
            "    print(len(s.eval_cases))\n")
    proc = subprocess.run([str(python), "-I", "-c", code, str(RESUME), str(ADVERSARIAL)],
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-800:]
    assert proc.stdout.split() == ["2", "3"]


def test_the_resume_template_passes_the_plugin_validator(run_script):
    config = PLUGIN_ROOT / "skills/eval-criteria/assets/templates/configs/pr_trajectory.json"
    result = run_script("skills/eval-review/scripts/validate_evalset.py", RESUME,
                        "--config", config, "--adk-version", "2.13", "--json")
    assert result.returncode == 0, result.stdout


def test_the_adversarial_template_has_a_prompt_injection_case():
    doc = json.loads(ADVERSARIAL.read_text())
    [case] = [c for c in doc["eval_cases"] if "injection" in c["eval_id"]]
    text = case["conversation"][0]["user_content"]["parts"][0]["text"]
    assert "Ignore your rules" in text and "token" in text


def test_gen_cases_writes_a_resume_case_with_seeded_state(run_script, tmp_agent):
    agent = tmp_agent("stateful_agent")
    spec = json.dumps([{"eval_id": "resume_receipt_requested_holds_again",
                        "state": {"claim_step": "receipt_requested", "claim_id": "CLM-000740"},
                        "turns": [{"user": "Approve it now please.", "tools": [],
                                   "response": "Still waiting for the receipt."}]}])
    result = run_script("skills/eval-create/scripts/gen_cases.py", agent, "--spec", spec,
                        "--new", "resume_more", "--json")
    payload = json.loads(result.stdout)
    assert payload["summary"]["added"][0]["category"] == "resume"
    written = json.loads(Path(payload["written"][0]).read_text())
    assert written["eval_cases"][0]["session_input"]["state"]["claim_step"] == "receipt_requested"
    assert not [f for f in payload["validation"]["findings"] if f["id"] == "EVS075"]
    typo = json.dumps([{"eval_id": "resume_typo", "state": {"claim_stage": "x"},
                        "turns": [{"user": "hi", "tools": []}]}])
    flagged = json.loads(run_script("skills/eval-create/scripts/gen_cases.py", agent, "--spec",
                                    typo, "--new", "resume_typo", "--dry-run", "--json").stdout)
    assert [f["id"] for f in flagged["validation"]["findings"]].count("EVS075") == 1
    bad = run_script("skills/eval-create/scripts/gen_cases.py", agent, "--spec",
                     json.dumps([{"eval_id": "resume_x", "state": [1],
                                  "turns": [{"user": "hi"}]}]), "--new", "x")
    assert bad.returncode == 2


def test_coverage_matrix_counts_cases_that_seed_state(run_script, agent_dir):
    result = run_script("skills/eval-review/scripts/coverage_matrix.py",
                        agent_dir("stateful_agent"), "--json")
    seeded = json.loads(result.stdout)["discovered"]["seeded_state_cases"]
    assert set(seeded) == {"resume_receipt_requested_holds", "resume_receipt_validated_submits"}
    md = run_script("skills/eval-review/scripts/coverage_matrix.py", agent_dir("stateful_agent"))
    assert "Cases that seed state: 2 of 2" in md.stdout


def test_validate_evalset_on_the_stateful_fixtures(run_script, agent_dir):
    good = run_script("skills/eval-review/scripts/validate_evalset.py",
                      agent_dir("stateful_agent"), "--adk-version", "2.13", "--json")
    assert good.returncode == 0
    assert not [f for f in json.loads(good.stdout)["findings"] if f["severity"] != "info"]
    broken = run_script("skills/eval-review/scripts/validate_evalset.py",
                        agent_dir("stateful_agent_broken"), "--json")
    [finding] = [f for f in json.loads(broken.stdout)["findings"] if f["id"] == "EVS075"]
    assert "'claim_stage'" in finding["message"]


def test_a_drive_qualified_id_through_the_script(run_script, tmp_path):
    doc = json.loads(RESUME.read_text())
    doc["eval_set_id"] = "c:resume"
    path = tmp_path / "x.evalset.json"
    path.write_text(json.dumps(doc))
    on_213 = run_script("skills/eval-review/scripts/validate_evalset.py", path,
                        "--adk-version", "2.13.0", "--json")
    old = run_script("skills/eval-review/scripts/validate_evalset.py", path,
                     "--adk-version", "2.12.0", "--json")
    sev = lambda r: [f["severity"] for f in json.loads(r.stdout)["findings"] if f["id"] == "EVS076"]
    assert sev(on_213) == ["error"] and sev(old) == ["warn"]


# -- agent review on the stateful fixtures -------------------------------------------

def _lint(run_script, agent):
    result = run_script("skills/agent-review/scripts/lint_agent.py", agent, "--no-backend",
                        "--json")
    return result, json.loads(result.stdout)["findings"]


def test_lint_is_quiet_on_the_stateful_agent(run_script, agent_dir):
    result, findings = _lint(run_script, agent_dir("stateful_agent"))
    assert result.returncode == 0
    assert not [f for f in findings if f["severity"] != "info"], findings


def test_lint_reports_the_broken_stateful_agent(run_script, agent_dir):
    _, findings = _lint(run_script, agent_dir("stateful_agent_broken"))
    by_id = {f["id"]: f for f in findings}
    assert by_id["AGT080"]["severity"] == "warn" and "{approver}" in by_id["AGT080"]["message"]
    assert by_id["AGT015"]["severity"] == "warn" and "cousins" in by_id["AGT015"]["message"]


@pytest.mark.parametrize("name", ["stateful_agent", "stateful_agent_broken"])
@pytest.mark.parametrize("venv", ["adk292", "adk213"])
def test_both_stateful_fixtures_load_under_real_adk(run_script, tmp_agent, monkeypatch, name, venv):
    """Both mistakes in the broken fixture show only at run time, so ADK loads it."""
    monkeypatch.setenv("ADK_PYTHON", str(venv_python(venv)))
    agent = tmp_agent(name)
    result = run_script("skills/agent-review/scripts/load_agent.py", agent, "--json")
    payload = json.loads(result.stdout)
    assert payload["loaded"] is True, payload
    assert payload["agent_name"] == ("claims_agent" if name == "stateful_agent" else "claims_router")


def test_the_yaml_agent_message_is_version_aware(run_script, agent_dir):
    _, findings = _lint(run_script, agent_dir("yaml_agent"))
    [finding] = [f for f in findings if f["id"] == "AGT050"]
    assert finding["message"] == (
        "this is a YAML config agent: the feature flag is stable from 2.13; the YAML loader "
        "still prints an [EXPERIMENTAL] warning through 2.13.0 (main after 2.13.0 drops it) "
        "and the config classes are deprecated")


# -- environment knobs ---------------------------------------------------------------

def test_check_env_reports_the_suppress_variable(run_script, tmp_agent):
    agent = tmp_agent("flat_good")
    (agent / ".env").write_text("GOOGLE_API_KEY=dummy\nADK_SUPPRESS_EXPERIMENTAL_FEATURE_WARNINGS=1\n")
    payload = json.loads(run_script("skills/adk/scripts/check_env.py", agent, "--no-probe",
                                    "--json").stdout)
    assert payload["knobs"]["ADK_SUPPRESS_EXPERIMENTAL_FEATURE_WARNINGS"] == {
        "value": "1", "from": ".env"}
    assert any("ADK_SUPPRESS_EXPERIMENTAL_FEATURE_WARNINGS=1 is set" in n for n in payload["notes"])
    (agent / ".env").write_text("GOOGLE_API_KEY=dummy\n")
    text = run_script("skills/adk/scripts/check_env.py", agent, "--no-probe")
    assert "optional knobs (unset): ADK_SUPPRESS_EXPERIMENTAL_FEATURE_WARNINGS" in text.stdout


def test_env_examples_mention_the_suppress_variable(plugin_root):
    for layout in ("flat", "recipes"):
        text = (plugin_root / f"skills/agent-create/assets/templates/{layout}/.env.example.tmpl"
                ).read_text()
        assert "# ADK_SUPPRESS_EXPERIMENTAL_FEATURE_WARNINGS=1" in text
