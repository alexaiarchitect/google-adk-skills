"""Context caching: static_instruction / ContextCacheConfig extraction and AGT060-066."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from adk_skills.tools_ast import (
    CACHE_CONFIG_BOUNDS, cache_config_problems, cache_suite_findings, extract_module,
)

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_SAMPLES = PLUGIN_ROOT.parent / "tmp" / "adk-python" / "contributing" / "samples" / \
    "context_management"
CACHE_IDS = {"AGT060", "AGT061", "AGT062", "AGT063", "AGT064", "AGT065", "AGT066", "AGT067"}


def ids(facts) -> list[str]:
    return [f.id for f in facts.findings]


def project(tmp_path: Path, source: str) -> Path:
    agent = tmp_path / "agent_pkg"
    agent.mkdir()
    (agent / "__init__.py").write_text("from . import agent  # noqa: F401\n")
    (agent / "agent.py").write_text(textwrap.dedent(source))
    return agent


# -- extraction ---------------------------------------------------------------

def test_cached_fixture_static_instruction_follows_content_part_and_a_constant(agent_dir):
    facts = extract_module(agent_dir("cached_agent"))
    agent = facts.root_agent()
    assert agent.has_static_instruction is True
    assert agent.static_instruction_len > 1500, "Content(parts=[Part(text=CONST)]) resolves"
    assert agent.static_instruction_dynamic == []
    assert "{claimant_name?}" in agent.instruction_text, "placeholders belong in instruction"
    assert facts.app.context_cache_set is True
    assert facts.app.context_cache_config == {
        "min_tokens": 2048, "ttl_seconds": 600, "cache_intervals": 5,
    }
    assert not CACHE_IDS & set(ids(facts))


def test_cached_fixture_rules_are_read_from_the_static_instruction(agent_dir):
    agent = extract_module(agent_dir("cached_agent")).root_agent()
    assert "lookup_policy" in agent.spec_text
    assert agent.spec_text.index("lookup_policy") < agent.spec_text.index("claimant_name")


def test_to_dict_carries_the_cache_facts_and_not_the_text(agent_dir):
    data = extract_module(agent_dir("cached_agent")).to_dict()
    agent = data["agents"][0]
    assert agent["static_instruction_len"] > 1500
    assert agent["static_instruction_dynamic"] == []
    assert "static_instruction_text" not in agent
    assert data["app"]["context_cache_config"]["cache_intervals"] == 5


def test_broken_fixture_reports_a_dynamic_prefix_and_an_invalid_config(agent_dir):
    facts = extract_module(agent_dir("cached_agent_broken"))
    found = {f.id: f for f in facts.findings}
    assert found["AGT060"].severity == "warn"
    assert "an f-string" in found["AGT060"].message
    assert "datetime.datetime.now()" in found["AGT060"].message
    assert found["AGT062"].severity == "error"
    assert "cache_intervals=0 is outside 1..100" in found["AGT062"].message


@pytest.mark.parametrize("expression, reason", [
    ('f"Today is {date.today()}. " + RULES', "an f-string"),
    ('RULES.format(day="Monday")', "`.format()`"),
    ('"Shift: %s" % SHIFT', "`%` formatting"),
    ('"Ticket " + str(uuid.uuid4())', "`uuid.uuid4()`"),
    ('"Greet {user_name} first."', "`{user_name}` placeholder"),
    ("build_rules", "InstructionProvider callable `build_rules`"),
    ("lambda ctx: RULES", "InstructionProvider (lambda)"),
    ('types.Part.from_text(text=f"{RULES} at {time.time()}")', "`time.time()`"),
])
def test_dynamic_static_instruction_shapes_are_reported(tmp_path, expression, reason):
    agent = project(tmp_path, f'''
        import time
        import uuid
        from datetime import date
        from google.adk.agents import Agent
        from google.genai import types

        RULES = "Always call lookup_policy first."
        SHIFT = "night"

        def build_rules(ctx):
            return RULES

        root_agent = Agent(name="a", model="gemini-flash-latest",
                           static_instruction={expression}, instruction="Go.")
    ''')
    facts = extract_module(agent)
    finding = next(f for f in facts.findings if f.id == "AGT060")
    assert reason in finding.message


def test_a_placeholder_in_instruction_is_not_a_cache_problem(tmp_path):
    agent = project(tmp_path, '''
        from google.adk.agents import Agent
        STATIC = """Always call lookup_policy first. Reply with {"status": "ok"} JSON."""
        root_agent = Agent(name="a", model="gemini-flash-latest",
                           static_instruction=STATIC, instruction="User is {user_name}.")
    ''')
    assert "AGT060" not in ids(extract_module(agent)), "JSON braces are not placeholders"


def test_static_instruction_only_does_not_count_as_a_missing_instruction(tmp_path):
    agent = project(tmp_path, '''
        from google.adk.agents import Agent
        root_agent = Agent(name="a", model="gemini-flash-latest",
                           static_instruction="Always call lookup_policy first.")
    ''')
    assert "AGT020" not in ids(extract_module(agent))


# -- AGT061 -------------------------------------------------------------------

LARGE = "Step: always check the policy before acting on a claim. " * 160   # ~9k chars


def test_a_large_uncached_instruction_is_noted(tmp_path):
    agent = project(tmp_path, f'''
        from google.adk.agents import Agent
        from google.adk.apps import App
        PROCEDURE = """{LARGE}"""
        root_agent = Agent(name="a", model="gemini-flash-latest", instruction=PROCEDURE)
        app = App(name="agent_pkg", root_agent=root_agent)
    ''')
    finding = next(f for f in extract_module(agent).findings if f.id == "AGT061")
    assert finding.severity == "info"
    assert "nothing caches it" in finding.message
    assert "second turn" in finding.fix


@pytest.mark.parametrize("extra, app_kwargs", [
    ("", ", context_cache_config=ContextCacheConfig()"),
    ('static_instruction="Fixed rules.", ', ""),
])
def test_a_large_instruction_with_caching_or_a_static_split_is_not_noted(tmp_path, extra, app_kwargs):
    agent = project(tmp_path, f'''
        from google.adk.agents import Agent
        from google.adk.agents.context_cache_config import ContextCacheConfig
        from google.adk.apps import App
        PROCEDURE = """{LARGE}"""
        root_agent = Agent(name="a", model="gemini-flash-latest", {extra}instruction=PROCEDURE)
        app = App(name="agent_pkg", root_agent=root_agent{app_kwargs})
    ''')
    assert "AGT061" not in ids(extract_module(agent))


# -- AGT062 -------------------------------------------------------------------

@pytest.mark.parametrize("config, problem", [
    ({"cache_intervals": 0}, "cache_intervals=0 is outside 1..100"),
    ({"cache_intervals": 101}, "cache_intervals=101 is outside 1..100"),
    ({"ttl_seconds": 0}, "ttl_seconds=0 must be greater than 0"),
    ({"min_tokens": -1}, "min_tokens=-1 must be at least 0"),
    ({"ttl": 60}, "has no field(s) ttl"),
])
def test_cache_config_bounds_match_the_pydantic_model(config, problem):
    assert any(problem in p for p in cache_config_problems(config))


def test_valid_and_unreadable_cache_configs_raise_nothing():
    assert cache_config_problems({"min_tokens": 4096, "ttl_seconds": 600, "cache_intervals": 3}) == []
    assert cache_config_problems({"ttl_seconds": "TTL"}) == [], "an expression is not judged"
    assert cache_config_problems(None) == []
    assert set(CACHE_CONFIG_BOUNDS) == {
        "cache_intervals", "ttl_seconds", "min_tokens", "create_http_options",
    }


def test_a_cache_config_held_in_a_constant_is_followed(tmp_path):
    agent = project(tmp_path, '''
        from google.adk.agents import Agent
        from google.adk.agents.context_cache_config import ContextCacheConfig
        from google.adk.apps import App
        CACHE = ContextCacheConfig(ttl_seconds=-5)
        root_agent = Agent(name="a", model="gemini-flash-latest", instruction="Go.")
        app = App(name="agent_pkg", root_agent=root_agent, context_cache_config=CACHE)
    ''')
    facts = extract_module(agent)
    assert facts.app.context_cache_config == {"ttl_seconds": -5}
    assert "AGT062" in ids(facts)


def test_an_explicit_none_cache_config_is_off(tmp_path):
    agent = project(tmp_path, '''
        from google.adk.agents import Agent
        from google.adk.apps import App
        root_agent = Agent(name="a", model="gemini-flash-latest", instruction="Go.")
        app = App(name="agent_pkg", root_agent=root_agent, context_cache_config=None)
    ''')
    assert extract_module(agent).app.context_cache_set is False


# -- AGT063 -------------------------------------------------------------------

def _case(turns: int, tools: bool) -> dict:
    tool_uses = [{"name": "lookup_policy", "args": {}}] if tools else []
    return {"eval_id": "c", "conversation": [
        {"user_content": {"parts": [{"text": "x"}]},
         "intermediate_data": {"tool_uses": tool_uses}} for _ in range(turns)
    ]}


def test_caching_with_only_one_request_per_case_is_noted(agent_dir):
    facts = extract_module(agent_dir("cached_agent"))
    found = cache_suite_findings(facts, [{"eval_cases": [_case(1, False), _case(1, False)]}])
    assert [f.id for f in found] == ["AGT063"]
    assert found[0].severity == "info"
    assert "cannot show a cache hit" in found[0].message


@pytest.mark.parametrize("cases", [
    [_case(2, False)],
    [_case(1, True)],
    [{"eval_id": "s", "conversation_scenario": {"starting_prompt": "hi"}}],
])
def test_a_case_with_a_second_request_satisfies_agt063(agent_dir, cases):
    facts = extract_module(agent_dir("cached_agent"))
    assert cache_suite_findings(facts, [{"eval_cases": cases}]) == []


def test_agt063_needs_caching_and_cases(agent_dir):
    assert cache_suite_findings(extract_module(agent_dir("flat_good")),
                                [{"eval_cases": [_case(1, False)]}]) == []
    assert cache_suite_findings(extract_module(agent_dir("cached_agent")), []) == []


# -- AGT064 / AGT065 ----------------------------------------------------------

def test_global_instruction_stays_on_agt002_not_a_new_id(tmp_path):
    agent = project(tmp_path, '''
        from google.adk.agents import Agent
        root_agent = Agent(name="a", model="gemini-flash-latest", instruction="Go.",
                           global_instruction="Be terse.")
    ''')
    facts = extract_module(agent)
    assert facts.root_agent().has_global_instruction is True
    assert "AGT002" in ids(facts) and "AGT064" not in ids(facts)


def test_a_plugin_imported_from_its_private_module_is_reported(tmp_path):
    agent = project(tmp_path, '''
        from google.adk.agents import Agent
        from google.adk.apps import App
        from google.adk.plugins._tool_call_integrity_plugin import ToolCallIntegrityPlugin
        root_agent = Agent(name="a", model="gemini-flash-latest", instruction="Go.")
        app = App(name="agent_pkg", root_agent=root_agent, plugins=[ToolCallIntegrityPlugin()])
    ''')
    finding = next(f for f in extract_module(agent).findings if f.id == "AGT065")
    assert finding.severity == "warn"
    assert "from google.adk.plugins import ToolCallIntegrityPlugin" in finding.fix
    assert "2.11" in finding.fix


def test_the_public_plugin_import_is_clean(tmp_path):
    agent = project(tmp_path, '''
        from google.adk.agents import Agent
        from google.adk.plugins import ToolCallIntegrityPlugin
        root_agent = Agent(name="a", model="gemini-flash-latest", instruction="Go.")
    ''')
    assert "AGT065" not in ids(extract_module(agent))


# -- the upstream samples' shapes ---------------------------------------------

def test_upstream_static_instruction_sample_shape_is_clean(tmp_path):
    """Content/Part wrapping a module constant, plus an InstructionProvider for
    the dynamic part and no App: the static_instruction sample's shape."""
    agent = project(tmp_path, '''
        from google.adk.agents import Agent
        from google.genai import types

        STATIC_INSTRUCTION_TEXT = """You are a digital pet. Stay in character."""

        def provide_dynamic_instruction(ctx):
            hunger = ctx.state.get("hunger", "full")
            return f"CURRENT HUNGER STATE: {hunger}"

        def eat(food: str) -> dict:
            """Feed the pet. Args: food: what it eats."""
            return {"status": "ok"}

        root_agent = Agent(
            name="digital_pet",
            static_instruction=types.Content(
                role="user", parts=[types.Part(text=STATIC_INSTRUCTION_TEXT)]),
            instruction=provide_dynamic_instruction,
            tools=[eat],
        )
    ''')
    facts = extract_module(agent)
    agent_spec = facts.root_agent()
    assert agent_spec.static_instruction_len == len("You are a digital pet. Stay in character.")
    assert agent_spec.instruction_is_dynamic is True
    assert not CACHE_IDS & set(ids(facts))


def test_upstream_cache_analysis_sample_shape_is_clean(tmp_path):
    """A large literal `instruction`, no static_instruction, and an App with a
    ContextCacheConfig: caching is on, so AGT061 has nothing to say."""
    agent = project(tmp_path, f'''
        from google.adk.agents import Agent
        from google.adk.agents.context_cache_config import ContextCacheConfig
        from google.adk.apps.app import App

        cache_analysis_agent = Agent(name="cache_analysis_assistant", instruction="""{LARGE}""")
        cache_analysis_app = App(
            name="cache_analysis",
            root_agent=cache_analysis_agent,
            context_cache_config=ContextCacheConfig(
                min_tokens=4096, ttl_seconds=600, cache_intervals=3),
        )
        app = cache_analysis_app
        root_agent = cache_analysis_agent
    ''')
    facts = extract_module(agent)
    assert facts.app.context_cache_config == {
        "min_tokens": 4096, "ttl_seconds": 600, "cache_intervals": 3,
    }
    # ~9k chars is ~2.2k tokens, under its own min_tokens=4096: AGT067 is a
    # legitimate note there, and nothing else fires.
    assert set(ids(facts)) & CACHE_IDS == {"AGT067"}


@pytest.mark.parametrize("sample", ["cache_analysis", "static_instruction"])
def test_the_real_upstream_samples_raise_no_caching_finding(sample):
    path = UPSTREAM_SAMPLES / sample
    if not path.is_dir():
        pytest.skip("the adk-python checkout is not beside the plugin")
    facts = extract_module(path)
    # cache_analysis: 11,800 chars + 7 tools ~ 3,370 tokens < its min_tokens=4096.
    expected = {"AGT067"} if sample == "cache_analysis" else set()
    assert set(ids(facts)) & CACHE_IDS == expected
    if expected:
        assert facts.root_agent().cache_prefix_tokens_estimate == 3370


# -- AGT067 -------------------------------------------------------------------

def test_a_prefix_below_the_floor_is_noted(agent_dir):
    facts = extract_module(agent_dir("cached_agent_broken"))
    finding = next(f for f in facts.findings if f.id == "AGT067")
    assert finding.severity == "info"
    assert finding.message == (
        "context caching is enabled on App 'cached_agent_broken' with min_tokens=2048, but "
        "the static prefix is about 674 tokens (1,738 chars of instruction plus 4 tool "
        "declarations): ADK only creates a cache once the previous request reached "
        "min_tokens, and the model floor is 2048 (Gemini 2.5) or 4096 (Gemini 3), so early "
        "turns never qualify and later hits are mostly history, not the prefix"
    )
    assert "32k" in finding.fix


def test_the_clean_fixture_prefix_clears_the_floor(agent_dir):
    agent = extract_module(agent_dir("cached_agent")).root_agent()
    assert agent.cache_prefix_tokens_estimate >= 2048
    assert agent.cache_prefix_tokens_estimate == agent.static_instruction_len // 4 + 4 * 60


@pytest.mark.parametrize("chars, min_tokens, fires", [
    (9000, 0, False),        # 2,250 tokens >= the 2048 floor
    (7000, 0, True),         # 1,750 tokens < 2048
    (9000, 4096, True),      # above the floor, below its own min_tokens
])
def test_agt067_threshold_is_the_larger_of_min_tokens_and_the_floor(tmp_path, chars, min_tokens,
                                                                    fires):
    agent = project(tmp_path, f'''
        from google.adk.agents import Agent
        from google.adk.agents.context_cache_config import ContextCacheConfig
        from google.adk.apps import App
        RULES = """{"x" * chars}"""
        root_agent = Agent(name="a", model="gemini-flash-latest", static_instruction=RULES,
                           instruction="Go.")
        app = App(name="agent_pkg", root_agent=root_agent,
                  context_cache_config=ContextCacheConfig(min_tokens={min_tokens}))
    ''')
    assert ("AGT067" in ids(extract_module(agent))) is fires


def test_agt067_skips_a_runtime_built_prefix(tmp_path):
    agent = project(tmp_path, '''
        from google.adk.agents import Agent
        from google.adk.agents.context_cache_config import ContextCacheConfig
        from google.adk.apps import App
        def build(ctx):
            return "rules"
        root_agent = Agent(name="a", model="gemini-flash-latest", instruction=build)
        app = App(name="agent_pkg", root_agent=root_agent,
                  context_cache_config=ContextCacheConfig())
    ''')
    assert "AGT067" not in ids(extract_module(agent))
