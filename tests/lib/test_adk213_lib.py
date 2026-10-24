"""Library behaviour for the ADK 2.13 sync: the target constant, version pins
read from project files, and the rules whose verdict depends on 2.13."""

from __future__ import annotations

import json

import pytest

from adk_skills import ADK_TARGET
from adk_skills.env import ADK_REQUIREMENT, pinned_adk_version, version_at_least


def test_the_target_release_is_2_13():
    assert ADK_TARGET == "2.13"
    assert ADK_REQUIREMENT == "google-adk[eval]==2.13.*"


def test_the_fake_runner_reports_the_target(fixtures):
    text = (fixtures / "bin" / "fake_adk.py").read_text()
    assert '"FAKE_ADK_VERSION", "2.13.0"' in text


def test_the_recipes_scaffold_pins_the_target(plugin_root):
    recipes = plugin_root / "skills/agent-create/assets/templates/recipes"
    for name in ("pyproject.toml.tmpl", "README.md.tmpl"):
        text = (recipes / name).read_text()
        assert "google-adk[eval]>=2.13,<3" in text
        assert pinned_adk_version(text) == "2.13"


@pytest.mark.parametrize("text, expected", [
    ('dependencies = ["google-adk[eval]>=2.13,<3"]', "2.13"),
    ('uv run --with "google-adk[eval]==2.13.*" adk eval app x.evalset.json', "2.13"),
    ("google-adk==2.13.0\n", "2.13.0"),
    ("google-adk[eval]~=2.12.0\n", "2.12.0"),
    ('google-adk = "^2.13"', "2.13"),
    ("google_adk>=2.9", "2.9"),
    ("google-adk\n", None),
    ("google-adk<3\n", None),
    ("google-adk-community>=0.4\n", None),
    ("", None),
])
def test_pinned_versions_are_read_from_every_form(text, expected):
    assert pinned_adk_version(text) == expected


def test_the_lowest_pin_wins_across_files():
    assert pinned_adk_version("google-adk>=2.13", "google-adk[eval]==2.9.*") == "2.9"
    assert pinned_adk_version("google-adk>=2.13", "requests>=2") == "2.13"
    assert version_at_least(pinned_adk_version("google-adk>=2.13"), "2.13")
    assert not version_at_least(pinned_adk_version("nothing here"), "2.13")


# -- multi_run_config (CFG072, CFG073) -------------------------------------------

from adk_skills.coverage import seeded_state_cases, shape, summarize_cases  # noqa: E402
from adk_skills.eval_config import LoadedConfig, validate_config  # noqa: E402
from adk_skills.evalset import LoadedEvalSet, case_category, stats, validate_evalset  # noqa: E402
from adk_skills.results import parse_result  # noqa: E402
from adk_skills.tools_ast import (  # noqa: E402
    agent_model_ids, collect_state_keys, extract_module, instruction_placeholders,
    state_placeholder_findings,
)


def _config(doc):
    return validate_config(LoadedConfig("cfg.json", doc), adk_version="2.13.0")


def _ids(report, fid=None):
    return [f for f in report.findings if fid is None or f.id == fid]


CRITERIA = {"tool_trajectory_avg_score": 1.0, "response_match_score": 0.8}


def test_multi_run_config_is_an_info_not_an_unknown_key():
    report = _config({"criteria": CRITERIA, "multi_run_config": {
        "num_runs": 3,
        "max_crashed_runs": 1,
        "cross_run_scoring": "mean_of_runs",
        "cross_run_scoring_by_metric": {
            "response_match_score": "min_over_runs",
            "tool_trajectory_avg_score": {"method": "pass_rate", "threshold": 0.9},
        },
        "cross_run_criteria": [{"method": "pass_rate", "threshold": 0.8}],
        "custom_methods": {"p25": {"code_config": {"name": "pkg.methods.p25"},
                                   "uses_scores": True}},
    }})
    [info] = _ids(report, "CFG072")
    assert info.severity == "info"
    assert info.message == ("multi_run_config is read only by ADK main after 2.13.0 "
                            "(unreleased); every released version drops it silently, so it "
                            "gates nothing yet")
    assert not _ids(report, "CFG073") and not _ids(report, "CFG004")


@pytest.mark.parametrize("multi_run, needle", [
    ({"num_runs": 0}, "num_runs"),
    ({"num_runs": "3"}, "num_runs"),
    ({"max_crashed_runs": -1}, "max_crashed_runs"),
    ({"cross_run_scoring": "pass_rate"}, "needs a threshold"),
    ({"cross_run_scoring": "median"}, "unknown cross-run method"),
    ({"cross_run_scoring": {"method": "mean_of_runs", "threshold": 0.5}}, "combines scores"),
    ({"cross_run_scoring": {"method": "pass_rate", "threshold": 1.5}}, "between 0 and 1"),
    ({"cross_run_scoring": {"method": "pass_rate"}}, "between 0 and 1"),
    ({"cross_run_scoring_by_metric": {"safety_v1": "mean_of_runs"}}, "not in `criteria`"),
    ({"cross_run_criteria": {"method": "pass_rate", "threshold": 0.8}}, "list of rule"),
    ({"cross_run_criteria": ["pass_rate"]}, "must be an object"),
    ({"custom_methods": {"p25": {"uses_scores": True}}}, "code_config.name"),
    ({"custom_methods": {"p25": {"code_config": {"name": "a.b"}, "uses_scores": "yes"}}},
     "boolean `uses_scores`"),
    ({"custom_methods": {"pass_rate": {"code_config": {"name": "a.b"}, "uses_scores": False}}},
     "built-in"),
    ({"num_run": 3}, "unrecognized"),
    ("three", "must be an object"),
])
def test_multi_run_shape_errors(multi_run, needle):
    report = _config({"criteria": CRITERIA, "multi_run_config": multi_run})
    errors = _ids(report, "CFG073")
    assert errors and all(f.severity == "error" for f in errors)
    assert any(needle in f.message for f in errors), [f.message for f in errors]


def test_a_declared_custom_method_is_accepted_by_its_kind():
    custom = {"share": {"code_config": {"name": "pkg.m.share"}, "uses_scores": False}}
    ok = _config({"criteria": CRITERIA, "multi_run_config": {
        "custom_methods": custom, "cross_run_criteria": [{"method": "share", "threshold": 0.5}]}})
    assert not _ids(ok, "CFG073")
    bare = _config({"criteria": CRITERIA, "multi_run_config": {
        "custom_methods": custom, "cross_run_scoring": "share"}})
    assert any("needs a threshold" in f.message for f in _ids(bare, "CFG073"))


# -- live and audio (SIM032-SIM034) --------------------------------------------------

AUDIO = {"type": "llm_audio", "model": "gemini-flash-latest", "max_allowed_invocations": 6}


@pytest.mark.parametrize("timeout", [0, -5, "300", None])
def test_a_non_positive_live_timeout_is_a_warning(timeout):
    report = _config({"criteria": {"hallucinations_v1": 0.5},
                      "live_model_config": {"timeout_seconds": timeout}})
    [finding] = _ids(report, "SIM032")
    assert finding.severity == "warn"
    assert "ADK accepts it; plugin policy: a non-positive timeout is not a meaningful live run" \
        in finding.message


def test_a_positive_live_timeout_is_fine():
    report = _config({"criteria": {"hallucinations_v1": 0.5},
                      "live_model_config": {"timeout_seconds": 300}})
    assert not _ids(report, "SIM032")


def test_live_config_with_a_text_model_agent_is_a_warning():
    doc = {"criteria": {"hallucinations_v1": 0.5}, "live_model_config": {}}
    text = validate_config(LoadedConfig("c", doc), agent_models=["gemini-flash-latest"])
    [finding] = _ids(text, "SIM033")
    assert finding.severity == "warn" and "gemini-flash-latest" in finding.message
    for live in ("gemini-live-2.5-flash-preview", "gemini-2.5-flash-native-audio-preview"):
        assert not _ids(validate_config(LoadedConfig("c", doc), agent_models=[live]), "SIM033")
    assert not _ids(validate_config(LoadedConfig("c", doc), agent_models=None), "SIM033")


@pytest.mark.parametrize("simulator, unknown", [
    ({**AUDIO, "audio_modle": "cloud_tts"}, "audio_modle"),
    ({"type": "llm_backed", "model": "m", "max_allowed_invocations": 4, "audio_model": "x"},
     "audio_model"),
    ({"model": "m", "max_allowed_invocations": 4, "max_turns": 3}, "max_turns"),
])
def test_unknown_simulator_keys_are_warned(simulator, unknown):
    report = _config({"criteria": {"hallucinations_v1": 0.5}, "user_simulator_config": simulator})
    [finding] = _ids(report, "SIM034")
    assert finding.severity == "warn" and unknown in finding.message
    assert "extra=allow" in finding.message


def test_every_audio_field_is_known():
    simulator = {**AUDIO, "audio_model": "cloud_tts", "include_text_with_audio": True,
                 "audio_model_configuration": {"speech_config": {"language_code": "en-US"}},
                 "model_configuration": {}, "custom_instructions": None,
                 "include_function_calls": False}
    report = _config({"criteria": {"hallucinations_v1": 0.5}, "user_simulator_config": simulator})
    assert not _ids(report, "SIM034")


# -- evalsets: drive-qualified ids (EVS076), seeded state (EVS075), resume ---------

def _evalset(eval_set_id="resume", app_name="app", state=None):
    doc = {"eval_set_id": eval_set_id, "eval_cases": [{
        "eval_id": "resume_receipt_requested_holds",
        "conversation": [{"invocation_id": "i", "user_content": {"parts": [{"text": "hi"}]},
                          "final_response": {"parts": [{"text": "waiting"}]},
                          "intermediate_data": {"tool_uses": []}}],
        "session_input": {"app_name": app_name, "user_id": "u", "state": state or {}},
    }]}
    return LoadedEvalSet("x.evalset.json", doc, doc, "evalset")


@pytest.mark.parametrize("version, severity", [("2.13.0", "error"), ("2.13", "error"),
                                               ("2.12.0", "warn"), (None, "warn")])
def test_a_drive_qualified_eval_set_id_depends_on_the_version(version, severity):
    report = validate_evalset(_evalset(eval_set_id="c:pass"), adk_version=version)
    [finding] = _ids(report, "EVS076")
    assert finding.severity == severity
    assert "must not be drive-qualified" in finding.message
    assert "when saving results after inference" in finding.message
    assert "EVS003" in finding.fix and "path separators" in finding.fix
    assert _ids(report, "EVS003"), "the colon also breaks the create-time id rule"


def test_a_drive_qualified_app_name_is_reported_and_a_later_colon_is_not():
    report = validate_evalset(_evalset(app_name="d:app"), adk_version="2.13.0")
    [finding] = _ids(report, "EVS076")
    assert finding.severity == "error" and "app_name" in finding.message
    later = validate_evalset(_evalset(app_name="app:v2"), adk_version="2.13.0")
    assert not _ids(later, "EVS076")


def test_a_seeded_key_nothing_reads_is_an_info():
    reads = {"claim_step", "claimant"}.__contains__
    report = validate_evalset(_evalset(state={"claim_step": "x", "claim_stage": "y"}),
                              state_key_read=reads)
    [finding] = _ids(report, "EVS075")
    assert finding.severity == "info" and "'claim_stage'" in finding.message
    assert "'claim_step'" not in finding.message
    assert not _ids(validate_evalset(_evalset(state={"claim_stage": "y"})), "EVS075"), \
        "no agent facts, no claim"


def test_resume_is_a_category_and_seeded_cases_are_counted():
    assert case_category("resume_receipt_missing_holds") == "resume"
    assert case_category("resumed_after_idle") == "resume"
    doc = _evalset(state={"claim_step": "receipt_requested"}).doc
    assert stats(doc)["seeded_state_cases"] == 1
    cases = summarize_cases({"x": doc})
    assert seeded_state_cases(cases) == {"resume_receipt_requested_holds": ["claim_step"]}
    assert shape(cases)["seeded_state_cases"] == 1


# -- results: repeated runs in one file -----------------------------------------------

def _multi_run_file(tmp_path, statuses):
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
            if len(runs) > 1:
                entry["repetition_index"] = len(runs) - 1 - index   # out of file order
            cases.append(entry)
    path = tmp_path / f"app_happy_{len(list(tmp_path.iterdir())) + 1790000000}.evalset_result.json"
    path.write_text(json.dumps({"eval_set_id": "happy", "eval_case_results": cases}))
    return path


def test_repeated_cases_are_labelled_run_k_of_n(tmp_path):
    run = parse_result(_multi_run_file(tmp_path, {"a": [1, 2, 1], "b": [1]}))
    labels = [(c.eval_id, c.repetition_index, c.run_label) for c in run.cases]
    assert labels == [("a", 2, "run 3/3"), ("a", 1, "run 2/3"), ("a", 0, "run 1/3"),
                      ("b", None, None)]
    assert run.repeated and len(run.case_groups()["a"]) == 3
    assert run.cases[0].to_dict()["run_label"] == "run 3/3"


def test_a_file_without_repeats_is_unchanged(fixtures):
    run = parse_result(fixtures / "eval_history/app_efficiency_1790000000.123456.evalset_result.json")
    assert not run.repeated and all(c.run_label is None for c in run.cases)


# -- agent state keys (AGT080) and duplicate names (AGT015) ----------------------------

def test_placeholders_follow_adks_own_rules():
    text = "{claim_step} {claimant?} {user:approver} {artifact.receipt} {a.b} {temp:x?} {bad:y}"
    assert instruction_placeholders(text) == [
        ("claim_step", False), ("claimant", True), ("user:approver", False), ("temp:x", True)]


def test_the_stateful_fixture_is_clean_and_the_broken_one_is_not(agent_dir):
    good = extract_module(agent_dir("stateful_agent"))
    assert not state_placeholder_findings(good, [])
    assert not [f for f in good.findings if f.id == "AGT015"]
    keys = collect_state_keys(good.files, good.str_constants)
    assert {"claim_step", "claim_id"} <= set(keys.written)
    assert {"claim_step", "claimant", "claim_id"} <= keys.read

    broken = extract_module(agent_dir("stateful_agent_broken"))
    [finding] = state_placeholder_findings(broken, [])
    assert finding.id == "AGT080" and finding.severity == "warn"
    assert finding.message.startswith("placeholder `{approver}` is never written to state: ADK "
                                      "raises KeyError")
    assert finding.message.endswith("write it from a tool/callback, seed it, or use `{approver?}`")
    [clash] = [f for f in broken.findings if f.id == "AGT015"]
    assert "'checker'" in clash.message and "cousins" in clash.message
    assert "find_agent()" in clash.fix and "2.13" in clash.fix


def _project(tmp_path, source, **files):
    (tmp_path / "agent.py").write_text(source)
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    return extract_module(tmp_path)


AGENT_SRC = '''
from google.adk.agents import Agent
root_agent = Agent(name="a", model="gemini-flash-latest", instruction="Step {claim_step}.")
'''


def test_a_seed_or_an_initial_state_file_counts_as_a_write(tmp_path):
    facts = _project(tmp_path, AGENT_SRC)
    assert [f.id for f in state_placeholder_findings(facts, [])] == ["AGT080"]
    seeded = {"eval_cases": [{"session_input": {"state": {"claim_step": "new"}}}]}
    assert not state_placeholder_findings(facts, [seeded])
    (tmp_path / "initial_state.json").write_text('{"claim_step": "new"}')
    assert not state_placeholder_findings(facts, [], tmp_path)


@pytest.mark.parametrize("writer", [
    'other = Agent(name="b", model="m", instruction="x", output_key="claim_step")',
    'def cb(callback_context):\n    callback_context.state.update({"claim_step": 1})',
    'def tool(tool_context):\n    tool_context.state.setdefault("claim_step", 1)',
    'from google.adk.events import EventActions\nEventActions(state_delta={"claim_step": 1})',
    'KEY = "claim_step"\ndef tool(tool_context):\n    tool_context.state[KEY] = 1',
])
def test_each_kind_of_write_is_seen(tmp_path, writer):
    facts = _project(tmp_path, AGENT_SRC + writer + "\n")
    assert not state_placeholder_findings(facts, [])


def test_a_computed_key_write_silences_the_rule(tmp_path):
    facts = _project(tmp_path, AGENT_SRC + "def t(tool_context, k):\n    tool_context.state[k] = 1\n")
    assert not state_placeholder_findings(facts, [])


@pytest.mark.parametrize("tree, relation", [
    ("a1 = Agent(name='x', model='m', instruction='i')\n"
     "a2 = Agent(name='x', model='m', instruction='i')\n"
     "root_agent = Agent(name='r', model='m', instruction='i', sub_agents=[a1, a2])\n", "siblings"),
    ("leaf = Agent(name='p', model='m', instruction='i')\n"
     "mid = Agent(name='p', model='m', instruction='i', sub_agents=[leaf])\n"
     "root_agent = Agent(name='r', model='m', instruction='i', sub_agents=[mid])\n",
     "an ancestor and its descendant"),
])
def test_duplicate_names_anywhere_in_the_tree(tmp_path, tree, relation):
    facts = _project(tmp_path, "from google.adk.agents import Agent\n" + tree)
    [finding] = [f for f in facts.findings if f.id == "AGT015"]
    assert relation in finding.message and finding.severity == "warn"


def test_agent_model_ids_resolve_env_models(agent_dir):
    facts = extract_module(agent_dir("stateful_agent"))
    assert agent_model_ids(facts) == ["gemini-flash-latest"]
    assert agent_model_ids(facts, "gemini-live-2.5-flash-preview") == ["gemini-live-2.5-flash-preview"]
