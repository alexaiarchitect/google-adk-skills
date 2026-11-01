"""Scripts for round 10 (ADK main through 2026-11-05): EVS121 / EVS122 and
invocation-rubric shape checks in validate_evalset.py and discover.py, AGT085 in
lint_agent.py and discover.py, and the LAST-RUN.md generator's comparison
table (only when ADK_SKILLS_LAST_RUN_GENERATOR names it: the generator lives
outside the repository)."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

VALIDATE = "skills/eval-review/scripts/validate_evalset.py"
LINT = "skills/agent-review/scripts/lint_agent.py"
DISCOVER = "skills/adk/scripts/discover.py"
PLUGIN_ROOT = Path(__file__).resolve().parents[2]


def _by_id(result, fid):
    return [f for f in json.loads(result.stdout)["findings"] if f["id"] == fid]


# -- validate_evalset.py --------------------------------------------------------

@pytest.mark.parametrize("args, severity", [
    ((), "warn"), (("--adk-version", "2.13.0"), "warn"), (("--adk-version", "2.9.2"), "warn"),
    (("--adk-version", "2.14.0"), "info"), (("--adk-version", "main"), "info"),
])
def test_validator_evs121_severity(run_script, fixtures, args, severity):
    path = fixtures / "evalsets" / "per_invocation_rubrics.evalset.json"
    result = run_script(VALIDATE, path, "--json", *args)
    assert result.returncode == 0, result.combined
    [finding] = _by_id(result, "EVS121")
    assert finding["severity"] == severity
    assert set(finding) >= {"id", "severity", "area", "message", "path", "fix", "source"}
    assert "memory_claim_amount_then_approver" in finding["locator"]


def test_validator_broken_fixture(run_script, fixtures):
    result = run_script(VALIDATE, fixtures / "evalsets" / "per_invocation_rubrics_broken.evalset.json",
                        "--json")
    assert result.returncode == 1
    assert len(_by_id(result, "EVS083")) == 1
    [repeat] = _by_id(result, "EVS122")
    assert repeat["severity"] == "error"
    assert "conversation[0]" in repeat["locator"]


def _config(tmp_path, metric):
    path = tmp_path / "test_config.json"
    path.write_text(json.dumps({"criteria": {metric: {"threshold": 0.8}}}))
    return path


@pytest.mark.parametrize("metric, fires", [
    ("rubric_based_final_response_quality_v1", True),
    ("rubric_based_tool_use_quality_v1", False),
    ("rubric_based_multi_turn_trajectory_quality_v1", False),
])
def test_validator_config_decides_the_types(run_script, fixtures, tmp_path, metric, fires):
    path = fixtures / "evalsets" / "per_invocation_rubrics.evalset.json"
    result = run_script(VALIDATE, path, "--json", "--config", _config(tmp_path, metric))
    assert bool(_by_id(result, "EVS121")) is fires
    assert result.json()["discovered"]["rubric_types"] is not None


def test_validator_reports_no_types_without_config(run_script, fixtures):
    result = run_script(VALIDATE, fixtures / "evalsets" / "per_invocation_rubrics.evalset.json",
                        "--json")
    assert result.json()["discovered"]["rubric_types"] is None


RECIPE_EVALSETS = (PLUGIN_ROOT.parent / "tmp" / "adk-recipes" / "core" / "python"
                   / "long-horizon-harness" / "tests" / "eval" / "evalsets")


@pytest.mark.skipif(not RECIPE_EVALSETS.is_dir(), reason="tmp/adk-recipes not checked out")
def test_validator_on_recipes(run_script):
    journey = run_script(VALIDATE, RECIPE_EVALSETS / "daily_news_bot_journey.evalset.json", "--json")
    assert journey.json()["discovered"]["rubric_types"] == ["FINAL_RESPONSE_QUALITY"]
    [hit] = _by_id(journey, "EVS121")
    assert hit["severity"] == "warn"
    recall = run_script(VALIDATE, RECIPE_EVALSETS / "memory_recall.evalset.json", "--json")
    assert not _by_id(recall, "EVS121")
    assert _by_id(recall, "EVS083")


def test_discover_surfaces_evs121(run_script, agent_dir, fixtures, tmp_path):
    project = tmp_path / "flat_good"
    shutil.copytree(agent_dir("flat_good"), project)
    target = project / "tests" / "eval" / "evalsets"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy(fixtures / "evalsets" / "per_invocation_rubrics.evalset.json", target)
    for config in project.rglob("*config.json"):
        config.unlink()
    result = run_script(DISCOVER, project, "--json", "--no-probe", "--focus", "evalsets")
    assert len(_by_id(result, "EVS121")) == 1, result.stdout[:2000]


# -- lint_agent.py / discover.py: AGT085 ------------------------------------------

@pytest.mark.parametrize("version, needle", [
    (None, "Extra inputs are not permitted"),
    ("2.13.0", "Extra inputs are not permitted"),
    ("2.14.0", "Input should be greater than or equal to 1"),
])
def test_lint_agt085(run_script, agent_dir, version, needle):
    args = ["--json", "--no-backend"] + (["--adk-version", version] if version else [])
    result = run_script(LINT, *args, agent_dir("parallel_workers_broken"))
    assert result.returncode == 1
    [finding] = _by_id(result, "AGT085")
    assert finding["severity"] == "error" and needle in finding["message"]


def test_discover_reports_agt085(run_script, agent_dir):
    result = run_script(DISCOVER, agent_dir("parallel_workers_broken"), "--json", "--no-probe")
    assert len(_by_id(result, "AGT085")) == 1


@pytest.mark.parametrize("name", ["wrapped_tools", "flat_good", "low_token_cap"])
def test_lint_other_fixtures_silent(run_script, agent_dir, name):
    assert not _by_id(run_script(LINT, "--json", "--no-backend", agent_dir(name)), "AGT085")


# -- the LAST-RUN.md generator ------------------------------------------------------

GENERATOR = os.environ.get("ADK_SKILLS_LAST_RUN_GENERATOR")


def _smoke(cases):
    return {
        "schemaVersion": 1, "claudeVersion": "2.1.318", "durationSeconds": 100, "costUsd": 1.0,
        "partial": False,
        "suite": {"ablation": "with-without", "threshold": 0.8, "concurrency": 2},
        "aggregates": {"casesTotal": len(cases), "casesPassed": 1, "overallScore": 0.5,
                       "meanDelta": 0.25},
        "cases": [{"name": name, "arms": {
            "with": [{"score": w, "turns": 3, "error": None, "graders": [
                {"name": "criteria", "passed": w >= 0.8}, {"name": "skill-fired", "passed": True}]}],
            "without": [{"score": wo, "turns": 2, "error": None, "graders": [
                {"name": "criteria", "passed": wo >= 0.8}]}],
        }} for name, w, wo in cases],
    }


@pytest.mark.skipif(not GENERATOR or not Path(GENERATOR).is_file(),
                    reason="set ADK_SKILLS_LAST_RUN_GENERATOR to the generator script")
def test_generator_compares_with_the_previous_table(tmp_path):
    previous = tmp_path / "LAST-RUN.md"
    previous.write_text(
        "# Last plugin eval run\n\n"
        "| Case | with | without | delta | turns with/without | skill fired | failed graders (with) |\n"
        "|---|---|---|---|---|---|---|\n"
        "| `ci-setup` | 0.5 | 0.5 | +0.0 | 8/7 | yes | criteria |\n"
        "| `pass-at-k` | 1.0 | 1.0 | +0.0 | 7/5 | yes | - |\n"
        "| `retired-case` | 1.0 | 0.0 | +1.0 | 4/4 | yes | - |\n\n"
        "## Rerun\n\n| Case | run scores | failed graders per run |\n|---|---|---|\n"
        "| `ci-setup` | 0.7, 0.7 | criteria; criteria |\n")
    smoke = tmp_path / "smoke.json"
    smoke.write_text(json.dumps(_smoke([("ci-setup", 1.0, 0.5), ("pass-at-k", 0.0, 1.0),
                                        ("new-case", 1.0, 1.0)])))
    proc = subprocess.run([sys.executable, GENERATOR, str(smoke), str(previous),
                           "--max-cost-usd", "30"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    text = previous.read_text()
    section = text.split("## Compared with the previous run", 1)[1]
    assert "| `ci-setup` | 0.5 | 1.0 | +0.5 | 0.5 | 0.5 | +0.0 |" in section
    assert "| `pass-at-k` | 1.0 | 0.0 | -1.0 | 1.0 | 1.0 | +0.0 |" in section
    assert "`new-case` (new case)" in section and "`retired-case` (not in this run)" in section
    reading = text.split("## Reading it", 1)[1].split("## Compared", 1)[0]
    assert "`new-case`" in reading                 # delta zero, both pass
    assert "`ci-setup` (0.5 to 1.0)" in reading      # a lift
    assert "`pass-at-k` is below 0.8" in reading and "criteria" in reading
    assert "--max-cost-usd 30" in text
    import re
    assert not re.search(r"\b20\d\d-\d\d-\d\d\b", text)
