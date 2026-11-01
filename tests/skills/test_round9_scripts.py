"""Scripts and suite files for round 9 (ADK main through 2026-11-04): AGT083 and
AGT084 in lint and discover, the silent-turn note and hint in run_evals.py, and
the plugin eval suite laid out the way Claude Code 2.1.318 loads it."""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from adk_skills.yaml_subset import loads

LINT = "skills/agent-review/scripts/lint_agent.py"
DISCOVER = "skills/adk/scripts/discover.py"
RUN_EVALS = "skills/eval-run/scripts/run_evals.py"
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
EVALS = PLUGIN_ROOT / "evals"
CASES = sorted(p for p in EVALS.iterdir() if p.is_dir() and p.name != "results")
SKILLS = sorted(p.name for p in (PLUGIN_ROOT / "skills").iterdir() if p.is_dir())

#: The keys Claude Code 2.1.318 accepts in prompt.md frontmatter (any other key
#: fails the case at load: `unknown frontmatter key`).
PROMPT_KEYS = {
    "schema_version", "name", "description", "tags", "plugins", "runs", "expected_outcome",
    "model", "max_turns", "timeout_seconds", "allowed_tools", "disallowed_tools",
    "artifact_publish", "growthbook_overrides", "append_system_prompt", "env",
}

#: The one skill each case must fire.
EXPECTED_SKILL = {
    "agent-review-drift": "agent-review", "analyze-results": "eval-analyze",
    "ci-setup": "eval-ci", "create-edge-cases": "eval-create",
    "criteria-choice": "eval-criteria", "debug-loop": "agent-debug",
    "evalset-validate": "eval-review", "pass-at-k": "eval-analyze",
    "routing-ambiguous": "adk", "simulation": "eval-simulation",
}


def _by_id(result, fid):
    return [f for f in json.loads(result.stdout)["findings"] if f["id"] == fid]


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), path
    return loads(text.split("---", 2)[1])


# -- AGT083 / AGT084 through the scripts ---------------------------------------

@pytest.mark.parametrize("fixture, fid", [("skills_overlap", "AGT083"),
                                          ("low_token_cap", "AGT084")])
def test_lint_reports_the_new_rule(run_script, agent_dir, fixture, fid):
    result = run_script(LINT, agent_dir(fixture), "--json", "--no-backend")
    [finding] = _by_id(result, fid)
    assert finding["severity"] == "info"
    assert set(finding) >= {"id", "severity", "area", "message", "path", "fix", "source"}
    assert result.returncode == 0, "an info never fails the review"
    other = {"skills_overlap": "AGT084", "low_token_cap": "AGT083"}[fixture]
    assert not _by_id(result, other)
    assert not _by_id(result, "AGT016")


@pytest.mark.parametrize("fixture, fid", [("skills_overlap", "AGT083"),
                                          ("low_token_cap", "AGT084")])
def test_discover_reports_the_new_rule(run_script, agent_dir, fixture, fid):
    result = run_script(DISCOVER, agent_dir(fixture), "--json", "--no-probe")
    assert len(_by_id(result, fid)) == 1


def test_no_other_fixture_trips_either_rule(run_script, fixtures):
    for target in sorted((fixtures / "agents").iterdir()):
        if target.name in ("skills_overlap", "low_token_cap") or not target.is_dir():
            continue
        result = run_script(LINT, target, "--json", "--no-backend")
        if result.returncode == 2:
            continue
        ids = {f["id"] for f in json.loads(result.stdout)["findings"]}
        assert not ids & {"AGT083", "AGT084"}, target.name


def test_lint_docstring_names_both_rules(run_script):
    text = run_script(LINT, "--help").stdout
    assert "AGT083" in text and "AGT084" in text


# -- run_evals.py: a silent turn ------------------------------------------------

def test_dry_run_notes_the_silent_turn(run_script, agent_dir):
    result = run_script(RUN_EVALS, agent_dir("recipes_good"), "--dry-run")
    assert result.returncode == 0
    assert "answers with no event aborts the whole run" in result.stderr
    assert "94eb6c4fc" in result.stderr


def test_a_silent_turn_failure_gets_a_hint(run_script, tmp_agent, fixtures):
    agent = tmp_agent("recipes_good")
    env = {"ADK_PYTHON": str(fixtures / "bin" / "fake_adk.py"), "FAKE_ADK_SILENT_TURN": "1"}
    previous = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        result = run_script(RUN_EVALS, agent, "--evalset",
                            agent / "tests/eval/evalsets/happy.evalset.json", "--json")
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    assert result.returncode == 2
    assert "Inferences should match conversations in eval case" in result.stderr
    assert "hint: a user turn produced no event at all" in result.stderr
    assert "no case was scored" in result.stderr


def test_failure_hint_table():
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_evals", PLUGIN_ROOT / RUN_EVALS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.failure_hint("ValueError: Inferences should match conversations in eval "
                               "case. Found 1 inferences and 2 conversations in eval case.")
    assert module.failure_hint("ModuleNotFoundError: No module named 'app'") is None
    assert module.failure_hint("") is None


# -- the plugin eval suite ------------------------------------------------------

def test_every_case_is_listed():
    assert {c.name for c in CASES} == set(EXPECTED_SKILL)


@pytest.mark.parametrize("case", CASES, ids=lambda p: p.name)
def test_case_yaml(case):
    doc = loads((case / "case.yaml").read_text(encoding="utf-8"))
    assert doc["schema_version"] == "1.0"
    assert doc["name"] == case.name
    script = doc["context"]["scaffold_script"]
    # 2.1.318 runs the value as `bash <path>`: one path, inside the case
    # directory, no arguments.
    assert " " not in script and ".." not in Path(script).parts
    assert not Path(script).is_absolute()
    assert (case / script).is_file()


@pytest.mark.parametrize("case", CASES, ids=lambda p: p.name)
def test_prompt_frontmatter_uses_only_accepted_keys(case):
    front = _frontmatter(case / "prompt.md")
    assert set(front) <= PROMPT_KEYS, set(front) - PROMPT_KEYS
    assert "scaffold_script" not in front
    assert {"max_turns", "allowed_tools", "tags"} <= set(front)
    body = (case / "prompt.md").read_text(encoding="utf-8").split("---", 2)[2]
    assert body.strip()


@pytest.mark.parametrize("case", CASES, ids=lambda p: p.name)
def test_skill_fired_names_one_skill(case):
    front = _frontmatter(case / "graders" / "skill-fired.md")
    assert front["type"] == "tool_used" and front["tool"] == "Skill"
    pattern = re.compile(front["input_match"])
    expected = EXPECTED_SKILL[case.name]
    assert expected in SKILLS
    assert pattern.search(json.dumps({"skill": f"adk:{expected}"}))
    assert pattern.search(json.dumps({"skill": expected}, separators=(",", ":")))
    for other in SKILLS:
        if other != expected:
            assert not pattern.search(json.dumps({"skill": f"adk:{other}"})), other


def test_every_grader_regex_compiles():
    for grader in sorted(EVALS.glob("*/graders/*.md")):
        front = _frontmatter(grader)
        for key in ("input_match", "pattern"):
            if key in front:
                re.compile(front[key])
        if front["type"] == "file_exists":
            # sees only files the run created, never the scaffold's own
            assert not front["path"].startswith(".scaffold/"), grader


@pytest.mark.parametrize("case", CASES, ids=lambda p: p.name)
def test_scaffold_wrapper_runs(case, tmp_path):
    doc = loads((case / "case.yaml").read_text(encoding="utf-8"))
    script = (case / doc["context"]["scaffold_script"]).resolve()
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)}
    proc = subprocess.run(["bash", str(script)], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    manifest = (tmp_path / ".scaffold" / "manifest.txt").read_text()
    assert manifest.startswith("fixture=")


def test_readme_and_last_run():
    readme = (EVALS / "README.md").read_text(encoding="utf-8")
    assert "early-access" not in readme
    assert ("claude plugin eval . --scaffold --allow-tools Bash Write Edit --trust-plugin"
            in readme.replace("\\\n  ", ""))
    for phrase in ("case.yaml", "context tax", "evals/results/", "indicator",
                   "--max-cost-usd 0"):
        assert phrase in readme, phrase
    last_run = (EVALS / "LAST-RUN.md").read_text(encoding="utf-8")
    # Either the placeholder or a recorded run with its per-case table.
    assert "Not yet run" in last_run or ("| Case |" in last_run and "claude plugin eval" in last_run)
