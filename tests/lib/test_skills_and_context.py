"""Skills (AGT070-075), context engineering (AGT076-079), the YAML subset
reader, EVAL.yaml (EVY001-010), EVS073/074, the rubric version gate, the
registry's frozen suites and the post-2.12 result shapes."""

from __future__ import annotations

import json
import shutil
import textwrap
from pathlib import Path

import pytest

from adk_skills import ADK_TARGET
from adk_skills.coverage import skill_coverage
from adk_skills.eval_yaml import (
    ASSERTION_FIELDS, load_eval_yaml, plugin_layer, validate_eval_yaml,
)
from adk_skills.evalset import (
    LoadedEvalSet, case_category, expected_skill_loads, load_evalset, validate_evalset,
)
from adk_skills.registry import find_registry, frozen_suites_for
from adk_skills.results import parse_result
from adk_skills.skills import (
    frontmatter_problems, load_skill_dir, parse_skill_md, repeated_share,
)
from adk_skills.tools_ast import (
    SKILL_TOOL_NAMES, compaction_config_problems, compaction_suite_findings, extract_module,
    skill_lifecycle_findings,
)
from adk_skills.yaml_subset import YamlError, loads

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = PLUGIN_ROOT.parent / "tmp" / "adk-python" / "contributing" / "samples"
RECIPES_RETAIL = PLUGIN_ROOT.parent / "tmp" / "adk-recipes" / "plugins" / "retail"


def ids(findings) -> list[str]:
    return [f.id for f in findings]


def project(tmp_path: Path, source: str, name: str = "agent_pkg") -> Path:
    agent = tmp_path / name
    agent.mkdir()
    (agent / "__init__.py").write_text("from . import agent  # noqa: F401\n")
    (agent / "agent.py").write_text(textwrap.dedent(source))
    return agent


# -- YAML subset ------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("a: 1\nb: true\nc: ~\nd: 'x''y'\ne: \"t\\n\"\n", {"a": 1, "b": True, "c": None, "d": "x'y", "e": "t\n"}),
    ("k: [a, 'b c', {x: 1}]\n", {"k": ["a", "b c", {"x": 1}]}),
    ("- a\n- b: 1\n  c: 2\n", ["a", {"b": 1, "c": 2}]),
    ("key:\n- a\n- b\n", {"key": ["a", "b"]}),
    ("d: >-\n  one\n  two\n\n  three\n", {"d": "one two\nthree"}),
    ("d: |\n  one\n  two\n", {"d": "one\ntwo\n"}),
    ("d: plain\n  continued\nn: 2\n", {"d": "plain continued", "n": 2}),
    ("# comment\nlist:\n  - one # trailing\n", {"list": ["one"]}),
])
def test_the_yaml_subset_reads_what_safe_load_reads(text, expected):
    assert loads(text) == expected


@pytest.mark.parametrize("text", ["a: [1, 2\n", "a: 'open\n", "a: 1\n  b: 2\n", "a: *ref\n",
                                  "a: 1\n---\nb: 2\n", "\ta: 1\n"])
def test_the_yaml_subset_refuses_what_it_cannot_read(text):
    with pytest.raises(YamlError):
        loads(text)


# -- SKILL.md --------------------------------------------------------------------

def test_parse_skill_md_splits_like_adk():
    frontmatter, body, error = parse_skill_md("---\nname: a-b\ndescription: d\n---\n\n# Body\n")
    assert error is None and frontmatter == {"name": "a-b", "description": "d"} and body == "# Body"
    assert parse_skill_md("# no frontmatter")[2].startswith("SKILL.md must start")
    assert "not closed" in parse_skill_md("---\nname: x\n")[2]


@pytest.mark.parametrize("frontmatter, directory, fragment", [
    ({"name": "Bad Name", "description": "d"}, None, "not lowercase kebab-case"),
    ({"name": "a" * 65, "description": "d"}, None, "at most 64"),
    ({"name": "a--b", "description": "d"}, None, "not lowercase kebab-case"),
    ({"name": "ok", "description": ""}, None, "`description` is empty"),
    ({"name": "ok", "description": "x" * 1025}, None, "at most 1024"),
    ({"description": "d"}, None, "`name` is missing"),
    ({"name": "other", "description": "d"}, "dir-name", "does not match the directory name"),
    ({"name": "ok", "description": "d", "metadata": {"adk_additional_tools": "x"}}, None, "must be a list"),
    ({"name": "ok", "description": "d", "metadata": {"adk_inject_state": "yes"}}, None, "true or false"),
    ({"name": "ok", "description": "d", "compatibility": "c" * 501}, None, "at most 500"),
])
def test_frontmatter_problems_mirror_the_adk_validators(frontmatter, directory, fragment):
    problems = frontmatter_problems(frontmatter, directory_name=directory)
    assert any(fragment in p for p in problems), problems


def test_valid_frontmatter_with_the_sample_metadata_keys_is_clean():
    assert frontmatter_problems({"name": "weather-skill", "description": "Weather.",
                                 "metadata": {"adk_additional_tools": ["get_wind_speed"],
                                              "adk_inject_state": True}}) == []


def test_load_skill_dir_counts_resources_and_lines(agent_dir):
    info = load_skill_dir(agent_dir("skilled_agent") / "skills" / "claim-policy")
    assert info.name == "claim-policy" and info.source == "dir" and not info.problems
    assert info.resources == {"references": 1, "assets": 0, "scripts": 0}
    assert info.instruction_lines == 7 and info.extra_metadata_keys == ["owner"]


def test_repeated_share_counts_distinct_lines_verbatim():
    skill = "Step one is long enough.\nStep two is long enough.\n\n# H\n"
    assert repeated_share(skill, "intro. Step one is long enough. more") == (0.5, 1, 2)
    assert repeated_share(skill, None)[0] == 0.0


# -- extraction ------------------------------------------------------------------

def test_the_skilled_fixture_is_clean_and_its_skills_are_facts(agent_dir):
    facts = extract_module(agent_dir("skilled_agent"))
    assert not {f for f in ids(facts.findings) if f.startswith("AGT07")}
    assert [(s.name, s.source) for s in facts.skills] == [
        ("receipt-check", "inline"), ("claim-policy", "dir")]
    assert facts.registered_skill_names == {"receipt-check", "claim-policy"}
    toolset = facts.skill_toolsets[0]
    assert toolset.label == "claim_skills" and toolset.discovery_mode is None
    for name in ("list_skills", "load_skill", "load_skill_resource", "run_skill_script",
                 "unload_skill"):
        assert facts.resolve(name).status == "opaque", name
    assert facts.resolve("search_skills").status == "unknown", "no registry, no search_skills"
    data = facts.to_dict()
    assert data["skills"][1]["resources"]["references"] == 1
    assert data["skill_toolsets"][0]["tools"][0] == "list_skills"


def test_the_broken_skills_fixture_reports_every_rule(agent_dir):
    facts = extract_module(agent_dir("skilled_agent_broken"))
    found = {}
    for finding in facts.findings:
        found.setdefault(finding.id, []).append(finding)
    assert len(found["AGT070"]) == 2 and all(f.severity == "error" for f in found["AGT070"])
    messages = " ".join(f.message for f in found["AGT070"])
    assert "not lowercase kebab-case" in messages and "1100 characters" in messages
    assert "no SKILL.md" in messages
    assert found["AGT071"][0].severity == "warn" and "602 lines" in found["AGT071"][0].message
    assert found["AGT072"][0].severity == "info" and "3 of skill 'fx'" in found["AGT072"][0].message
    assert found["AGT073"][0].severity == "info" and "'fx'" in found["AGT073"][0].message
    assert found["AGT075"][0].severity == "error" and "max_active_skills=0" in found["AGT075"][0].message
    assert facts.skill_toolsets[0].lifecycle_config == {"default_mode": "BOUNDED",
                                                        "max_active_skills": 0}


def test_agt074_needs_release_modes_and_no_flag(agent_dir, tmp_path):
    facts = extract_module(agent_dir("skilled_agent_broken"))
    note = skill_lifecycle_findings(facts, env_enabled=None)
    assert ids(note) == ["AGT074"] and "unload_skill" in note[0].message
    assert "pruned" in note[0].message
    assert skill_lifecycle_findings(facts, env_enabled=True) == []
    old = skill_lifecycle_findings(facts, env_enabled=False, adk_version="2.9.2")
    assert "only exist from 2.10" in old[0].message
    persistent = project(tmp_path, """
        from google.adk.tools.skill_toolset import SkillLifecycleConfig, SkillToolset
        toolset = SkillToolset(skills=[], lifecycle_config=SkillLifecycleConfig(max_active_skills=3))
    """)
    assert skill_lifecycle_findings(extract_module(persistent), env_enabled=None) == []


@pytest.mark.parametrize("expression", [
    'pathlib.Path(__file__).parent / "skills" / "claim-policy"',
    'os.path.join(os.path.dirname(__file__), "skills", "claim-policy")',
    'Path(__file__).resolve().parent.joinpath("skills", "claim-policy")',
    'SKILLS / "claim-policy"',
    'str(SKILLS / "claim-policy")',
])
def test_skill_paths_resolve_from_the_shapes_agents_use(tmp_path, agent_dir, expression):
    agent = project(tmp_path, f"""
        import os, pathlib
        from pathlib import Path
        from google.adk.skills import load_skill_from_dir
        from google.adk.tools.skill_toolset import SkillToolset
        SKILLS = pathlib.Path(__file__).parent / "skills"
        toolset = SkillToolset(skills=[load_skill_from_dir({expression})])
    """)
    shutil.copytree(agent_dir("skilled_agent") / "skills", agent / "skills")
    facts = extract_module(agent)
    assert [s.name for s in facts.skills] == ["claim-policy"]
    assert not facts.skill_toolsets[0].skills_unresolved


def test_load_skills_from_dir_registers_every_subdirectory(tmp_path, agent_dir):
    agent = project(tmp_path, """
        import pathlib
        from google.adk.skills import load_skills_from_dir
        from google.adk.tools.skill_toolset import SkillToolset, SkillDiscoveryMode
        skills = load_skills_from_dir(pathlib.Path(__file__).parent / "skills")
        toolset = SkillToolset(skills=skills, discovery_mode=SkillDiscoveryMode.EAGER,
                               tool_name_prefix="claims")
    """)
    shutil.copytree(agent_dir("skilled_agent") / "skills", agent / "skills")
    toolset = extract_module(agent).skill_toolsets[0]
    assert [s.name for s in toolset.skills] == ["claim-policy"]
    assert toolset.discovery_mode == "EAGER"
    assert "claims_list_skills" not in toolset.tool_names, "EAGER drops list_skills"
    assert "claims_load_skill" in toolset.tool_names


def test_a_runtime_skill_list_makes_the_registered_set_unknown(tmp_path):
    agent = project(tmp_path, """
        from google.adk.tools.skill_toolset import SkillToolset
        from somewhere import build
        toolset = SkillToolset(skills=build())
    """)
    facts = extract_module(agent)
    assert facts.skill_toolsets[0].skills_unresolved
    assert facts.registered_skill_names is None
    assert facts.resolve("load_skill").status == "opaque"


def test_a_project_without_skills_has_no_registered_set(agent_dir):
    assert extract_module(agent_dir("flat_good")).registered_skill_names is None


@pytest.mark.skipif(not UPSTREAM.is_dir(), reason="no upstream samples checkout")
@pytest.mark.parametrize("sample, expected", [
    ("environment_and_skills/skills", ["support-hours-skill", "weather-skill"]),
    ("environment_and_skills/skills_agent", ["greeting-skill", "weather-skill"]),
    ("environment_and_skills/skills_inject_state", ["code-review-skill"]),
    ("environment_and_skills/local_env_skill_toolset", ["calc-skill", "text-skill"]),
    ("environment_and_skills/skills_agent_gcs", [None]),
    ("integrations/gcp_skill_registry_agent", [None]),
])
def test_upstream_skill_samples_have_valid_skills(sample, expected):
    facts = extract_module(UPSTREAM / sample)
    assert "AGT070" not in ids(facts.findings)
    assert [s.name for s in facts.skills] == expected
    assert facts.resolve("load_skill").status == "opaque"


# -- context engineering ---------------------------------------------------------

@pytest.mark.parametrize("config, fragment", [
    ({"compaction_interval": 0, "overlap_size": 0}, "compaction_interval=0 must be greater than 0"),
    ({"compaction_interval": 3, "overlap_size": -1}, "overlap_size=-1 must be at least 0"),
    ({"token_threshold": 0, "event_retention_size": 2}, "token_threshold=0"),
    ({"compaction_interval": 3}, "without overlap_size"),
    ({"token_threshold": 1000}, "without event_retention_size"),
    ({}, "configures no trigger"),
    ({"compaction_interval": 3, "overlap_size": 1, "bogus": 1}, "extra fields are forbidden"),
])
def test_compaction_config_problems(config, fragment):
    assert any(fragment in p for p in compaction_config_problems(config))


def test_a_valid_compaction_config_is_clean():
    assert compaction_config_problems({"compaction_interval": 5, "overlap_size": 1}) == []
    assert compaction_config_problems(None) == []


def test_the_compaction_fixture_reports_agt076_078_079(agent_dir):
    facts = extract_module(agent_dir("compaction_broken"))
    found = ids(facts.findings)
    assert found.count("AGT076") == 2
    assert "AGT078" in found and "AGT079" in found
    assert facts.app.events_compaction_config == {"compaction_interval": 0, "overlap_size": -1}


def test_agt078_skips_workflow_children_and_scoped_sub_agents(tmp_path):
    agent = project(tmp_path, """
        from google.adk.agents import Agent, SequentialAgent
        a = Agent(name="a", instruction="x")
        b = Agent(name="b", instruction="x", include_contents="none")
        c = Agent(name="c", instruction="x")
        pipeline = SequentialAgent(name="p", sub_agents=[c])
        root_agent = Agent(name="root", instruction="route", sub_agents=[a, b, pipeline])
    """)
    found = [f for f in extract_module(agent).findings if f.id == "AGT078"]
    assert [f.message.split("'")[1] for f in found] == ["a"]


def test_agt079_follows_a_returned_module_constant(tmp_path):
    agent = project(tmp_path, f"""
        from google.adk.agents import Agent
        BIG = "{'x' * 2500}"
        def handbook() -> str:
            \"\"\"Return the whole handbook text for the agent to read.\"\"\"
            return BIG
        root_agent = Agent(name="r", instruction="use handbook", tools=[handbook])
    """)
    assert "AGT079" in ids(extract_module(agent).findings)


def test_agt077_needs_a_long_conversation_and_no_compaction(agent_dir, fixtures):
    facts = extract_module(agent_dir("recipes_good"))
    long_doc = load_evalset(fixtures / "evalsets" / "long_conversation.evalset.json").doc
    note = compaction_suite_findings(facts, [long_doc])
    assert ids(note) == ["AGT077"] and "events_compaction_config" in note[0].message
    scenario = load_evalset(fixtures / "evalsets" / "scenario.evalset.json").doc
    assert ids(compaction_suite_findings(facts, [scenario])) == ["AGT077"]
    short = load_evalset(next((agent_dir("recipes_good") / "tests/eval/evalsets").glob("happy*"))).doc
    assert compaction_suite_findings(facts, [short]) == []
    compacted = extract_module(agent_dir("compaction_broken"))
    assert compaction_suite_findings(compacted, [long_doc]) == []


def test_include_contents_is_extracted(tmp_path):
    agent = project(tmp_path, """
        from google.adk.agents import Agent
        MODE = "none"
        root_agent = Agent(name="r", instruction="x", include_contents=MODE)
    """)
    assert extract_module(agent).root_agent().include_contents == "none"


# -- evalsets: activation, EVS074, rubric text_property -------------------------

def test_activation_cases_have_their_own_category():
    assert case_category("activation_claim_policy_missing_amount") == "activation"


def test_expected_skill_loads_reads_skill_name_and_prefixes():
    case = {"conversation": [{"intermediate_data": {"tool_uses": [
        {"name": "load_skill", "args": {"skill_name": "a"}},
        {"name": "claims_load_skill", "args": {"skill_name": "b"}},
        {"name": "load_skill_resource", "args": {"skill_name": "a", "file_path": "x"}},
    ]}}]}
    assert expected_skill_loads(case) == [("load_skill", "a"), ("claims_load_skill", "b")]


def test_evs074_warns_on_a_skill_the_agent_does_not_register(agent_dir):
    path = agent_dir("skilled_agent") / "activation.evalset.json"
    doc = json.loads(path.read_text())
    doc["eval_cases"][0]["conversation"][0]["intermediate_data"]["tool_uses"][0]["args"] = \
        {"skill_name": "claims-policy"}
    loaded = LoadedEvalSet(str(path), doc, doc, "evalset")
    facts = extract_module(agent_dir("skilled_agent"))
    report = validate_evalset(loaded, registered_skills=facts.registered_skill_names,
                              tool_resolver=facts.resolve)
    found = [f for f in report.findings if f.id == "EVS074"]
    assert len(found) == 1 and found[0].severity == "warn" and "claims-policy" in found[0].message
    assert "EVS036" not in ids(report.findings), "load_skill is a real (opaque) tool"
    silent = validate_evalset(loaded, registered_skills=None)
    assert "EVS074" not in ids(silent.findings), "an unknown registered set says nothing"


@pytest.mark.parametrize("version, severity", [("2.9.2", "error"), ("2.11.0", "error"),
                                               ("2.12.0", "warn"), (None, "warn")])
def test_a_rubric_without_text_property_is_an_error_only_below_2_12(version, severity):
    doc = {"eval_set_id": "r", "eval_cases": [{
        "eval_id": "happy_x",
        "conversation": [{"invocation_id": "i", "user_content": {"parts": [{"text": "hi"}]}}],
        "session_input": {"app_name": "app", "user_id": "u"},
        "rubrics": [{"rubric_id": "r1", "rubric_content": {}, "type": "FINAL_RESPONSE_QUALITY"}],
    }]}
    report = validate_evalset(LoadedEvalSet("x", doc, doc, "evalset"), adk_version=version)
    found = [f for f in report.findings if f.id == "EVS082"]
    assert found and found[0].severity == severity
    assert ("requires it" in found[0].message) == (severity == "error")


def test_the_config_rubric_check_follows_the_same_gate(tmp_path):
    from adk_skills.eval_config import load_config, validate_config
    path = tmp_path / "eval_config.json"
    path.write_text(json.dumps({"criteria": {"rubric_based_final_response_quality_v1": {
        "threshold": 0.8, "rubrics": [{"rubric_id": "a", "rubric_content": {}}]}}}))
    old = validate_config(load_config(path), adk_version="2.11.0")
    new = validate_config(load_config(path), adk_version="2.12.0")
    assert [f.severity for f in old.findings if f.id == "CFG044"] == ["error"]
    assert [f.severity for f in new.findings if f.id == "CFG044"] == ["warn"]


def test_skill_coverage_lists_skills_no_case_loads(agent_dir):
    doc = load_evalset(agent_dir("skilled_agent") / "activation.evalset.json").doc
    full = skill_coverage({"claim-policy", "receipt-check"}, {"a": doc})
    assert full["never_activated"] == []
    partial = skill_coverage({"claim-policy", "receipt-check", "fx"}, {"a": doc})
    assert partial["never_activated"] == ["fx"]
    assert skill_coverage(None, {"a": doc}) is None


# -- EVAL.yaml -------------------------------------------------------------------

def test_the_fixture_plugin_layer_is_summarised(fixtures):
    layer = plugin_layer(fixtures / "plugins" / "retail-like")
    assert layer["plugins"][0]["name"] == "expenses"
    assert layer["plugins"][0]["skills"] == ["product-search"]
    summary = layer["eval_yamls"][0]
    assert (summary["cases"], summary["rubric_lines"], summary["assertions"]) == (2, 4, 4)
    assert summary["assertion_types"]["tool_called"] == 1 and summary["threshold"] is None
    assert validate_eval_yaml(load_eval_yaml(
        fixtures / "plugins/retail-like/skills/product-search/EVAL.yaml")) == []


@pytest.mark.parametrize("text, expected", [
    ("cases: [\n", "EVY001"),
    ("other: 1\n", "EVY002"),
    ("cases: []\n", "EVY002"),
    ("cases:\n- prompt: p\n  rubric: [The response MUST x.]\n", "EVY003"),
    ("cases:\n- id: a\n  rubric: [The response MUST x.]\n", "EVY004"),
    ("cases:\n- id: a\n  prompt: p\n  rubric: [The response MUST x.]\n"
     "- id: a\n  prompt: q\n  rubric: [The response MUST y.]\n", "EVY005"),
    ("cases:\n- id: a\n  prompt: p\n", "EVY006"),
    ("cases:\n- id: a\n  prompt: p\n  rubric: [It is fine.]\n", "EVY007"),
    ("cases:\n- id: a\n  prompt: p\n  rubric: [The response MUST x.]\n"
     "  assertions:\n    - type: vibes\n", "EVY008"),
    ("cases:\n- id: a\n  prompt: p\n  rubric: [The response MUST x.]\n"
     "  assertions:\n    - type: price_under\n", "EVY009"),
    ("cases:\n- id: a\n  prompt: p\n  rubric: [The response MUST x.]\n"
     "  assertions:\n    - type: tool_called\n      tool: never_named\n", "EVY010"),
])
def test_every_evy_id(tmp_path, text, expected):
    skill = tmp_path / "skills" / "s"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\nUse lookup_claims.\n")
    (skill / "EVAL.yaml").write_text(text)
    found = validate_eval_yaml(load_eval_yaml(skill / "EVAL.yaml"))
    assert expected in ids(found), ids(found)


def test_evy006_is_a_warning_when_assertions_still_check_the_case(tmp_path):
    (tmp_path / "EVAL.yaml").write_text(
        "cases:\n- id: a\n  prompt: p\n  assertions:\n    - type: response_not_empty\n")
    found = validate_eval_yaml(load_eval_yaml(tmp_path / "EVAL.yaml"))
    assert [(f.id, f.severity) for f in found] == [("EVY006", "warn")]


def test_assertion_fields_cover_the_recipes_types():
    assert set(ASSERTION_FIELDS) == {
        "contains_product", "price_under", "tool_called", "no_hallucination", "mentions_price",
        "mentions_field", "response_not_empty", "response_min_length", "graceful_no_results",
        "skill_covers"}


@pytest.mark.skipif(not RECIPES_RETAIL.is_dir(), reason="no adk-recipes checkout")
def test_the_recipes_retail_eval_yamls_are_clean():
    layer = plugin_layer(RECIPES_RETAIL)
    counts = {e["skill_dir"]: (e["cases"], e["rubric_lines"], e["assertions"])
              for e in layer["eval_yamls"]}
    assert counts == {"product-search": (10, 42, 31), "virtual-tryon": (15, 55, 0)}
    for entry in layer["eval_yamls"]:
        assert not [f for f in validate_eval_yaml(load_eval_yaml(entry["path"]))
                    if f.severity == "error"]


# -- frozen suites ---------------------------------------------------------------

def test_frozen_suites_are_found_from_the_evalset(tmp_path, fixtures, agent_dir):
    shutil.copytree(agent_dir("recipes_good"), tmp_path / "proj")
    shutil.copy(fixtures / "registry/frozen_suite/eval_registry.json", tmp_path / "proj")
    evalsets = tmp_path / "proj" / "tests/eval/evalsets"
    assert find_registry(evalsets) == tmp_path / "proj" / "eval_registry.json"
    assert frozen_suites_for(evalsets / "happy.evalset.json")[0] == ["golden"]
    assert frozen_suites_for(evalsets / "edge.evalset.json")[0] == []


# -- results after 2.12.0 ---------------------------------------------------------

def test_duration_split_and_vertex_explanation_are_read(tmp_path):
    path = tmp_path / "app_s_1.evalset_result.json"
    path.write_text(json.dumps({"eval_set_id": "s", "eval_case_results": [{
        "eval_id": "c", "final_eval_status": 1, "overall_eval_metric_results": [
            {"metric_name": "invocation_duration_v1", "score": 3.0, "eval_status": 4,
             "details": {"duration_details": {"total_seconds": 3.0, "model_seconds": 2.0,
                                              "tool_seconds": 0.5, "framework_seconds": 0.5}}},
            {"metric_name": "rubric_based_final_response_quality_v1", "score": 1.0,
             "threshold": 0.8, "eval_status": 1, "details": {"rubric_scores": [
                 {"rubric_id": "names_rule", "score": 1.0, "rationale": "named"},
                 {"rubric_id": "explanation", "score": None, "rationale": "All rubrics met."}]}},
        ]}]}))
    run = parse_result(path)
    case = run.cases[0]
    assert case.duration_split()["model_seconds"] == 2.0
    assert run.efficiency()["invocation_duration_v1"]["duration_details"]["tool_seconds"] == 0.5
    judge = case.metric("rubric_based_final_response_quality_v1")
    assert [r["rubric_id"] for r in judge.rubric_scores] == ["names_rule"]
    assert judge.explanation == "All rubrics met." and judge.failed_rubrics() == []


def test_a_212_run_without_the_split_has_none(fixtures):
    run = parse_result(fixtures / "eval_history/app_efficiency_1790000000.123456.evalset_result.json")
    assert run.duration_means() == {}


def test_the_target_release_is_2_12():
    assert ADK_TARGET == "2.12"
    assert "unload_skill" in SKILL_TOOL_NAMES
