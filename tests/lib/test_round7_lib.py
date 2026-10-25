"""Rules added against ADK main after 2.13.0 (2026-10-29): compound rubrics
(RUB006 tightened), action rubrics under the final-response judge (CFG075), the
multi-turn trajectory judge and built-in tools (CFG076), streaming tools off
`run_live` (AGT081), the src layout, and per-metric means for compare_runs."""

import textwrap

import pytest

from adk_skills.env import is_live_model_id
from adk_skills.eval_config import LoadedConfig, asks_about_actions, validate_config
from adk_skills.layout import detect_layout
from adk_skills.results import case_metric_scores, shared_metric_means, CaseResult, MetricResult
from adk_skills.rubrics import lint_rubric_text
from adk_skills.tools_ast import builtin_model_tools, extract_module, streaming_tool_findings


def rub_ids(text: str) -> set[str]:
    return {f.id for f in lint_rubric_text(text, rubric_id="r")}


# -- RUB006: one connector between two verb phrases --------------------------

@pytest.mark.parametrize("text", [
    "Across the call, the agent confirms the caller's name and validates their date of "
    "birth before disclosing any appointment details.",
    "After identity is verified, the agent delivers the appointment time and answers the "
    "caller's follow-up question.",
    "The response names the specific device in the requested location and states its "
    "current on/off status.",
    "The final response names the exceeded limit and states what the claimant can do next.",
    "The agent cites the policy rule; it also offers an alternative cost centre.",
    "The agent calls `lookup_policy` and then calls `submit_claim`.",
])
def test_two_expectations_joined_once_are_compound(text):
    assert "RUB006" in rub_ids(text)


@pytest.mark.parametrize("text", [
    "If the agent shares appointment details in this turn, the caller's name and date of "
    "birth must already have been confirmed earlier in the conversation.",
    "The conversation proceeds through the intended stages in order: greeting and name "
    "confirmation, then date-of-birth verification, then appointment delivery.",
    "The response is concise and free of filler.",
    "Every number and identifier in the final response appears in a tool result from this "
    "conversation.",
    "The agent does NOT call `submit_claim` when `lookup_policy` reported the category as "
    "restricted.",
])
def test_noun_and_adjective_pairs_are_not_compound(text):
    assert "RUB006" not in rub_ids(text)


def test_the_compound_message_says_one_expectation_per_rubric():
    [finding] = [f for f in lint_rubric_text(
        "The response names the limit and states the next step.", rubric_id="r")
        if f.id == "RUB006"]
    assert "one expectation per rubric; compound expectations grade inconsistently" in \
        finding.message


# -- CFG075 --------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("The agent calls `check_cost_centre_funds` before `submit_claim`.", True),
    ("The agent ran the receipt check.", True),
    ("The agent uses the policy lookup tool.", True),
    ("The agent checked the cost centre before submitting.", True),
    ("`submit_claim` must not be called when the category is restricted.", True),
    ("The agent ends the call warmly with a goodbye.", False),
    ("Every call to `submit_claim` is preceded by a call to `lookup_policy`.", False),
    ("Facts the user stated must not be called fabrication.", False),
    ("The response names the specific constraint that blocked the claim.", False),
    ("The agent uses a value the user gave earlier in the conversation.", False),
])
def test_action_wording(text, expected):
    assert asks_about_actions(text) is expected


def _rubric_config(*criteria: str, text: str) -> LoadedConfig:
    doc = {"criteria": {}}
    for name in criteria:
        doc["criteria"][name] = {
            "threshold": 0.8,
            "judge_model_options": {"judge_model": "gemini-flash-latest", "num_samples": 3},
            "rubrics": [{"rubric_id": "funds_first", "rubric_content": {"text_property": text}}],
        } if name.startswith("rubric_based") else 1.0
    return LoadedConfig("c.json", doc)


ACTION = "The agent calls `check_cost_centre_funds` before it calls `submit_claim`."


def test_an_action_rubric_under_the_reply_judge_alone_is_noted(fixtures):
    from adk_skills.eval_config import load_config
    report = validate_config(load_config(fixtures / "eval_configs/action_rubric_reply_judge.json"))
    [finding] = [f for f in report.findings if f.id == "CFG075"]
    assert finding.severity == "info"
    assert "'funds_checked_before_submit'" in finding.message
    assert "move this rubric under `rubric_based_tool_use_quality_v1` or add a trajectory " \
        "metric" in finding.message
    assert finding.locator == "criteria.rubric_based_final_response_quality_v1.rubrics[0]"


@pytest.mark.parametrize("other", [
    "tool_trajectory_avg_score", "rubric_based_tool_use_quality_v1",
    "rubric_based_multi_turn_trajectory_quality_v1",
])
def test_a_trajectory_metric_in_the_config_silences_cfg075(other):
    loaded = _rubric_config("rubric_based_final_response_quality_v1", other, text=ACTION)
    assert not [f for f in validate_config(loaded).findings if f.id == "CFG075"]


def test_a_custom_trajectory_metric_silences_cfg075():
    loaded = _rubric_config("rubric_based_final_response_quality_v1", text=ACTION)
    loaded.doc["custom_metrics"] = {"claim_trajectory": {"code_config": {"name": "m.f"}}}
    loaded.doc["criteria"]["claim_trajectory"] = 1.0
    assert not [f for f in validate_config(loaded).findings if f.id == "CFG075"]


def test_cfg075_ignores_reply_rubrics_and_the_tool_use_judge():
    reply = _rubric_config("rubric_based_final_response_quality_v1",
                           text="The final response names the exceeded limit.")
    assert not [f for f in validate_config(reply).findings if f.id == "CFG075"]
    tool_use = _rubric_config("rubric_based_tool_use_quality_v1", text=ACTION)
    assert not [f for f in validate_config(tool_use).findings if f.id == "CFG075"]


# -- CFG076 --------------------------------------------------------------------

TRAJECTORY = "rubric_based_multi_turn_trajectory_quality_v1"


def _cfg076(version, builtins=("google_search",)):
    loaded = _rubric_config(TRAJECTORY, text="The agent asks for the city before it searches.")
    report = validate_config(loaded, adk_version=version, builtin_tools=list(builtins))
    return [f for f in report.findings if f.id == "CFG076"]


@pytest.mark.parametrize("version", [None, "2.9.2", "2.12.0", "2.13.0"])
def test_the_trajectory_judge_with_a_builtin_tool_warns_through_2_13(version):
    [finding] = _cfg076(version)
    assert finding.severity == "warn"
    assert "TypeError: 'NoneType' object is not iterable" in finding.message
    assert "google_search" in finding.message


@pytest.mark.parametrize("version", ["2.13.1", "2.14.0", "2.14.0.dev1"])
def test_a_newer_or_unknown_runner_gets_an_info(version):
    [finding] = _cfg076(version)
    assert finding.severity == "info" and version in finding.message


def test_no_builtin_tool_no_cfg076():
    assert not _cfg076("2.13.0", builtins=())


def test_builtin_tools_are_read_from_the_agent(agent_dir, tmp_path):
    assert builtin_model_tools(extract_module(agent_dir("search_agent"))) == ["google_search"]
    assert builtin_model_tools(extract_module(agent_dir("flat_good"))) == []
    (tmp_path / "agent.py").write_text(textwrap.dedent('''
        from google.adk.agents import Agent
        from google.adk.code_executors import BuiltInCodeExecutor
        from google.adk.tools import VertexAiSearchTool, exit_loop
        root_agent = Agent(name="a", model="gemini-flash-latest", instruction="x",
                           code_executor=BuiltInCodeExecutor(),
                           tools=[VertexAiSearchTool(data_store_id="d"), exit_loop])
    '''))
    assert builtin_model_tools(extract_module(tmp_path)) == ["VertexAiSearchTool",
                                                            "code_execution"]


# -- AGT081 --------------------------------------------------------------------

@pytest.mark.parametrize("model,live", [
    ("gemini-3.8-live", True), ("gemini-3.8-live-extended-thinking", True),
    ("gemini-3.1-flash-live-preview", True), ("gemini-live-2.5-flash-native-audio", True),
    ("gemini-2.5-flash-native-audio-preview-12-2025", True),
    ("gemini-flash-latest", False), ("gemini-3.8-flash", False), ("delivery-agent", False),
])
def test_live_model_ids(model, live):
    assert is_live_model_id(model) is live


def _streaming(tmp_path, model_expr, *, extra="", configs=()):
    (tmp_path / "agent.py").write_text(textwrap.dedent(f'''
        from typing import AsyncGenerator
        from google.adk.agents import Agent
        {extra}

        async def track(claim_id: str) -> AsyncGenerator[str, None]:
            """Track a claim. Call this ONCE.

            Args:
                claim_id: The claim.
            """
            yield "PROGRESS: started"
            yield "DONE: approved"

        async def plain(claim_id: str) -> dict:
            """Not a generator.

            Args:
                claim_id: The claim.
            """
            def inner():
                yield 1
            return {{"status": "ok"}}

        root_agent = Agent(name="tracker", model={model_expr}, instruction="x",
                           tools=[track, plain])
    '''))
    return streaming_tool_findings(extract_module(tmp_path), configs)


def test_a_streaming_tool_on_a_text_model_warns(tmp_path):
    [finding] = _streaming(tmp_path, '"gemini-flash-latest"')
    assert (finding.id, finding.severity) == ("AGT081", "warn")
    assert "'track'" in finding.message and "2 `yield`s" in finding.message
    assert "ValueError: Output already set" in finding.message
    assert "on 2.9.2 the body never runs" in finding.message


def test_a_streaming_tool_on_a_live_model_is_an_info(tmp_path):
    [finding] = _streaming(tmp_path, '"gemini-3.8-live"')
    assert finding.severity == "info" and "live_model_config" in finding.message
    assert "stop_streaming" in finding.fix


def test_live_model_config_beside_a_live_agent_silences_the_info(tmp_path):
    assert _streaming(tmp_path, '"gemini-3.8-live"', configs=[{"live_model_config": {}}]) == []


def test_cascade_live_and_a_model_constant_count_as_live(tmp_path):
    [cascade] = _streaming(tmp_path, 'CascadeLive(model="gemini-3.8-flash")',
                           extra="from google.adk.live import CascadeLive")
    assert cascade.severity == "info" and "CascadeLive(...)" in cascade.message
    [constant] = _streaming(tmp_path, "MODEL", extra='MODEL = "gemini-3.8-live"')
    assert constant.severity == "info"


def test_an_env_model_decides(tmp_path):
    found = _streaming(tmp_path, 'os.getenv("MODEL_NAME", "gemini-flash-latest")',
                       extra="import os")
    assert [f.severity for f in found] == ["warn"]
    facts = extract_module(tmp_path)
    assert [f.severity for f in streaming_tool_findings(
        facts, env_model="gemini-3.8-live")] == ["info"]


def test_a_sub_agent_inherits_its_parents_live_model(tmp_path):
    (tmp_path / "agent.py").write_text(textwrap.dedent('''
        from google.adk.agents import Agent

        async def track(claim_id: str):
            """Track.

            Args:
                claim_id: The claim.
            """
            yield "PROGRESS"

        child = Agent(name="child", instruction="x", tools=[track])
        root_agent = Agent(name="root", model="gemini-3.8-live", instruction="x",
                           sub_agents=[child])
    '''))
    [finding] = streaming_tool_findings(extract_module(tmp_path))
    assert finding.severity == "info" and "'child'" in finding.message


def test_the_live_fixtures(agent_dir):
    live = extract_module(agent_dir("live_agent"))
    assert live.tools["track_claim_progress"].is_streaming
    assert not live.tools["stop_streaming"].is_streaming
    assert streaming_tool_findings(live, [{"live_model_config": {"timeout_seconds": 300}}]) == []
    assert "AGT045" not in {f.id for f in live.findings}
    broken = streaming_tool_findings(extract_module(agent_dir("live_agent_broken")))
    assert [(f.id, f.severity) for f in broken] == [("AGT081", "warn")]


# -- src layout ----------------------------------------------------------------

def test_a_src_layout_resolves_to_the_package(fixtures):
    layout = detect_layout(fixtures / "projects/src_layout")
    assert layout.style == "src"
    assert layout.agent_dir.endswith("src/claims_agent")
    assert layout.app_name == "claims_agent"
    assert layout.expected_app_name() == "claims"
    assert [p.split("/")[-1] for p in layout.agents_cli_datasets] == ["limits-dataset.json"]
    assert layout.evalsets == []
    assert any("not ADK evalsets" in note for note in layout.notes)


def test_src_without_a_manifest_takes_the_single_package(tmp_path):
    (tmp_path / "src/claims").mkdir(parents=True)
    (tmp_path / "src/__init__.py").write_text("from .claims import root_agent\n")
    (tmp_path / "src/claims/__init__.py").write_text("from . import agent\n")
    (tmp_path / "src/claims/agent.py").write_text(
        'from google.adk.agents import Agent\nroot_agent = Agent(name="c", instruction="x")\n')
    layout = detect_layout(tmp_path)
    assert (layout.style, layout.app_name) == ("src", "claims")


# -- per-metric means ------------------------------------------------------------

def _case(eval_id, **scores):
    return CaseResult(eval_id=eval_id, status="PASSED", metrics=[
        MetricResult(name=name, score=score, threshold=0.5, status="PASSED")
        for name, score in scores.items()
    ] + [MetricResult(name="token_usage_v1", score=900.0, threshold=None,
                      status="INFORMATIONAL")])


def test_metric_means_cover_only_shared_cases():
    before = {"a": case_metric_scores([_case("a", judge=1.0)]),
              "b": case_metric_scores([_case("b", judge=0.5)]),
              "gone": case_metric_scores([_case("gone", judge=0.0)])}
    after = {"a": case_metric_scores([_case("a", judge=0.8), _case("a", judge=0.6)]),
             "b": case_metric_scores([_case("b", judge=0.5)]),
             "new": case_metric_scores([_case("new", judge=0.0)])}
    means = shared_metric_means(before, after)
    assert set(means) == {"judge"}
    assert means["judge"] == {"baseline": 0.75, "candidate": 0.6, "delta": -0.15, "cases": 2}


def test_another_actor_running_something_is_not_an_agent_action():
    assert not asks_about_actions("Memories the tool injected before the model ran are real.")
    assert not asks_about_actions("The agent waits until the user calls back.")
