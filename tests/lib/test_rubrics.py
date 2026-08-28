"""Rubric wording rules."""

from adk_skills.rubrics import (
    build_restraint_rubric, is_restraint_rubric, dedupe_ids, lint_all,
    lint_rubric_text, suggest_rubric_id,
)

TOOLS = {"submit_claim", "lookup_policy", "check_cost_centre_funds"}


def ids(text: str) -> set[str]:
    return {f.id for f in lint_rubric_text(text, rubric_id="r", known_tools=TOOLS)}


def test_a_graded_scale_is_rejected():
    assert "RUB003" in ids("Rate the response quality on a scale of 1 to 5.")
    assert "RUB003" in ids("Score how well the agent explained itself.")


def test_subjective_wording_is_flagged():
    assert "RUB004" in ids("The response is helpful and appropriate.")


def test_compound_rubrics_are_flagged_because_one_bit_cannot_localize():
    assert "RUB006" in ids(
        "The agent cites the policy and names the cost centre and offers an alternative."
    )


def test_claims_about_the_agents_mind_are_flagged():
    assert "RUB005" in ids("The agent understands what the user wanted.")


def test_a_tool_name_the_agent_does_not_have_is_flagged():
    assert "RUB007" in ids("The agent calls verify_receipt before approving.")


def test_a_good_restraint_rubric_passes_clean():
    text = ("The agent does NOT call `submit_claim` when `lookup_policy` reported the "
            "category as restricted.")
    assert ids(text) == set()
    assert is_restraint_rubric(text)


def test_empty_rubric_is_an_error():
    assert "RUB001" in ids("   ")


def test_restraint_builder_produces_a_clean_typed_rubric():
    rubric = build_restraint_rubric(
        "no_submit_on_frozen", "submit_claim", "the cost centre is frozen"
    )
    assert rubric["type"] == "TOOL_USE_QUALITY"
    assert "does NOT call `submit_claim`" in rubric["rubric_content"]["text_property"]
    assert lint_all([rubric], known_tools=TOOLS) == []


def test_ids_are_made_unique():
    out = dedupe_ids([{"rubric_id": "tone"}, {"rubric_id": "tone"}, {"rubric_id": "tone"}])
    assert [r["rubric_id"] for r in out] == ["tone", "tone_2", "tone_3"]


def test_suggested_ids_are_readable():
    assert suggest_rubric_id("The agent does NOT call submit_claim when the budget is frozen") \
        .startswith("call_submit_claim")
