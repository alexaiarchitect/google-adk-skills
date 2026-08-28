"""SOP rule extraction and coverage analysis."""

from adk_skills.coverage import (
    branch_count, extract_sop_rules, matrix, n_plus_one_gaps, shape,
    summarize_cases, tool_coverage,
)
from adk_skills.evalset import load_evalset
from adk_skills.tools_ast import extract_module

TOOLS = {"lookup_policy", "check_cost_centre_funds", "submit_claim", "reject_claim"}


def rules_for(agent_path):
    facts = extract_module(agent_path)
    agent = facts.root_agent()
    return extract_sop_rules(agent.instruction_text, facts.bound_tool_names)


def test_instruction_splits_into_classified_rules(agent_dir):
    rules = rules_for(agent_dir("flat_good"))
    kinds = {rule.kind for rule in rules}
    assert {"mandatory", "conditional", "prohibition"} <= kinds
    assert branch_count(rules) >= 5


def test_wrapped_lines_are_not_counted_as_separate_rules(agent_dir):
    rules = rules_for(agent_dir("flat_good"))
    assert not any(rule.text.startswith("limit, call") for rule in rules)


def test_a_role_statement_is_not_a_prohibition(agent_dir):
    rules = rules_for(agent_dir("flat_good"))
    first = rules[0]
    assert first.text.startswith("You approve")
    assert first.kind == "informational", "reject/refuse as verbs are not prohibitions"


def test_rules_are_linked_to_the_tools_they_name(agent_dir):
    rules = rules_for(agent_dir("flat_good"))
    prohibitions = [r for r in rules if r.kind == "prohibition" and r.tools]
    assert {"submit_claim", "check_cost_centre_funds"} <= {t for r in prohibitions for t in r.tools}


def test_cases_are_matched_to_the_rules_they_exercise(agent_dir):
    rules = rules_for(agent_dir("recipes_good"))
    docs = {}
    for name in ("happy", "edge"):
        loaded = load_evalset(
            agent_dir("recipes_good") / f"tests/eval/evalsets/{name}.evalset.json"
        )
        docs[loaded.path] = loaded.doc
    cases = summarize_cases(docs)
    assert len(cases) == 3
    result = matrix(rules, cases)
    covered = {rule.index for rule, ids in
               ((r, result.coverage.get(r.index)) for r in rules) if ids}
    assert covered, "the policy rule is exercised by every case"
    assert result.uncovered_branches(), "three cases cannot cover nine branches"


def test_branch_rule_reports_the_shortfall(agent_dir):
    rules = rules_for(agent_dir("recipes_good"))
    loaded = load_evalset(agent_dir("recipes_good") / "tests/eval/evalsets/happy.evalset.json")
    gaps = n_plus_one_gaps(rules, summarize_cases({loaded.path: loaded.doc}))
    assert gaps["minimum_cases"] == gaps["branches"] + 1
    assert gaps["shortfall"] > 0


def _cases(count: int, category: str = "happy", turns: int = 1):
    from adk_skills.coverage import CaseSummary

    return [
        CaseSummary(eval_id=f"{category}_{i}", category=category, tools=[], turns=turns, text="")
        for i in range(count)
    ]


def test_shape_reports_missing_adversarial_and_multi_turn():
    result = shape(_cases(6))
    assert result["verdict"] == "gaps"
    assert any("adversarial" in note for note in result["notes"])
    assert any("multi-turn" in note for note in result["notes"])


def test_a_suite_too_small_to_have_a_shape_is_not_told_to_rebalance(agent_dir):
    """Two cases are not "10% adversarial" short; they are two cases. Reporting
    a 70/20/10 imbalance on them is advice about a ratio that does not exist."""
    loaded = load_evalset(agent_dir("recipes_good") / "tests/eval/evalsets/edge.evalset.json")
    result = shape(summarize_cases({loaded.path: loaded.doc}))
    assert result["total"] == 2
    assert result["verdict"] == "too_small"
    assert result["notes"] == []
    assert result["suppressed_notes"], "the observations are kept, just not reported as gaps"


def test_empty_suite_is_reported_rather_than_dividing_by_zero():
    assert shape([])["verdict"] == "empty"


def test_tool_coverage_finds_untested_and_unknown_tools(agent_dir):
    loaded = load_evalset(agent_dir("recipes_good") / "tests/eval/evalsets/happy.evalset.json")
    cases = summarize_cases({loaded.path: loaded.doc})
    result = tool_coverage(TOOLS, cases)
    assert set(result["exercised"]) == {"lookup_policy", "check_cost_centre_funds", "submit_claim"}
    assert result["never_exercised"] == ["reject_claim"]
    assert result["unknown_in_cases"] == []


def test_no_instruction_yields_no_rules():
    assert extract_sop_rules(None) == []
    assert extract_sop_rules("") == []
