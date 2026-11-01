"""Rules added against ADK main through 2026-11-05 (round 10): invocation-level
rubrics that differ between turns (EVS121), shape checks on invocation rubrics
(EVS080-085), rubric ids repeated across the case/invocation merge (EVS122), and
`max_parallel_workers` on an LlmAgent (AGT085)."""

import copy
import json
import textwrap
from pathlib import Path

import pytest

from adk_skills.eval_config import LoadedConfig
from adk_skills.evalset import (
    PER_INVOCATION_RUBRIC_TYPES, effective_rubric_ids, load_evalset, validate_evalset,
)
from adk_skills.tools_ast import extract_module, parallel_worker_findings

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
PER_INVOCATION = FIXTURES / "evalsets" / "per_invocation_rubrics.evalset.json"
BROKEN = FIXTURES / "evalsets" / "per_invocation_rubrics_broken.evalset.json"


def _rubric(rid, rtype="FINAL_RESPONSE_QUALITY", **extra):
    rubric = {"rubric_id": rid, "rubric_content": {"text_property": f"The reply covers {rid}."}}
    if rtype:
        rubric["type"] = rtype
    rubric.update(extra)
    return rubric


def _invocation(n, rubrics=None):
    inv = {
        "invocation_id": f"inv_{n}",
        "user_content": {"parts": [{"text": f"Question {n} about claim CLM-000601."}], "role": "user"},
        "final_response": {"parts": [{"text": f"Answer {n}."}], "role": "model"},
    }
    if rubrics is not None:
        inv["rubrics"] = rubrics
    return inv


def _validate(tmp_path, cases, **kwargs):
    doc = {"eval_set_id": "claims", "eval_cases": [
        {"eval_id": f"memory_claim_{i}", "session_input": {"app_name": "app", "user_id": "u"},
         **case} for i, case in enumerate(cases)]}
    path = tmp_path / "claims.evalset.json"
    path.write_text(json.dumps(doc))
    return validate_evalset(load_evalset(path), **kwargs).findings


def _ids(findings, fid):
    return [f for f in findings if f.id == fid]


# -- EVS121 -------------------------------------------------------------------

def test_effective_list_keeps_case_and_invocation_rubrics_of_the_type():
    case = [_rubric("polite"), _rubric("untyped", None)]
    inv = [_rubric("amount"), _rubric("tool", "TOOL_USE_QUALITY")]
    assert effective_rubric_ids(case, inv, "FINAL_RESPONSE_QUALITY") == {"polite", "amount"}
    assert effective_rubric_ids(case, inv, "TOOL_USE_QUALITY") == {"tool"}


def test_trajectory_rubrics_are_not_per_invocation():
    assert PER_INVOCATION_RUBRIC_TYPES == ("FINAL_RESPONSE_QUALITY", "TOOL_USE_QUALITY")


@pytest.mark.parametrize("version", [None, "2.13.0", "2.9.2"])
def test_fixture_warns_through_213_and_unprobed(version):
    findings = validate_evalset(load_evalset(PER_INVOCATION), adk_version=version).findings
    [finding] = _ids(findings, "EVS121")
    assert finding.severity == "warn"
    assert "Rubric <text> not found in the rubrics provided to the metric." in finding.message
    assert "e4f6c3a9c" in finding.message and "'amount'" in finding.message
    assert "can pass" in finding.message
    assert "case level" in finding.fix
    assert not [f for f in findings if f.severity in ("error", "warn") and f.id != "EVS121"]


@pytest.mark.parametrize("version", ["2.13.1", "2.14.0", "main"])
def test_fixture_is_info_on_a_newer_runner(version):
    [finding] = _ids(validate_evalset(load_evalset(PER_INVOCATION), adk_version=version).findings,
                     "EVS121")
    assert finding.severity == "info"
    assert version in finding.message


def test_identical_lists_are_silent(tmp_path):
    findings = _validate(tmp_path, [{"conversation": [
        _invocation(1, [_rubric("amount")]), _invocation(2, [_rubric("amount")])]}])
    assert not _ids(findings, "EVS121")


def test_case_level_rubrics_only_are_silent(tmp_path):
    findings = _validate(tmp_path, [{"conversation": [_invocation(1), _invocation(2)],
                                     "rubrics": [_rubric("polite")]}])
    assert not _ids(findings, "EVS121")


def test_one_turn_with_rubrics_and_one_without_differs(tmp_path):
    findings = _validate(tmp_path, [{"conversation": [
        _invocation(1), _invocation(2, [_rubric("approver")])]}])
    assert len(_ids(findings, "EVS121")) == 1


def test_untyped_invocation_rubrics_do_not_differ(tmp_path):
    """The memory_recall shape: turn 1 empty, later turns untyped. The judge
    drops untyped rubrics, so the effective lists are equal; EVS083 says why."""
    findings = _validate(tmp_path, [{"conversation": [
        _invocation(1, []), _invocation(2, [_rubric("recall", None)])]}])
    assert not _ids(findings, "EVS121")
    [untyped] = _ids(findings, "EVS083")
    assert "per-invocation" in untyped.message
    assert untyped.locator.endswith(".conversation[1].rubrics[0]")


def test_single_invocation_is_silent(tmp_path):
    findings = _validate(tmp_path, [{"conversation": [_invocation(1, [_rubric("amount")])]}])
    assert not _ids(findings, "EVS121")


def test_config_restricts_the_types(tmp_path):
    cases = [{"conversation": [_invocation(1, [_rubric("t1", "TOOL_USE_QUALITY")]),
                               _invocation(2, [_rubric("t2", "TOOL_USE_QUALITY")])]}]
    assert len(_ids(_validate(tmp_path, cases), "EVS121")) == 1
    final_only = frozenset({"FINAL_RESPONSE_QUALITY"})
    assert not _ids(_validate(tmp_path, cases, rubric_types=final_only), "EVS121")
    assert not _ids(_validate(tmp_path, cases, rubric_types=frozenset()), "EVS121")
    tool = frozenset({"TOOL_USE_QUALITY"})
    [finding] = _ids(_validate(tmp_path, cases, rubric_types=tool), "EVS121")
    assert "TOOL_USE_QUALITY" in finding.message


def test_trajectory_type_differences_are_silent(tmp_path):
    cases = [{"conversation": [_invocation(1, [_rubric("a", "TRAJECTORY_QUALITY")]),
                               _invocation(2, [_rubric("b", "TRAJECTORY_QUALITY")])]}]
    assert not _ids(_validate(tmp_path, cases), "EVS121")


def test_loaded_config_rubric_types():
    config = LoadedConfig("c.json", {"criteria": {
        "rubric_based_final_response_quality_v1": {"threshold": 0.8},
        "rubric_based_multi_turn_trajectory_quality_v1": {"threshold": 0.8},
        "tool_trajectory_avg_score": 1.0}})
    assert config.rubric_types() == {"FINAL_RESPONSE_QUALITY", "TRAJECTORY_QUALITY"}
    assert LoadedConfig("c.json", {"criteria": {"response_match_score": 0.8}}).rubric_types() == set()


# -- EVS080-085 and EVS122 on invocation rubrics --------------------------------

def test_broken_fixture():
    findings = validate_evalset(load_evalset(BROKEN)).findings
    [untyped] = _ids(findings, "EVS083")
    assert "memory_claim_amount_untyped" in untyped.locator
    assert ".conversation[0].rubrics[0]" in untyped.locator
    [repeated] = _ids(findings, "EVS122")
    assert repeated.severity == "error"
    assert "memory_claim_polite_twice" in repeated.locator
    assert "Rubric with rubric_id 'polite' already exists." in repeated.message
    assert "case level and again at conversation[0]" in repeated.message


def test_unknown_key_and_missing_id_on_an_invocation_rubric(tmp_path):
    bad = {"rubric_content": {"text_property": "x"}, "type": "FINAL_RESPONSE_QUALITY",
           "bogus": 1}
    findings = _validate(tmp_path, [{"conversation": [_invocation(1, [bad])]}])
    [unknown] = _ids(findings, "EVS085")
    assert "bogus" in unknown.message and unknown.severity == "error"
    assert unknown.locator.endswith(".conversation[0].rubrics[0]")
    assert _ids(findings, "EVS081")


def test_bad_type_on_an_invocation_rubric(tmp_path):
    findings = _validate(tmp_path, [{"conversation": [_invocation(1, [_rubric("a", "QUALITY")])]}])
    assert _ids(findings, "EVS084")


def test_repeat_within_one_invocation_and_within_case_level(tmp_path):
    findings = _validate(tmp_path, [
        {"conversation": [_invocation(1, [_rubric("a"), _rubric("a")])]},
        {"conversation": [_invocation(1), _invocation(2)],
         "rubrics": [_rubric("p"), _rubric("p")]},
    ])
    repeats = _ids(findings, "EVS122")
    assert len(repeats) == 2
    assert "twice at conversation[0]" in repeats[0].message
    assert "twice at case level" in repeats[1].message
    assert repeats[1].locator.endswith(".rubrics")


def test_distinct_ids_are_silent(tmp_path):
    findings = _validate(tmp_path, [{"conversation": [_invocation(1, [_rubric("amount")])],
                                     "rubrics": [_rubric("polite")]}])
    assert not _ids(findings, "EVS122")


def test_camel_case_invocation_rubrics_are_read(tmp_path):
    doc = {"eval_set_id": "claims", "eval_cases": [{
        "evalId": "memory_claim_camel", "sessionInput": {"appName": "app", "userId": "u"},
        "conversation": [
            {"invocationId": "a", "userContent": {"parts": [{"text": "q"}], "role": "user"},
             "rubrics": [{"rubricId": "x", "rubricContent": {"textProperty": "p"},
                          "type": "FINAL_RESPONSE_QUALITY"}]},
            {"invocationId": "b", "userContent": {"parts": [{"text": "q"}], "role": "user"}}]}]}
    path = tmp_path / "camel.evalset.json"
    path.write_text(json.dumps(doc))
    assert _ids(validate_evalset(load_evalset(path)).findings, "EVS121")


# -- templates, fixtures, upstream ----------------------------------------------

NEW_IDS = {"EVS121", "EVS122"}


def _evalsets(root):
    return sorted(p for p in root.rglob("*") if p.name.endswith((".evalset.json", ".test.json")))


@pytest.mark.parametrize("path", _evalsets(PLUGIN_ROOT / "skills"), ids=lambda p: p.name)
def test_templates_stay_clean(path):
    findings = validate_evalset(load_evalset(path)).findings
    assert not [f for f in findings if f.id in NEW_IDS or ".rubrics[" in (f.locator or "")
                and ".conversation[" in (f.locator or "")]


@pytest.mark.parametrize("path", [p for p in _evalsets(FIXTURES)
                                  if not p.name.startswith("per_invocation_rubrics")],
                         ids=lambda p: str(p.relative_to(FIXTURES)))
def test_existing_fixtures_stay_clean(path):
    findings = validate_evalset(load_evalset(path)).findings
    assert not [f for f in findings if f.id in NEW_IDS]
    assert not [f for f in findings if ".conversation[" in (f.locator or "")
                and ".rubrics[" in (f.locator or "")]


RECIPES = PLUGIN_ROOT.parent / "tmp" / "adk-recipes" / "core" / "python" / "long-horizon-harness"
RECIPE_EVALSETS = RECIPES / "tests" / "eval" / "evalsets"


@pytest.mark.skipif(not RECIPE_EVALSETS.is_dir(), reason="tmp/adk-recipes not checked out")
def test_recipes_long_horizon_harness():
    config_path = RECIPES / "tests" / "eval" / "eval_config.json"
    types = None
    if config_path.is_file():
        from adk_skills.eval_config import load_config
        types = load_config(config_path).rubric_types()
    journey = validate_evalset(load_evalset(RECIPE_EVALSETS / "daily_news_bot_journey.evalset.json"),
                               rubric_types=types).findings
    [hit] = _ids(journey, "EVS121")
    assert "build_daily_news_bot_with_agents_cli" in hit.locator and hit.severity == "warn"
    recall = validate_evalset(load_evalset(RECIPE_EVALSETS / "memory_recall.evalset.json"),
                              rubric_types=types).findings
    assert not _ids(recall, "EVS121")
    assert len({f.locator.split(".")[0] for f in _ids(recall, "EVS083")}) == 8


# -- AGT085 ---------------------------------------------------------------------

def _agent(tmp_path, body):
    (tmp_path / "__init__.py").write_text("from . import agent  # noqa: F401\n")
    (tmp_path / "agent.py").write_text(textwrap.dedent(body))
    return extract_module(tmp_path)


HEADER = """
from google.adk import Agent
from google.adk.workflow import node
"""


@pytest.mark.parametrize("version", [None, "2.13.0", "2.9.2"])
@pytest.mark.parametrize("kwargs", ["parallel_worker=True, max_parallel_workers=2",
                                    "parallel_worker=True, max_parallel_workers=None",
                                    "max_parallel_workers=WORKERS"])
def test_any_value_is_an_error_through_213(tmp_path, version, kwargs):
    facts = _agent(tmp_path, HEADER + f"""
WORKERS = 3
root_agent = Agent(name="review_claim", instruction="Review it.", {kwargs})
""")
    [finding] = parallel_worker_findings(facts, adk_version=version)
    assert finding.id == "AGT085" and finding.severity == "error"
    assert "Extra inputs are not permitted" in finding.message
    assert "5e4f0e18c" in finding.message


@pytest.mark.parametrize("kwargs, expected", [
    ("parallel_worker=True, max_parallel_workers=0",
     "Input should be greater than or equal to 1"),
    ("parallel_worker=True, max_parallel_workers=ZERO",
     "Input should be greater than or equal to 1"),
    ("max_parallel_workers=0", "Input should be greater than or equal to 1"),
    ("max_parallel_workers=2", "max_parallel_workers can only be set when parallel_worker is True"),
    ("parallel_worker=False, max_parallel_workers=2",
     "max_parallel_workers can only be set when parallel_worker is True"),
])
@pytest.mark.parametrize("version", ["2.14.0", "main"])
def test_newer_runner_errors(tmp_path, kwargs, expected, version):
    facts = _agent(tmp_path, HEADER + f"""
ZERO = 0
root_agent = Agent(name="review_claim", instruction="Review it.", {kwargs})
""")
    [finding] = parallel_worker_findings(facts, adk_version=version)
    assert finding.severity == "error" and expected in finding.message
    assert ("can only be set" in finding.message) == ("can only be set" in expected)


@pytest.mark.parametrize("kwargs", ["parallel_worker=True, max_parallel_workers=4",
                                    "parallel_worker=True, max_parallel_workers=None",
                                    "max_parallel_workers=None"])
def test_newer_runner_valid_is_silent(tmp_path, kwargs):
    facts = _agent(tmp_path, HEADER + f"""
root_agent = Agent(name="review_claim", instruction="Review it.", {kwargs})
""")
    assert parallel_worker_findings(facts, adk_version="2.14.0") == []


@pytest.mark.parametrize("version", [None, "2.13.0", "2.14.0"])
def test_node_and_parallel_worker_alone_are_silent(tmp_path, version):
    facts = _agent(tmp_path, HEADER + """
@node(parallel_worker=True, max_parallel_workers=4)
def normalize(node_input: str):
    yield node_input.strip()

root_agent = Agent(name="review_claim", instruction="Review it.", parallel_worker=True)
""")
    assert parallel_worker_findings(facts, adk_version=version) == []


def test_fixture_and_upstream_sample():
    facts = extract_module(FIXTURES / "agents" / "parallel_workers_broken")
    [finding] = parallel_worker_findings(facts)
    assert "review_claim" in finding.message and finding.locator.startswith("line ")
    [newer] = parallel_worker_findings(facts, adk_version="2.14.0")
    assert "greater than or equal to 1" in newer.message


SAMPLE = PLUGIN_ROOT.parent / "tmp" / "adk-python" / "contributing" / "samples" / "workflows" / "parallel_worker"


@pytest.mark.skipif(not SAMPLE.is_dir(), reason="tmp/adk-python not checked out")
def test_upstream_parallel_worker_sample_is_silent():
    facts = extract_module(SAMPLE)
    assert parallel_worker_findings(facts) == []
    assert parallel_worker_findings(facts, adk_version="2.14.0") == []


@pytest.mark.parametrize("name", sorted(p.name for p in (FIXTURES / "agents").iterdir()
                                        if p.is_dir() and p.name != "parallel_workers_broken"))
def test_other_agent_fixtures_are_silent(name):
    assert parallel_worker_findings(extract_module(FIXTURES / "agents" / name)) == []
