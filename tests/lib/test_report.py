"""The shared report type: rendering, severity buckets, exit codes."""

from adk_skills.report import (
    EXIT_FINDINGS, EXIT_INCONCLUSIVE, EXIT_OK, GapReport, Option, exit_code_for,
)


def make_report() -> GapReport:
    report = GapReport(subject="/tmp/project", skill="eval-review")
    report.discovered["layout"] = {"style": "recipes", "cases": 3}
    report.add("EVS002", "error", "evalset", "missing eval_set_id",
               path="/tmp/project/a.json", fix="add one", source="eval_set.py")
    report.add("EVS060", "warn", "evalset", "vague eval_id", locator="eval_cases[0]")
    report.add("EVS026", "info", "evalset", "trajectory only")
    report.add_gap("GAP-CI", "No CI gate", "nothing runs the suite", "use /adk:eval-ci")
    report.add_option(Option(key="1", label="Fix ids", recommended=True, writes=["a.json"]))
    return report


def test_summary_counts_by_severity():
    assert make_report().summary == {"errors": 1, "warnings": 1, "infos": 1}


def test_json_round_trips_every_section():
    import json

    payload = json.loads(make_report().to_json())
    assert payload["schema"] == "adk-skills.gap-report/1"
    assert {f["id"] for f in payload["findings"]} == {"EVS002", "EVS060", "EVS026"}
    assert payload["gaps"][0]["id"] == "GAP-CI"
    assert payload["options"][0]["recommended"] is True


def test_markdown_groups_by_severity_and_keeps_fixes():
    text = make_report().to_md()
    assert text.index("## Errors") < text.index("## Warnings") < text.index("## Notes")
    assert "Fix: add one" in text
    assert "## Missing" in text and "## Options" in text


def test_markdown_omits_the_subject_path_on_every_line():
    report = GapReport(subject="/tmp/project/a.json", skill="eval-review")
    report.add("EVS002", "error", "evalset", "missing eval_set_id", path="/tmp/project/a.json")
    line = [l for l in report.to_md().splitlines() if "EVS002" in l][0]
    assert "/tmp/project/a.json" not in line


def test_exit_codes_follow_the_contract():
    clean = GapReport(subject="x", skill="s")
    assert exit_code_for(clean) == EXIT_OK
    assert exit_code_for(clean, inconclusive=True) == EXIT_INCONCLUSIVE
    assert exit_code_for(make_report()) == EXIT_FINDINGS
