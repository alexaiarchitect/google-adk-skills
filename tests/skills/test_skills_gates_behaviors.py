"""Scripts for the 2.12 sync: skills and context findings through lint and
discovery, EVAL.yaml validation, merge gates, frozen suites, --state-file,
behavioural tests and the new templates."""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = PLUGIN_ROOT.parent / "tmp" / "adk-python" / "contributing" / "samples"
RECIPES_RETAIL = PLUGIN_ROOT.parent / "tmp" / "adk-recipes" / "plugins" / "retail"
VENVS = {
    version: Path(os.environ.get(f"ADK_SKILLS_PYTHON_{version.replace('.', '')}",
                                 PLUGIN_ROOT.parent / ".venvs" / f"adk{version.replace('.', '')}"
                                 / "bin" / "python"))
    for version in ("2.9.2", "2.12", "2.13")
}
EFFICIENCY = "eval_history/app_efficiency_1790000000.123456.evalset_result.json"
CACHED = "eval_history/app_efficiency_cached_1790000100.5.evalset_result.json"
LINT = "skills/agent-review/scripts/lint_agent.py"
DISCOVER = "skills/adk/scripts/discover.py"


def finding_ids(payload: dict) -> list[str]:
    return [f["id"] for f in payload["findings"]]


def need_python(version: str) -> Path:
    python = VENVS[version]
    if not python.is_file():
        pytest.skip(f"no ADK {version} interpreter at {python}")
    return python


@pytest.fixture
def fake_adk(fixtures, monkeypatch):
    monkeypatch.setenv("ADK_PYTHON", str(fixtures / "bin" / "fake_adk.py"))
    return fixtures / "bin" / "fake_adk.py"


# -- lint and discover ---------------------------------------------------------

def test_lint_reports_the_skill_facts_and_stays_clean(run_script, agent_dir):
    result = run_script(LINT, agent_dir("skilled_agent"), "--no-backend", "--json")
    payload = json.loads(result.stdout)
    assert result.returncode == 0, result.stdout
    assert not [i for i in finding_ids(payload) if i.startswith("AGT07")]
    skills = payload["discovered"]["skills"]
    assert [(s["name"], s["source"]) for s in skills] == [("receipt-check", "inline"),
                                                          ("claim-policy", "dir")]
    assert payload["discovered"]["skill_toolsets"][0]["var"] == "claim_skills"
    assert payload["discovered"]["events_compaction_config"] is None


def test_lint_fails_the_broken_skills_fixture(run_script, agent_dir):
    result = run_script(LINT, agent_dir("skilled_agent_broken"), "--no-backend", "--json",
                        "--adk-version", "2.9.2")
    payload = json.loads(result.stdout)
    assert result.returncode == 1
    found = finding_ids(payload)
    for fid in ("AGT070", "AGT071", "AGT072", "AGT073", "AGT074", "AGT075"):
        assert fid in found, fid
    note = next(f for f in payload["findings"] if f["id"] == "AGT074")
    assert "only exist from 2.10" in note["message"]


def test_a_lifecycle_flag_in_the_env_silences_agt074(run_script, tmp_agent):
    agent = tmp_agent("skilled_agent_broken")
    (agent / ".env").write_text("ADK_ENABLE_SKILL_LIFECYCLE=1\n")
    payload = json.loads(run_script(LINT, agent, "--no-backend", "--json").stdout)
    assert "AGT074" not in finding_ids(payload)


def test_lint_reports_the_compaction_fixture(run_script, agent_dir):
    payload = json.loads(run_script(LINT, agent_dir("compaction_broken"), "--no-backend",
                                    "--json").stdout)
    found = finding_ids(payload)
    assert found.count("AGT076") == 2 and "AGT078" in found and "AGT079" in found
    assert payload["discovered"]["events_compaction_config"] == {
        "compaction_interval": 0, "overlap_size": -1}


def test_long_conversations_get_agt077_in_lint_and_discover(run_script, tmp_agent, fixtures):
    project = tmp_agent("recipes_good")
    shutil.copy(fixtures / "evalsets" / "long_conversation.evalset.json",
                project / "tests" / "eval" / "evalsets")
    lint = json.loads(run_script(LINT, project, "--no-backend", "--json").stdout)
    assert "AGT077" in finding_ids(lint)
    discovered = json.loads(run_script(DISCOVER, project, "--no-probe", "--json").stdout)
    assert "AGT077" in finding_ids(discovered)


def test_discover_carries_skills_and_skips_evs073_when_every_skill_is_activated(
        run_script, agent_dir, fake_adk):
    payload = json.loads(run_script(DISCOVER, agent_dir("skilled_agent"), "--json").stdout)
    agent = payload["discovered"]["agent"]
    assert [s["name"] for s in agent["skills"]] == ["receipt-check", "claim-policy"]
    assert agent["skill_toolsets"][0]["discovery_mode"] == "LAZY"
    assert payload["discovered"]["coverage"]["skills_never_activated"] == []
    assert "EVS073" not in finding_ids(payload) and "EVS036" not in finding_ids(payload)


def test_evs073_needs_cases_and_an_unactivated_skill(run_script, tmp_agent):
    agent = tmp_agent("skilled_agent")
    doc = json.loads((agent / "activation.evalset.json").read_text())
    doc["eval_cases"] = [doc["eval_cases"][0]]          # receipt-check is never loaded
    (agent / "activation.evalset.json").write_text(json.dumps(doc))
    payload = json.loads(run_script(DISCOVER, agent, "--no-probe", "--json").stdout)
    found = [f for f in payload["findings"] if f["id"] == "EVS073"]
    assert found and "receipt-check" in found[0]["message"]
    matrix = json.loads(run_script("skills/eval-review/scripts/coverage_matrix.py", agent,
                                   "--json").stdout)
    assert "EVS073" in finding_ids(matrix)

    (agent / "activation.evalset.json").unlink()
    empty = json.loads(run_script(DISCOVER, agent, "--no-probe", "--json").stdout)
    assert "EVS073" not in finding_ids(empty), "no cases: GAP-EVALSET says it already"


def test_discover_on_a_plugin_layer_is_a_note_not_an_error(run_script, fixtures):
    result = run_script(DISCOVER, fixtures / "plugins" / "retail-like", "--no-probe", "--json")
    payload = json.loads(result.stdout)
    assert "ENV014" in finding_ids(payload) and "ENV011" not in finding_ids(payload)
    layer = payload["discovered"]["plugin_layer"]
    assert layer["cases"] == 2 and layer["threshold_declared"] is False
    assert payload["options"][0]["label"] == "/adk:eval-review"


def test_inventory_lists_the_plugin_layer(run_script, fixtures):
    result = run_script("skills/adk/scripts/inventory.py", fixtures / "plugins" / "retail-like",
                        "--json")
    payload = json.loads(result.stdout)
    assert payload["plugin_layer"]["eval_yamls"][0]["rubric_lines"] == 4
    md = run_script("skills/adk/scripts/inventory.py", fixtures / "plugins" / "retail-like")
    assert "## Agent-Plugins skill layer" in md.stdout


@pytest.mark.skipif(not UPSTREAM.is_dir(), reason="no upstream samples checkout")
@pytest.mark.parametrize("sample", [
    "environment_and_skills/skills", "environment_and_skills/skills_agent",
    "environment_and_skills/skills_inject_state", "environment_and_skills/local_env_skill_toolset",
    "environment_and_skills/skills_agent_gcs", "integrations/gcp_skill_registry_agent",
])
def test_upstream_skill_samples_lint_without_agt070(run_script, sample):
    payload = json.loads(run_script(LINT, UPSTREAM / sample, "--no-backend", "--json").stdout)
    assert "AGT070" not in finding_ids(payload)
    assert payload["discovered"]["skills"], "the skills are in the facts"


# -- EVAL.yaml -----------------------------------------------------------------

def test_validate_eval_yaml_exit_codes(run_script, fixtures, tmp_path):
    clean = run_script("skills/eval-review/scripts/validate_eval_yaml.py",
                       fixtures / "plugins" / "retail-like", "--json")
    assert clean.returncode == 0 and json.loads(clean.stdout)["discovered"]["cases"] == 2
    bad = tmp_path / "EVAL.yaml"
    bad.write_text("cases:\n- id: a\n")
    broken = run_script("skills/eval-review/scripts/validate_eval_yaml.py", bad, "--json")
    assert broken.returncode == 1 and "EVY004" in finding_ids(json.loads(broken.stdout))
    warn = tmp_path / "w" / "EVAL.yaml"
    warn.parent.mkdir()
    warn.write_text("cases:\n- id: a\n  prompt: p\n  rubric: [Fine.]\n")
    assert run_script("skills/eval-review/scripts/validate_eval_yaml.py", warn).returncode == 0
    assert run_script("skills/eval-review/scripts/validate_eval_yaml.py", warn,
                      "--strict").returncode == 1
    empty = tmp_path / "none"
    empty.mkdir()
    assert run_script("skills/eval-review/scripts/validate_eval_yaml.py", empty).returncode == 2


@pytest.mark.skipif(not RECIPES_RETAIL.is_dir(), reason="no adk-recipes checkout")
def test_validate_eval_yaml_on_the_recipes_has_no_errors(run_script):
    result = run_script("skills/eval-review/scripts/validate_eval_yaml.py", RECIPES_RETAIL)
    assert result.returncode == 0, result.stdout


# -- evalsets: activation, EVS074, rubric versions -----------------------------

def test_gen_cases_expands_a_skill_into_load_skill(run_script, tmp_agent):
    agent = tmp_agent("skilled_agent")
    spec = [{"eval_id": "activation_receipt_check_loads",
             "turns": [{"user": "Claim CLM-000420 for 30 dollars of meals on sales.",
                        "skill": "receipt-check",
                        "tools": [{"skill": "claim-policy"},
                                  {"name": "check_cost_centre_funds",
                                   "args": {"cost_centre": "sales", "amount_usd": 30.0}}]}]}]
    result = run_script("skills/eval-create/scripts/gen_cases.py", agent, "--spec",
                        json.dumps(spec), "--evalset", agent / "activation.evalset.json", "--json")
    payload = json.loads(result.stdout)
    assert result.returncode == 0, result.stdout
    doc = json.loads((agent / "activation.evalset.json").read_text())
    tools = doc["eval_cases"][-1]["conversation"][0]["intermediate_data"]["tool_uses"]
    assert tools[:2] == [{"name": "load_skill", "args": {"skill_name": "receipt-check"}},
                         {"name": "load_skill", "args": {"skill_name": "claim-policy"}}]
    ids = [f["id"] for f in payload["validation"]["findings"]]
    assert "EVS036" not in ids and "EVS074" not in ids


def test_gen_cases_warns_on_an_unregistered_skill(run_script, tmp_agent):
    agent = tmp_agent("skilled_agent")
    spec = [{"eval_id": "activation_typo", "turns": [{"user": "Claim it.",
                                                      "tools": [{"skill": "claims-policy"}]}]}]
    result = run_script("skills/eval-create/scripts/gen_cases.py", agent, "--spec",
                        json.dumps(spec), "--new", "typo", "--json", "--dry-run")
    ids = [f["id"] for f in json.loads(result.stdout)["validation"]["findings"]]
    assert "EVS074" in ids


def test_validate_evalset_passes_the_runner_version_to_the_rubric_check(run_script, tmp_path):
    doc = {"eval_set_id": "r", "eval_cases": [{
        "eval_id": "happy_x",
        "conversation": [{"invocation_id": "i", "user_content": {"parts": [{"text": "hi"}]},
                          "final_response": {"parts": [{"text": "ok"}]}}],
        "session_input": {"app_name": "app", "user_id": "u"},
        "rubrics": [{"rubric_id": "r1", "rubric_content": {}, "type": "FINAL_RESPONSE_QUALITY"}],
    }]}
    path = tmp_path / "r.evalset.json"
    path.write_text(json.dumps(doc))
    old = run_script("skills/eval-review/scripts/validate_evalset.py", path, "--json",
                     "--adk-version", "2.11.0")
    new = run_script("skills/eval-review/scripts/validate_evalset.py", path, "--json",
                     "--adk-version", "2.12.0")
    assert old.returncode == 1 and new.returncode == 0
    assert "EVS082" in finding_ids(json.loads(new.stdout))


def test_the_activation_template_is_a_valid_evalset_skeleton(plugin_root):
    from adk_skills.evalset import LoadedEvalSet, validate_evalset

    path = plugin_root / "skills/eval-create/assets/templates/evalsets/activation.evalset.json"
    doc = json.loads(path.read_text())
    tools = doc["eval_cases"][0]["conversation"][0]["intermediate_data"]["tool_uses"]
    assert tools[0] == {"name": "load_skill", "args": {"skill_name": "REPLACE-skill-name"}}
    report = validate_evalset(LoadedEvalSet(str(path), doc, doc, "evalset"))
    assert not report.has_errors()


@pytest.mark.parametrize("version", ["2.9.2", "2.12", "2.13"])
def test_the_activation_template_validates_under_adk(plugin_root, version):
    python = need_python(version)
    path = plugin_root / "skills/eval-create/assets/templates/evalsets/activation.evalset.json"
    proc = subprocess.run([str(python), "-I", "-c",
                           "import sys, pathlib; from google.adk.evaluation.eval_set import EvalSet; "
                           "EvalSet.model_validate_json(pathlib.Path(sys.argv[1]).read_text())",
                           str(path)], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-800:]


# -- merge gates ---------------------------------------------------------------

def compare(run_script, *args):
    return run_script("skills/eval-analyze/scripts/compare_runs.py", *args)


def test_gates_hold_by_default_and_are_listed(run_script, fixtures):
    result = compare(run_script, fixtures / EFFICIENCY, fixtures / CACHED, "--json")
    payload = json.loads(result.stdout)
    assert result.returncode == 0 and payload["gates_passed"] is True
    assert payload["gates"][0]["gate"] == "max_regressions" and payload["gates"][0]["limit"] == 0


def test_min_pass_rate_fails_below_the_floor(run_script, fixtures, tmp_path):
    candidate = tmp_path / "app_efficiency_cached_1790000200.0.evalset_result.json"
    doc = json.loads((fixtures / CACHED).read_text())
    doc["eval_case_results"][-1]["final_eval_status"] = 2      # 1 of 2 now passes
    candidate.write_text(json.dumps(doc))
    result = compare(run_script, fixtures / CACHED, candidate, "--min-pass-rate", "0.95",
                     "--max-regressions", "5", "--json")
    payload = json.loads(result.stdout)
    assert result.returncode == 1
    gate = next(g for g in payload["gates"] if g["gate"] == "min_pass_rate")
    assert gate["passed"] is False and gate["value"] == 0.5
    assert next(g for g in payload["gates"] if g["gate"] == "max_regressions")["passed"] is True
    md = compare(run_script, fixtures / CACHED, candidate, "--min-pass-rate", "0.95")
    assert "FAIL pass rate 50.0% (floor 95.0%)" in md.stdout


def test_max_regressions_tolerates_up_to_n_flips(run_script, fixtures, tmp_path):
    candidate = tmp_path / "app_efficiency_1790000200.0.evalset_result.json"
    doc = json.loads((fixtures / EFFICIENCY).read_text())
    doc["eval_case_results"][0]["final_eval_status"] = 2
    candidate.write_text(json.dumps(doc))
    assert compare(run_script, fixtures / EFFICIENCY, candidate).returncode == 1
    assert compare(run_script, fixtures / EFFICIENCY, candidate,
                   "--max-regressions", "1").returncode == 0


def test_budget_flags_fail_on_the_candidate_and_ignore_na(run_script, fixtures, tmp_path):
    payload = json.loads(compare(run_script, fixtures / EFFICIENCY, fixtures / CACHED,
                                 "--json").stdout)
    prompt = payload["candidate"]["figures"]["prompt_tokens"]
    tools = payload["candidate"]["figures"]["tool_calls"]
    over = compare(run_script, fixtures / EFFICIENCY, fixtures / CACHED,
                   "--max-mean-prompt-tokens", str(prompt - 1), "--json")
    assert over.returncode == 1
    assert compare(run_script, fixtures / EFFICIENCY, fixtures / CACHED,
                   "--max-mean-prompt-tokens", str(prompt), "--max-mean-tool-calls",
                   str(tools)).returncode == 0
    assert compare(run_script, fixtures / EFFICIENCY, fixtures / CACHED,
                   "--max-mean-seconds", "0.001").returncode == 1

    no_efficiency = tmp_path / "app_plain_1790000300.0.evalset_result.json"
    doc = json.loads((fixtures / EFFICIENCY).read_text())
    for case in doc["eval_case_results"]:
        case["overall_eval_metric_results"] = [
            m for m in case["overall_eval_metric_results"] if m.get("eval_status") != 4]
    no_efficiency.write_text(json.dumps(doc))
    na = compare(run_script, no_efficiency, no_efficiency, "--max-mean-seconds", "0.001",
                 "--json")
    gate = next(g for g in json.loads(na.stdout)["gates"] if g["gate"] == "max_mean_seconds")
    assert na.returncode == 0 and gate["checked"] is False, "n/a never fails"


def test_gate_flags_are_validated(run_script, fixtures):
    assert compare(run_script, fixtures / EFFICIENCY, fixtures / CACHED,
                   "--min-pass-rate", "95").returncode == 2
    assert compare(run_script, fixtures / EFFICIENCY, fixtures / CACHED,
                   "--max-regressions", "-1").returncode == 2


def test_run_evals_gates_on_a_global_pass_rate(run_script, agent_dir, fake_adk, monkeypatch,
                                               tmp_agent):
    agent = tmp_agent("recipes_good")
    monkeypatch.setenv("FAKE_ADK_EVAL_SET", "happy")
    monkeypatch.setenv("FAKE_ADK_PASSED", "19")
    monkeypatch.setenv("FAKE_ADK_FAILED", "1")
    evalset = agent / "tests/eval/evalsets/happy.evalset.json"
    strict = run_script("skills/eval-run/scripts/run_evals.py", agent, "--evalset", evalset,
                        "--json")
    assert strict.returncode == 1
    gated = run_script("skills/eval-run/scripts/run_evals.py", agent, "--evalset", evalset,
                       "--min-pass-rate", "0.95", "--json")
    payload = json.loads(gated.stdout)
    assert gated.returncode == 0 and payload["pass_rate"] == 0.95
    above = run_script("skills/eval-run/scripts/run_evals.py", agent, "--evalset", evalset,
                       "--min-pass-rate", "0.96")
    assert above.returncode == 1 and "FAIL" in above.stdout
    dry = run_script("skills/eval-run/scripts/run_evals.py", agent, "--dry-run")
    assert "exits 1 on a failure from 2.13.0" in dry.stderr
    assert run_script("skills/eval-run/scripts/run_evals.py", agent,
                      "--min-pass-rate", "2").returncode == 2


# -- frozen suites -------------------------------------------------------------

@pytest.fixture
def frozen_project(tmp_agent, fixtures):
    project = tmp_agent("recipes_good")
    shutil.copy(fixtures / "registry/frozen_suite/eval_registry.json", project)
    return project


@pytest.mark.parametrize("command", [
    ["remove", "happy_travel_within_policy"],
    ["rename", "happy_travel_within_policy", "happy_travel_renamed"],
    ["move", "happy_travel_within_policy", "--to", "OTHER"],
    ["rebase-app-name", "renamed_app"],
])
def test_a_frozen_suite_refuses_edits(run_script, frozen_project, command):
    evalset = frozen_project / "tests/eval/evalsets/happy.evalset.json"
    before = evalset.read_text()
    args = [str(frozen_project / "tests/eval/evalsets/other.evalset.json") if a == "OTHER" else a
            for a in command]
    result = run_script("skills/eval-create/scripts/manage_cases.py", *args, evalset)
    assert result.returncode == 2, result.combined
    assert "EVS120" in result.stderr and "golden" in result.stderr
    assert evalset.read_text() == before


def test_unfreeze_allows_a_deliberate_revision(run_script, frozen_project):
    evalset = frozen_project / "tests/eval/evalsets/happy.evalset.json"
    result = run_script("skills/eval-create/scripts/manage_cases.py", "remove",
                        "happy_travel_within_policy", evalset, "--unfreeze")
    assert result.returncode in (0, 1), result.combined
    assert "overridden by --unfreeze" in result.stderr
    assert "happy_travel_within_policy" not in evalset.read_text()


def test_a_suite_that_is_not_frozen_is_editable(run_script, frozen_project):
    evalset = frozen_project / "tests/eval/evalsets/edge.evalset.json"
    case = json.loads(evalset.read_text())["eval_cases"][0]["eval_id"]
    result = run_script("skills/eval-create/scripts/manage_cases.py", "rename", case,
                        f"{case}_v2", evalset, "--dry-run")
    assert result.returncode == 0, result.combined


def test_adding_a_case_to_a_frozen_suite_is_allowed(run_script, frozen_project):
    evalset = frozen_project / "tests/eval/evalsets/happy.evalset.json"
    spec = [{"eval_id": "happy_meals_within_policy",
             "turns": [{"user": "Submit CLM-000600 for 40 dollars of meals on sales.",
                        "tools": [{"name": "lookup_policy", "args": {"req": {"category": "meals"}}}]}]}]
    result = run_script("skills/eval-create/scripts/gen_cases.py", frozen_project, "--spec",
                        json.dumps(spec), "--evalset", evalset)
    assert result.returncode == 0, result.combined
    assert "happy_meals_within_policy" in evalset.read_text()


def test_registry_init_writes_frozen_false(run_script, tmp_agent):
    project = tmp_agent("recipes_good")
    result = run_script("skills/eval-run/scripts/run_registry.py", "--init", "--root", project,
                        "--json")
    manifest = json.loads(result.stdout)["manifest"]
    assert all(suite["frozen"] is False for suite in manifest["suites"])


def test_the_registry_template_declares_frozen(plugin_root):
    template = json.loads((plugin_root / "skills/eval-run/assets/templates/eval_registry.json")
                          .read_text())
    assert [s["frozen"] for s in template["suites"]] == [True, False]


# -- adk run --state_file ------------------------------------------------------

def test_run_jsonl_passes_a_state_file_on_2_12(run_script, agent_dir, fake_adk, tmp_path,
                                              monkeypatch):
    agent = shutil.copytree(agent_dir("recipes_good"), tmp_path / "proj")
    (agent / ".env").write_text("GOOGLE_API_KEY=fake\n")
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"claimant_name": "Ada"}))
    result = run_script("skills/agent-debug/scripts/run_jsonl.py", agent, "Claim it",
                        "--state-file", state, "--out", tmp_path / "trace.jsonl", "--json")
    assert result.returncode == 0, result.combined
    events = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    assert events[0]["actions"]["stateDelta"] == {"claimant_name": "Ada"}

    monkeypatch.setenv("FAKE_ADK_VERSION", "2.11.0")
    refused = run_script("skills/agent-debug/scripts/run_jsonl.py", agent, "Claim it",
                         "--state-file", state)
    assert refused.returncode == 2 and "2.12.0" in refused.stderr


def test_run_jsonl_state_file_dry_run_and_validation(run_script, agent_dir, tmp_path):
    state = tmp_path / "state.json"
    state.write_text("{}")
    dry = run_script("skills/agent-debug/scripts/run_jsonl.py", agent_dir("recipes_good"),
                     "x", "--state-file", state, "--dry-run", "--json")
    payload = json.loads(dry.stdout)
    assert "--state_file" in payload["command"] and payload["state_file_needs"] == "ADK 2.12+"
    state.write_text("[1]")
    assert run_script("skills/agent-debug/scripts/run_jsonl.py", agent_dir("recipes_good"),
                      "x", "--state-file", state, "--dry-run").returncode == 2


# -- behavioural tests ---------------------------------------------------------

def test_gen_test_eval_behavioral_derives_the_three_behaviours(run_script, tmp_agent):
    agent = tmp_agent("skilled_agent")
    result = run_script("skills/eval-run/scripts/gen_test_eval.py", agent, "--behavioral",
                        "--json")
    payload = json.loads(result.stdout)
    assert result.returncode == 0, result.combined
    behaviors = payload["behaviors"]
    assert behaviors["expected"]["tool"] == "load_skill" and behaviors["expected"]["from_evalset"]
    assert behaviors["missing"]["tool"] == "submit_claim"
    assert behaviors["missing"]["prompt"].startswith("Please submit claim CLM-000414")
    out = Path(payload["out"])
    assert out.name == "test_behaviors.py"
    source = out.read_text()
    ast.parse(source)
    assert "{{" not in source
    assert '"check_cost_centre_funds"' in source and "ADK_EVAL_MIN_PASS_RATE" in source


def test_gen_test_eval_behavioral_needs_no_config_and_marks_todos(run_script, tmp_agent):
    agent = tmp_agent("flat_good")
    result = run_script("skills/eval-run/scripts/gen_test_eval.py", agent, "--behavioral",
                        "--json")
    payload = json.loads(result.stdout)
    assert result.returncode == 0, result.combined
    assert payload["behaviors"]["expected"]["prompt"].startswith("TODO")
    assert payload["behaviors"]["expected"]["tool"] == "lookup_policy"
    ast.parse(Path(payload["out"]).read_text())


def test_gen_test_eval_behavioral_uses_the_happy_and_edge_cases(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    payload = json.loads(run_script("skills/eval-run/scripts/gen_test_eval.py", agent,
                                    "--behavioral", "--json").stdout)
    behaviors = payload["behaviors"]
    assert behaviors["expected"]["tool"] == "lookup_policy"
    assert behaviors["forbidden"]["tool"] == "submit_claim"
    assert behaviors["forbidden"]["from_evalset"] is True


@pytest.mark.parametrize("version", ["2.9.2", "2.12", "2.13"])
def test_the_behaviour_file_imports_under_adk(run_script, tmp_agent, version):
    """Without credentials the module skips itself after its ADK imports; with a
    dummy key it imports fully, including the agent through the Runner."""
    python = need_python(version)
    agent = tmp_agent("skilled_agent")
    out = Path(json.loads(run_script("skills/eval-run/scripts/gen_test_eval.py", agent,
                                     "--behavioral", "--json").stdout)["out"])
    pytest_path = Path(__import__("pytest").__file__).parents[1]
    code = (
        "import importlib.util, sys, os\n"
        "spec = importlib.util.spec_from_file_location('tb', sys.argv[1])\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "sys.modules['tb'] = mod\n"
        "try:\n"
        "    spec.loader.exec_module(mod)\n"
        "except BaseException as exc:\n"
        "    print('SKIPPED' if type(exc).__name__ == 'Skipped' else repr(exc)); raise SystemExit(0)\n"
        "runner = mod._load_runner()\n"
        "print('LOADED', runner.app_name, len(mod.BEHAVIORS))\n"
    )
    env = {k: v for k, v in os.environ.items()
           if k not in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI")}
    env["PYTHONPATH"] = str(pytest_path)
    skipped = subprocess.run([str(python), "-c", code, str(out)], capture_output=True, text=True,
                             env=env, timeout=300)
    assert "SKIPPED" in skipped.stdout, skipped.stdout + skipped.stderr[-600:]
    env["GOOGLE_API_KEY"] = "dummy-not-used"
    loaded = subprocess.run([str(python), "-c", code, str(out)], capture_output=True, text=True,
                            env=env, timeout=300)
    assert "LOADED skilled_agent 4" in loaded.stdout, loaded.stdout + loaded.stderr[-800:]


def test_run_pytest_eval_behavioral_targets_the_file_and_sets_the_gate(
        run_script, tmp_agent, fake_adk):
    agent = tmp_agent("recipes_good")
    missing = run_script("skills/eval-run/scripts/run_pytest_eval.py", agent, "--behavioral")
    assert missing.returncode == 2 and "gen_test_eval.py --behavioral" in missing.stderr
    run_script("skills/eval-run/scripts/gen_test_eval.py", agent, "--behavioral")
    result = run_script("skills/eval-run/scripts/run_pytest_eval.py", agent, "--behavioral",
                        "--runs", "5", "--min-pass-rate", "0.8")
    assert result.returncode == 0, result.combined
    echoed = json.loads(next(line for line in result.stdout.splitlines() if line.startswith("{")))
    assert echoed["pytest_args"][0].endswith("test_behaviors.py")
    assert echoed["env"]["ADK_EVAL_RUNS"] == "5" and echoed["env"]["ADK_EVAL_MIN_PASS_RATE"] == "0.8"

    plain = run_script("skills/eval-run/scripts/run_pytest_eval.py", agent)
    echoed = json.loads(next(line for line in plain.stdout.splitlines() if line.startswith("{")))
    assert any(a.startswith("--ignore=") and a.endswith("test_behaviors.py")
               for a in echoed["pytest_args"])
    assert run_script("skills/eval-run/scripts/run_pytest_eval.py", agent,
                      "--runs", "3").returncode == 2
