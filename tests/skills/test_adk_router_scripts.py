"""The router's scripts, run the way a skill runs them."""

import json

from adk_skills import ADK_TARGET


def test_discover_reports_a_recipes_project_as_json(run_script, agent_dir):
    result = run_script(
        "skills/adk/scripts/discover.py", agent_dir("recipes_good"), "--json", "--no-probe"
    )
    payload = json.loads(result.stdout)
    assert payload["schema"] == "adk-skills.gap-report/1"
    assert payload["discovered"]["layout"]["style"] == "recipes"
    assert payload["discovered"]["evalsets"]["cases"] == 3
    assert payload["discovered"]["criteria"]["cost_tier"] == "free"
    assert payload["options"], "a report always offers a next step"
    assert sum(1 for o in payload["options"] if o["recommended"]) == 1


def test_discover_markdown_is_what_a_user_would_read(run_script, agent_dir):
    result = run_script("skills/adk/scripts/discover.py", agent_dir("flat_good"), "--no-probe")
    assert "## Found" in result.stdout
    assert "## Missing" in result.stdout
    assert "claims_agent" in result.stdout


def test_discover_exits_two_on_a_path_that_does_not_exist(run_script, tmp_path):
    result = run_script("skills/adk/scripts/discover.py", tmp_path / "nope")
    assert result.returncode == 2
    assert "does not exist" in result.stderr


def test_discover_exits_one_when_the_agent_has_errors(run_script, agent_dir):
    result = run_script("skills/adk/scripts/discover.py", agent_dir("flat_drift"), "--no-probe")
    assert result.returncode == 1


def test_discover_focus_limits_the_work(run_script, agent_dir):
    result = run_script(
        "skills/adk/scripts/discover.py", agent_dir("recipes_good"),
        "--json", "--no-probe", "--focus", "layout",
    )
    assert set(json.loads(result.stdout)["discovered"]) == {"layout"}


def test_discover_rejects_an_unknown_focus(run_script, agent_dir):
    result = run_script(
        "skills/adk/scripts/discover.py", agent_dir("flat_good"), "--focus", "nonsense"
    )
    assert result.returncode == 2 and "unknown focus" in result.stderr


def test_discover_finds_several_agents_and_says_to_pick_one(run_script, fixtures):
    result = run_script(
        "skills/adk/scripts/discover.py", fixtures / "agents", "--json", "--no-probe"
    )
    payload = json.loads(result.stdout)
    assert payload["discovered"]["layout"]["style"] == "collection"
    found = payload["discovered"]["layout"]["agents_found"]
    assert {"flat_good", "flat_drift", "yaml_agent"} <= set(found)
    assert "recipes_good/app" in found, "an agent nested below a project root still counts"


def test_check_env_reports_blockers_and_exits_one(run_script, agent_dir):
    result = run_script("skills/adk/scripts/check_env.py", agent_dir("flat_good"), "--json", "--no-probe")
    payload = json.loads(result.stdout)
    assert payload["ready"] is False
    assert any("GOOGLE_" in blocker for blocker in payload["blockers"])
    assert result.returncode == 1


def test_check_env_is_ready_with_vertex_configured(run_script, tmp_agent, fixtures, monkeypatch):
    agent = tmp_agent("flat_good")
    (agent / ".env").write_text(
        "GOOGLE_GENAI_USE_VERTEXAI=TRUE\nGOOGLE_CLOUD_PROJECT=p\n"
        "GOOGLE_CLOUD_LOCATION=us-central1\nMODEL_NAME=gemini-flash-latest\n"
    )
    import os
    os.environ["ADK_PYTHON"] = str(fixtures / "bin" / "fake_adk.py")
    try:
        result = run_script("skills/adk/scripts/check_env.py", agent, "--json")
        payload = json.loads(result.stdout)
        assert payload["ready"] is True
        assert payload["runner"]["adk_version"] == f"{ADK_TARGET}.0"
        assert result.returncode == 0
    finally:
        os.environ.pop("ADK_PYTHON", None)


def test_check_env_accepts_an_api_key_for_vertex_scored_metrics(run_script, tmp_agent, fixtures):
    """The Vertex evaluation facade reads GOOGLE_API_KEY first, so a key alone
    can serve safety_v1; the only remaining blocker is the unverified runner."""
    agent = tmp_agent("flat_good")
    (agent / ".env").write_text("GOOGLE_API_KEY=abc\n")
    result = run_script(
        "skills/adk/scripts/check_env.py", agent, "--json", "--no-probe",
        "--config", fixtures / "eval_configs/vertex_safety.json",
    )
    payload = json.loads(result.stdout)
    assert not any("evaluation service" in blocker for blocker in payload["blockers"])


def test_check_env_blocks_a_vertex_scored_metric_with_no_credentials(run_script, tmp_agent, fixtures):
    agent = tmp_agent("flat_good")
    result = run_script(
        "skills/adk/scripts/check_env.py", agent, "--json", "--no-probe",
        "--config", fixtures / "eval_configs/vertex_safety.json",
    )
    payload = json.loads(result.stdout)
    assert payload["ready"] is False
    assert any("evaluation service" in blocker for blocker in payload["blockers"])
