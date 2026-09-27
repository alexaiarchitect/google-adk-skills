"""Evalset loading, key normalization, validation and legacy migration."""

from adk_skills.evalset import (
    EVAL_SET_ID_RE, case_category, key_style, load_evalset, migrate_legacy,
    normalize_keys, sanitize_eval_set_id, stats, tool_sequence, validate_evalset,
)
from adk_skills.report import GapReport


def ids(report: GapReport) -> set[str]:
    return {f.id for f in report.findings}


def test_camel_keys_normalize_but_tool_arguments_are_left_alone():
    doc = normalize_keys({
        "evalSetId": "x",
        "evalCases": [{"evalId": "c", "sessionInput": {"appName": "app"}}],
        "toolUses": [{"name": "t", "args": {"costCentre": "engineering", "amountUsd": 1}}],
    })
    assert doc["eval_set_id"] == "x"
    assert doc["eval_cases"][0]["session_input"]["app_name"] == "app"
    assert doc["tool_uses"][0]["args"] == {"costCentre": "engineering", "amountUsd": 1}, (
        "tool argument names belong to the agent's tools, not to ADK"
    )


def test_state_values_are_left_alone_too():
    doc = normalize_keys({"sessionInput": {"state": {"lastClaimId": "CLM-1"}}})
    assert doc["session_input"]["state"] == {"lastClaimId": "CLM-1"}


def test_key_style_detects_both_conventions():
    assert key_style({"evalSetId": 1}) == {"camel"}
    assert key_style({"eval_set_id": 1}) == {"snake"}
    assert key_style({"eval_set_id": 1, "evalCases": []}) == {"camel", "snake"}


def test_valid_evalset_produces_no_errors(fixtures):
    loaded = load_evalset(fixtures / "agents/recipes_good/tests/eval/evalsets/happy.evalset.json")
    report = validate_evalset(
        loaded, expected_app_name="app",
        known_tools={"lookup_policy", "check_cost_centre_funds", "submit_claim", "reject_claim"},
        reference_metrics=False,
    )
    assert report.count("error") == 0
    assert report.count("warn") == 0


def test_camel_case_inside_cases_is_accepted(fixtures):
    """Every model below `EvalSet` has a camelCase alias generator, so camel
    keys inside a case parse exactly as snake ones do."""
    loaded = load_evalset(fixtures / "evalsets/camel.evalset.json")
    assert loaded.camel_top_level == []
    report = validate_evalset(loaded, expected_app_name="app")
    assert report.count("error") == 0
    assert loaded.doc["eval_cases"][0]["eval_id"] == "happy_meals_within_limit"


def test_camel_case_at_the_top_level_is_an_error(fixtures):
    """`EvalSet` is a plain BaseModel with no alias generator, so `evalSetId`
    and `evalCases` are not read at all and both required fields come back
    missing. Accepting this quietly is how a file that cannot load gets shipped."""
    loaded = load_evalset(fixtures / "evalsets/camel_toplevel.evalset.json")
    assert loaded.camel_top_level == ["evalCases", "evalSetId"]
    report = validate_evalset(loaded, expected_app_name="app")
    finding = [f for f in report.findings if f.id == "EVS007"][0]
    assert finding.severity == "error"
    assert "evalSetId" in finding.message


def test_legacy_list_format_is_detected_and_offered_a_migration(fixtures):
    loaded = load_evalset(fixtures / "evalsets/legacy.test.json")
    assert loaded.is_legacy
    assert loaded.legacy_shape == "wrapped"
    assert "EVS050" in ids(validate_evalset(loaded))


def test_legacy_cases_are_counted_rather_than_scored_as_an_empty_suite(fixtures):
    """A legacy file is migrated on read, so every counter sees its cases.
    Counting them as zero is how a project with a real suite gets graded L0."""
    loaded = load_evalset(fixtures / "evalsets/legacy.test.json")
    assert stats(loaded.doc)["cases"] == 1
    assert tool_sequence(loaded.doc["eval_cases"][0]) == [
        "lookup_policy", "check_cost_centre_funds"
    ]


def test_flat_turn_legacy_is_an_error_because_adk_eval_cannot_read_it(fixtures):
    """`[{query, expected_tool_use, reference}]` has no `data` key, so
    `load_eval_set_from_file` raises KeyError('data'). Only
    `AgentEvaluator.evaluate()` handles it, by wrapping the whole list as one
    case - which is what the migration does here."""
    loaded = load_evalset(fixtures / "evalsets/flat_legacy.test.json")
    assert loaded.is_legacy and loaded.is_flat_legacy
    found = ids(validate_evalset(loaded))
    assert "EVS050" in found and "EVS051" in found

    summary = stats(loaded.doc)
    assert summary["cases"] == 1, "AgentEvaluator makes one case of the whole list"
    assert summary["turns"] == 2, "and keeps every turn"
    assert tool_sequence(loaded.doc["eval_cases"][0]) == [
        "lookup_policy", "check_cost_centre_funds", "submit_claim"
    ]


def test_broken_evalset_reports_each_planted_problem(fixtures):
    loaded = load_evalset(fixtures / "evalsets/broken.evalset.json")
    report = validate_evalset(
        loaded, expected_app_name="app",
        known_tools={"lookup_policy", "submit_claim", "check_cost_centre_funds", "reject_claim"},
        reference_metrics=True,
    )
    found = ids(report)
    assert "EVS003" in found, "eval_set_id with a dash and a dot is rejected on save"
    assert "EVS012" in found, "duplicate eval_id"
    assert "EVS014" in found, "conversation and conversation_scenario together"
    assert "EVS016" in found, "empty conversation"
    assert "EVS023" in found, "user_content with no text"
    assert "EVS030" in found, "missing session_input"
    assert "EVS033" in found, "app_name that does not match the agent"
    assert "EVS035" in found, "tool use without a name"
    assert "EVS036" in found, "call to a tool the agent does not define"
    assert "EVS037" in found, "args that are not an object"
    assert "EVS040" in found, "typo'd top-level key that ADK would ignore"
    assert "EVS060" in found, "eval_id that says nothing"
    assert "EVS083" in found, "per-case rubric with no type is never read"


def test_missing_reference_is_an_error_only_when_a_reference_metric_is_configured(fixtures):
    loaded = load_evalset(fixtures / "agents/recipes_good/tests/eval/evalsets/happy.evalset.json")
    without = validate_evalset(loaded, reference_metrics=False)
    assert "EVS025" not in ids(without) and "EVS026" in ids(without)
    with_reference = validate_evalset(loaded, reference_metrics=True)
    assert "EVS025" in ids(with_reference)


def test_scenario_case_is_valid_without_a_conversation(fixtures):
    loaded = load_evalset(fixtures / "evalsets/scenario.evalset.json")
    assert validate_evalset(loaded, expected_app_name="app").count("error") == 0


def test_unreadable_file_is_reported_not_raised(tmp_path):
    bad = tmp_path / "x.evalset.json"
    bad.write_text("{not json")
    assert "EVS001" in ids(validate_evalset(load_evalset(bad)))


def test_stats_counts_cases_tools_and_categories(fixtures):
    loaded = load_evalset(fixtures / "agents/recipes_good/tests/eval/evalsets/edge.evalset.json")
    summary = stats(loaded.doc)
    assert summary["cases"] == 2
    assert summary["by_category"] == {"edge": 2}
    assert "reject_claim" in summary["tools_referenced"]


def test_tool_sequence_is_flat_and_ordered(fixtures):
    loaded = load_evalset(fixtures / "agents/recipes_good/tests/eval/evalsets/happy.evalset.json")
    case = loaded.doc["eval_cases"][0]
    assert tool_sequence(case) == ["lookup_policy", "check_cost_centre_funds", "submit_claim"]


def test_case_category_reads_the_naming_convention():
    assert case_category("happy_travel_within_policy") == "happy"
    assert case_category("edge_frozen_cost_centre") == "edge"
    assert case_category("adversarial_conflicting_policy") == "adversarial"
    assert case_category("memory_recalls_cost_centre") == "memory"
    assert case_category("regression_issue_412") == "regression"
    assert case_category("something_else") == "uncategorized"


def test_legacy_migration_produces_a_valid_evalset(fixtures):
    loaded = load_evalset(fixtures / "evalsets/legacy.test.json")
    migrated = migrate_legacy(loaded.raw, eval_set_id="migrated", app_name="app")
    assert EVAL_SET_ID_RE.match(migrated["eval_set_id"])
    case = migrated["eval_cases"][0]
    assert case["eval_id"] == "happy_equipment_claim"
    assert tool_sequence(case) == ["lookup_policy", "check_cost_centre_funds"]
    assert case["conversation"][0]["intermediate_data"]["tool_uses"][0]["args"] == {
        "req": {"category": "equipment"}
    }
    assert case["session_input"]["app_name"] == "app"

    from adk_skills.evalset import LoadedEvalSet

    report = validate_evalset(LoadedEvalSet("migrated.json", migrated, migrated, "evalset"))
    assert report.count("error") == 0


def test_sanitize_eval_set_id_makes_a_legal_id():
    assert sanitize_eval_set_id("my-eval.set v2") == "my_eval_set_v2"
    assert EVAL_SET_ID_RE.match(sanitize_eval_set_id("123start"))


def test_unknown_keys_are_errors_exactly_where_adk_forbids_them(tmp_path):
    """`Invocation`, `IntermediateData`, `ConversationScenario` and `Rubric` are
    all extra="forbid", so a typo in any of them raises at load. `EvalCase` and
    `EvalSet` accept extras, so the same typo there is silent - a warning."""
    import json

    path = tmp_path / "x.evalset.json"
    path.write_text(json.dumps({
        "eval_set_id": "strict",
        "typo_on_the_set": 1,
        "eval_cases": [{
            "eval_id": "happy_one",
            "typo_on_the_case": 1,
            "conversation": [{
                "invocation_id": "inv_1",
                "typo_on_the_invocation": 1,
                "user_content": {"parts": [{"text": "hi"}], "role": "user"},
                "final_response": {"parts": [{"text": "ok"}], "role": "model"},
                "intermediate_data": {
                    "tool_uses": [{"name": "lookup_policy", "args": {}}],
                    "invocation_events": [],
                },
            }],
            "session_input": {"app_name": "app", "user_id": "evaluator"},
        }],
    }))
    report = validate_evalset(load_evalset(path))
    severities = {f.id: f.severity for f in report.findings}
    assert severities["EVS021"] == "error", "Invocation forbids extras"
    assert severities["EVS047"] == "error", "IntermediateData forbids extras"
    assert severities["EVS013"] == "warn", "EvalCase allows extras, so this is only a typo"
    assert severities["EVS040"] == "warn", "EvalSet ignores extras"


def _case_with(invocation: dict) -> dict:
    return {
        "eval_set_id": "captured",
        "eval_cases": [{
            "eval_id": "happy_travel_within_policy",
            "conversation": [invocation],
            "session_input": {"app_name": "app", "user_id": "evaluator"},
        }],
    }


def test_a_case_captured_from_a_2_10_run_validates(tmp_path):
    """`Invocation.duration` is new in ADK 2.10 and legal: a case captured from a
    2.10 run carries it, and flagging it would call a real field a typo."""
    import json

    path = tmp_path / "x.evalset.json"
    path.write_text(json.dumps(_case_with({
        "invocation_id": "inv_1",
        "user_content": {"parts": [{"text": "Submit CLM-000412 for 900 dollars of travel."}],
                         "role": "user"},
        "final_response": {"parts": [{"text": "Submitted CLM-000412."}], "role": "model"},
        "intermediate_data": {"tool_uses": [{"name": "lookup_policy", "args": {}}]},
        "creation_timestamp": 1790000000.5,
        "duration": 2.5,
    })))
    report = validate_evalset(load_evalset(path))
    assert report.count("error") == 0, [f.id for f in report.findings if f.severity == "error"]
    assert "EVS021" not in ids(report)
    # Measured on the wheels: 2.9.2 refuses `duration` as an extra input, 2.10
    # reads it, so the note is about portability rather than a mistake.
    note = [f for f in report.findings if f.id == "EVS049"]
    assert len(note) == 1 and note[0].severity == "info"
    assert "2.9.2 refuses the file" in (note[0].fix or "")


def test_the_captured_invocation_events_shape_is_accepted(tmp_path):
    """`intermediate_data` is a union: `{"invocation_events": [...]}` is what a
    captured run writes, and it is not an unrecognized key."""
    import json

    path = tmp_path / "x.evalset.json"
    path.write_text(json.dumps(_case_with({
        "invocation_id": "inv_1",
        "user_content": {"parts": [{"text": "hi"}], "role": "user"},
        "final_response": {"parts": [{"text": "ok"}], "role": "model"},
        "intermediate_data": {"invocation_events": [
            {"author": "app", "content": {"parts": [{"text": "ok"}], "role": "model"},
             "usage_metadata": {"total_token_count": 312}, "model_version": "gemini-flash-latest"},
        ]},
        "duration": 1.25,
    })))
    report = validate_evalset(load_evalset(path))
    assert report.count("error") == 0, [f.id for f in report.findings if f.severity == "error"]


def test_mixing_the_two_intermediate_data_shapes_is_an_error(tmp_path):
    import json

    path = tmp_path / "x.evalset.json"
    path.write_text(json.dumps(_case_with({
        "invocation_id": "inv_1",
        "user_content": {"parts": [{"text": "hi"}], "role": "user"},
        "intermediate_data": {"tool_uses": [], "invocation_events": []},
    })))
    report = validate_evalset(load_evalset(path))
    message = [f.message for f in report.findings if f.id == "EVS047"][0]
    assert "mixes the two shapes" in message


def test_a_scenario_typo_is_an_error(tmp_path):
    import json

    path = tmp_path / "x.evalset.json"
    path.write_text(json.dumps({
        "eval_set_id": "scenarios",
        "eval_cases": [{
            "eval_id": "memory_simulated",
            "conversation_scenario": {
                "starting_prompt": "hi",
                "conversation_plan": "Ask once, then stop.",
                "user_persona_typo": {},
            },
            "session_input": {"app_name": "app", "user_id": "evaluator"},
        }],
    }))
    assert "EVS093" in ids(validate_evalset(load_evalset(path)))


def test_tool_responses_that_do_not_pair_with_calls_are_noted(tmp_path):
    import json

    path = tmp_path / "x.evalset.json"
    path.write_text(json.dumps({
        "eval_set_id": "pairs",
        "eval_cases": [{
            "eval_id": "happy_one",
            "conversation": [{
                "invocation_id": "inv_1",
                "user_content": {"parts": [{"text": "hi"}], "role": "user"},
                "final_response": {"parts": [{"text": "ok"}], "role": "model"},
                "intermediate_data": {
                    "tool_uses": [
                        {"name": "lookup_policy", "args": {}},
                        {"name": "submit_claim", "args": {}},
                    ],
                    "tool_responses": [{"name": "lookup_policy", "response": {}}],
                },
            }],
            "session_input": {"app_name": "app", "user_id": "evaluator"},
        }],
    }))
    finding = [f for f in validate_evalset(load_evalset(path)).findings if f.id == "EVS110"][0]
    assert finding.severity == "info"
    assert "1 tool_responses for 2 tool_uses" in finding.message


def test_intermediate_responses_must_be_author_parts_pairs(tmp_path):
    import json

    path = tmp_path / "x.evalset.json"
    path.write_text(json.dumps({
        "eval_set_id": "pairs",
        "eval_cases": [{
            "eval_id": "happy_one",
            "conversation": [{
                "invocation_id": "inv_1",
                "user_content": {"parts": [{"text": "hi"}], "role": "user"},
                "final_response": {"parts": [{"text": "ok"}], "role": "model"},
                "intermediate_data": {
                    "intermediate_responses": [
                        {"author": "checker", "text": "looks fine"},
                    ],
                },
            }],
            "session_input": {"app_name": "app", "user_id": "evaluator"},
        }],
    }))
    assert "EVS111" in ids(validate_evalset(load_evalset(path)))


def test_no_unknown_tool_error_when_the_agent_cannot_be_read(fixtures, tmp_path):
    """The kwargs_agent fixture binds `export_to_ledger` only under a feature
    flag, and its whole construction is unreadable, so an expected call to it
    cannot be called a mistake."""
    import json

    from adk_skills.tools_ast import extract_module, tool_resolver

    facts = extract_module(fixtures / "agents/kwargs_agent")
    path = tmp_path / "x.evalset.json"
    path.write_text(json.dumps(_case_with({
        "invocation_id": "inv_1",
        "user_content": {"parts": [{"text": "Submit CLM-000412 for 900 dollars of travel."}],
                         "role": "user"},
        "final_response": {"parts": [{"text": "Submitted CLM-000412."}], "role": "model"},
        "intermediate_data": {"tool_uses": [
            {"name": "lookup_policy", "args": {}},
            {"name": "submit_claim", "args": {}},
            {"name": "export_to_ledger", "args": {}},
        ]},
    })))
    report = validate_evalset(
        load_evalset(path),
        known_tools=facts.bound_tool_names,
        tool_resolver=tool_resolver(facts),
    )
    assert "EVS036" not in ids(report), [f.message for f in report.findings if f.id == "EVS036"]
    assert report.count("error") == 0


def test_a_resolver_decides_what_counts_as_an_unknown_tool(fixtures):
    """EVS036 must mean one thing: a name the agent could never emit. A toolset
    or an AgentTool has a real trajectory name and no readable definition, so it
    is not one."""
    from adk_skills.tools_ast import extract_module, tool_resolver

    facts = extract_module(fixtures / "agents/wrapped_tools")
    resolve = tool_resolver(facts)
    assert resolve("google_search").status == "opaque"
    assert resolve("policy_researcher").status == "opaque"
    assert resolve("check_condition_and_escalate").status == "defined"
    assert resolve("notify_finance").status == "unknown"
