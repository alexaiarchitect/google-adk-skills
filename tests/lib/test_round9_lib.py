"""Rules added against ADK main through 2026-11-04 (round 9): two skills on one
agent whose descriptions overlap (AGT083) and an output cap too small for a
thinking model (AGT084)."""

import textwrap
from pathlib import Path

import pytest

from adk_skills.skills import (
    OVERLAP_CONTAINMENT, OVERLAP_JACCARD, OVERLAP_MIN_TOKENS, description_overlap,
    description_tokens, descriptions_overlap,
)
from adk_skills.tools_ast import (
    MIN_THINKING_OUTPUT_TOKENS, THINKING_MODEL_ALIASES, THINKING_MODEL_PREFIXES,
    extract_module, is_thinking_model_id,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _ids(facts, fid):
    return [f for f in facts.findings if f.id == fid]


def _agent(tmp_path, source):
    (tmp_path / "__init__.py").write_text("from . import agent  # noqa: F401\n")
    (tmp_path / "agent.py").write_text(textwrap.dedent(source))
    return extract_module(tmp_path)


# -- AGT083: the scoring -------------------------------------------------------

def test_thresholds_are_the_measured_ones():
    assert (OVERLAP_JACCARD, OVERLAP_CONTAINMENT, OVERLAP_MIN_TOKENS) == (0.5, 0.8, 4)


def test_tokens_drop_stopwords_and_single_letters():
    assert description_tokens("Use this skill to check a claim, and the 2 receipts") == {
        "check", "claim", "receipts"}


def test_the_fixture_pair_is_a_near_duplicate():
    general = ("Checks an expense claim against the travel, meals and equipment policy "
               "before it is submitted, and says which rule a rejected claim broke.")
    hotel = ("Checks an expense claim against the travel, meals, hotel and equipment "
             "policy before it is submitted, and says which rule a rejected claim broke.")
    hit, jaccard, containment = descriptions_overlap(general, hotel)
    assert hit and round(jaccard, 2) == 0.93 and containment == 1.0


def test_the_closest_distinct_upstream_pair_stays_below():
    # adk-python .agents/skills adk-unit-design vs adk-unit-guide shape: J 0.33 / C 0.50.
    a = "Writes an architecture document for one code unit: modules, flows and decisions."
    b = "Writes a developer guide for one code unit: setup, examples and common tasks."
    hit, jaccard, containment = descriptions_overlap(a, b)
    assert not hit and jaccard < OVERLAP_JACCARD and containment < OVERLAP_CONTAINMENT


@pytest.mark.parametrize("short, expected", [
    ("claim receipt merchant", False),            # 3 tokens, fully contained: too short
    ("claim receipt merchant amount", True),      # 4 tokens, fully contained
])
def test_containment_needs_four_tokens_in_the_shorter(short, expected):
    long_ = "Checks claim receipt merchant amount date currency and the approver's name."
    hit, _, containment = descriptions_overlap(short, long_)
    assert containment == 1.0 and hit is expected


def test_jaccard_edge_is_inclusive():
    # 4 shared of 8 distinct = 0.5 exactly; containment 4/6 stays under 0.8.
    a = "alpha beta gamma delta epsilon zeta"
    b = "alpha beta gamma delta eta theta"
    jaccard, containment, short = description_overlap(a, b)
    assert jaccard == 0.5 and containment < 0.8 and short == 6
    assert descriptions_overlap(a, b)[0]
    assert not descriptions_overlap(a, "alpha beta gamma eta theta iota")[0]


def test_empty_descriptions_never_overlap():
    assert descriptions_overlap("", "Checks a claim against the policy") == (False, 0.0, 0.0)


# -- AGT083: the rule ----------------------------------------------------------

def test_overlap_fixture_reports_one_pair():
    facts = extract_module(FIXTURES / "agents/skills_overlap")
    [finding] = _ids(facts, "AGT083")
    assert finding.severity == "info" and finding.area == "skill"
    assert finding.message.startswith(
        "skills 'claim-policy' and 'claim-policy-hotel' describe themselves with mostly the "
        "same words (Jaccard 0.93, containment 1.00): the model chooses a skill from "
        "`list_skills`' name and description alone")
    assert "EAGER discovery" in finding.message
    assert finding.fix.startswith("say what each does and when")
    assert "list_skills" in finding.source and "Five rules for evaluating agent skills" in finding.source
    assert finding.path.endswith("skills/claim-policy")


@pytest.mark.parametrize("name", ["skilled_agent", "skilled_agent_broken", "flat_good",
                                  "recipes_good", "cached_agent"])
def test_existing_fixtures_stay_clean(name):
    assert not _ids(extract_module(FIXTURES / "agents" / name), "AGT083")


INLINE = '''
from google.adk.agents import Agent
from google.adk.skills import models
from google.adk.tools.skill_toolset import SkillToolset

a = models.Skill(frontmatter=models.Frontmatter(name="meal-limits",
    description="Checks meal claims against the daily meal limit for the trip city."),
    instructions="1. Look up the city.")
b = models.Skill(frontmatter=models.Frontmatter(name="meal-limits-intl",
    description="Checks meal claims against the daily meal limit for the trip city abroad."),
    instructions="1. Convert the currency.")
c = models.Skill(frontmatter=models.Frontmatter(name="mileage",
    description="Works out the mileage allowance for a car journey from the odometer readings."),
    instructions="1. Multiply.")
{toolsets}
'''


def test_inline_skills_in_one_toolset(tmp_path):
    facts = _agent(tmp_path, INLINE.format(toolsets=(
        "skills = SkillToolset(skills=[a, b, c])\n"
        "root_agent = Agent(name='x', model='gemini-flash-latest', instruction='Do it.', "
        "tools=[skills])")))
    [finding] = _ids(facts, "AGT083")
    assert "'meal-limits' and 'meal-limits-intl'" in finding.message
    assert finding.locator and finding.locator.startswith("line ")


def test_two_toolsets_on_one_agent_are_one_catalog(tmp_path):
    facts = _agent(tmp_path, INLINE.format(toolsets=(
        "one = SkillToolset(skills=[a, c])\n"
        "two = SkillToolset(skills=[b])\n"
        "root_agent = Agent(name='x', model='gemini-flash-latest', instruction='Do it.', "
        "tools=[one, two])")))
    assert len(_ids(facts, "AGT083")) == 1


def test_siblings_on_different_agents_do_not_compete(tmp_path):
    facts = _agent(tmp_path, INLINE.format(toolsets=(
        "one = SkillToolset(skills=[a, c])\n"
        "two = SkillToolset(skills=[b])\n"
        "helper = Agent(name='helper', model='gemini-flash-latest', instruction='Do it.', "
        "tools=[two])\n"
        "root_agent = Agent(name='x', model='gemini-flash-latest', instruction='Do it.', "
        "tools=[one], sub_agents=[helper])")))
    assert not _ids(facts, "AGT083")


# -- AGT084 --------------------------------------------------------------------

def test_thinking_family_is_named_once():
    assert THINKING_MODEL_PREFIXES == ("gemini-2.5-", "gemini-3")
    assert THINKING_MODEL_ALIASES == ("gemini-flash-latest", "gemini-pro-latest",
                                      "gemini-flash-lite-latest")
    assert MIN_THINKING_OUTPUT_TOKENS == 1024


@pytest.mark.parametrize("model, expected", [
    ("gemini-2.5-flash", True), ("gemini-2.5-pro", True), ("gemini-3.8-flash", True),
    ("gemini-3-pro", True), ("gemini-flash-latest", True), ("gemini-pro-latest", True),
    ("gemini-flash-lite-latest", True), ("models/gemini-2.5-flash", True),
    ("gemini-2.0-flash", False), ("gemini-1.5-pro", False), ("gemini-3.8-live", False),
    ("gemini-2.5-flash-preview-tts", False), (None, False), ("", False),
])
def test_is_thinking_model_id(model, expected):
    assert is_thinking_model_id(model) is expected


def test_low_token_cap_fixture():
    facts = extract_module(FIXTURES / "agents/low_token_cap")
    [finding] = _ids(facts, "AGT084")
    assert finding.severity == "info"
    assert finding.message == (
        "max_output_tokens=300 on gemini-flash-latest is a ceiling, not a thinking budget: a "
        "hard turn spends the cap on reasoning and stops mid-chain, so the reply is truncated "
        "or wrong; easy turns use the same thinking tokens either way")
    assert finding.fix == (
        "bound the answer with the instruction or `thinking_config`, not the output size; "
        "keep max_output_tokens at least 1024 on thinking models, or drop it")
    assert "Cost per task" in finding.source and "developers.googleblog.com" in finding.source
    assert not _ids(facts, "AGT016"), "a config object is not a misplaced kwarg"


AGENT = '''
import os
from google.adk.agents import Agent
from google.genai import types
{prelude}
root_agent = Agent(name="x", model={model}, instruction="Do it.",
                   generate_content_config={gcc})
'''


def _agt084(tmp_path, gcc, model='"gemini-2.5-flash"', prelude=""):
    facts = _agent(tmp_path, AGENT.format(gcc=gcc, model=model, prelude=prelude))
    return _ids(facts, "AGT084")


@pytest.mark.parametrize("gcc, prelude", [
    ("types.GenerateContentConfig(max_output_tokens=300)", ""),
    ("GCC", "GCC = types.GenerateContentConfig(max_output_tokens=512)"),
    ("types.GenerateContentConfig(max_output_tokens=CAP)", "CAP = 256"),
    ('{"max_output_tokens": 1023}', ""),
    ("types.GenerateContentConfig(max_output_tokens=300, thinking_config="
     "types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW))", ""),
])
def test_low_caps_fire(tmp_path, gcc, prelude):
    assert len(_agt084(tmp_path, gcc, prelude=prelude)) == 1


@pytest.mark.parametrize("gcc", [
    "types.GenerateContentConfig(max_output_tokens=1024)",
    "types.GenerateContentConfig(temperature=0.1)",
    "types.GenerateContentConfig(max_output_tokens=300, "
    "thinking_config=types.ThinkingConfig(thinking_budget=0))",
    "types.GenerateContentConfig(max_output_tokens=200, "
    "thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL))",
    '{"max_output_tokens": 200, "thinking_config": {"thinking_level": "minimal"}}',
    "types.GenerateContentConfig(max_output_tokens=LIMIT)",
])
def test_exempt_or_unreadable_caps_stay_quiet(tmp_path, gcc):
    assert not _agt084(tmp_path, gcc)


@pytest.mark.parametrize("model", [
    '"gemini-2.0-flash"',            # not a thinking model
    'os.getenv("MODEL_NAME")',       # env-only: no id to judge
    "pick_model()",                  # unknown
    '"gemini-3.8-live"',             # a live model
])
def test_non_thinking_or_unknown_models_are_skipped(tmp_path, model):
    gcc = "types.GenerateContentConfig(max_output_tokens=300)"
    assert not _agt084(tmp_path, gcc, model=model)


def test_env_default_and_wrapped_model_are_judged(tmp_path):
    gcc = "types.GenerateContentConfig(max_output_tokens=300)"
    [finding] = _agt084(tmp_path, gcc, model='os.getenv("MODEL_NAME", "gemini-pro-latest")')
    assert "on gemini-pro-latest" in finding.message
    other = tmp_path / "wrapped"
    other.mkdir()
    prelude = "from google.adk.models import Gemini"
    assert _agt084(other, gcc, model='Gemini(model="gemini-3.8-flash")', prelude=prelude)
