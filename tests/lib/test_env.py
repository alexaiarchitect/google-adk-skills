"""Backend detection, model policy, and runner resolution."""

import os

from adk_skills import ADK_MIN_SUPPORTED, ADK_TARGET
from adk_skills.env import (
    ADK_REQUIREMENT, classify_model_id, default_judge_model, detect_backend, read_dotenv,
    resolve_runner, version_at_least, version_is_supported,
)


def test_dotenv_parsing_handles_quotes_exports_and_comments(tmp_path):
    (tmp_path / ".env").write_text(
        '# a comment\n'
        'export GOOGLE_CLOUD_PROJECT="my-project"\n'
        "GOOGLE_CLOUD_LOCATION=us-central1  # trailing comment\n"
        "EMPTY=\n"
        "not a variable line\n"
    )
    values = read_dotenv(tmp_path / ".env")
    assert values["GOOGLE_CLOUD_PROJECT"] == "my-project"
    assert values["GOOGLE_CLOUD_LOCATION"] == "us-central1"
    assert values["EMPTY"] == ""


def test_vertex_backend_requires_project_and_location(tmp_path):
    (tmp_path / ".env").write_text("GOOGLE_GENAI_USE_VERTEXAI=TRUE\nGOOGLE_CLOUD_PROJECT=p\n")
    backend = detect_backend(tmp_path, include_os_environ=False)
    assert backend.backend == "vertex"
    assert backend.vertex_ready is False
    assert "GOOGLE_CLOUD_LOCATION" in backend.missing


def test_vertex_ready_when_both_are_set(tmp_path):
    (tmp_path / ".env").write_text(
        "GOOGLE_GENAI_USE_VERTEXAI=true\nGOOGLE_CLOUD_PROJECT=p\nGOOGLE_CLOUD_LOCATION=us-central1\n"
    )
    backend = detect_backend(tmp_path, include_os_environ=False)
    assert backend.vertex_ready and backend.judge_ready


def test_an_api_key_alone_can_serve_the_vertex_metrics(tmp_path):
    """`vertex_ai_eval_facade.py` reads GOOGLE_API_KEY before it looks at
    project and location, so an API key is enough for safety_v1 and the
    multi_turn_* metrics. Demanding a GCP project here blocked runs that would
    have worked."""
    (tmp_path / ".env").write_text("GOOGLE_API_KEY=abc123\n")
    backend = detect_backend(tmp_path, include_os_environ=False)
    assert backend.backend == "api_key"
    assert backend.judge_ready is True
    assert backend.vertex_ready is True
    assert backend.vertex_credential == "GOOGLE_API_KEY"


def test_project_and_location_still_serve_the_vertex_metrics(tmp_path):
    (tmp_path / ".env").write_text(
        "GOOGLE_GENAI_USE_VERTEXAI=TRUE\nGOOGLE_CLOUD_PROJECT=p\n"
        "GOOGLE_CLOUD_LOCATION=us-central1\n"
    )
    backend = detect_backend(tmp_path, include_os_environ=False)
    assert backend.vertex_ready is True
    assert backend.vertex_credential.startswith("GOOGLE_CLOUD_PROJECT")


def test_neither_credential_means_the_vertex_metrics_cannot_run(tmp_path):
    backend = detect_backend(tmp_path, include_os_environ=False)
    assert backend.vertex_ready is False
    assert backend.vertex_credential is None


def test_no_configuration_is_reported_as_unknown(tmp_path):
    backend = detect_backend(tmp_path, include_os_environ=False)
    assert backend.backend == "unknown" and backend.missing


def test_env_example_is_noticed_but_never_treated_as_configuration(tmp_path):
    (tmp_path / ".env.example").write_text("GOOGLE_API_KEY=replace-me\n")
    backend = detect_backend(tmp_path, include_os_environ=False)
    assert backend.has_api_key is False
    assert backend.example_files and all(
        p.endswith(".env.example") for p in backend.example_files
    )
    assert backend.env_files == [], "an example is documentation, never configuration"


def test_the_nearest_dotenv_wins_and_a_repo_root_one_is_found(tmp_path):
    """ADK walks from the agent directory to the filesystem root and takes the
    first `.env` it finds (`cli/utils/envs.py`). A repository-root `.env` two
    levels above the agent is therefore loaded, which is how most real projects
    are arranged - and looking only one level up reported them as unconfigured."""
    (tmp_path / ".env").write_text("GOOGLE_API_KEY=from-repo-root\n")
    agent = tmp_path / "services" / "claims_agent"
    agent.mkdir(parents=True)

    backend = detect_backend(agent, include_os_environ=False)
    assert backend.backend == "api_key"
    assert backend.source == str(tmp_path / ".env")

    (agent / ".env").write_text("GOOGLE_API_KEY=from-agent\n")
    nearer = detect_backend(agent, include_os_environ=False)
    assert nearer.source == str(agent / ".env")
    assert nearer.values["GOOGLE_API_KEY"] == "from-agent"


def test_model_policy_grades():
    assert classify_model_id("gemini-flash-latest") == "alias"
    assert classify_model_id("gemini-2.0-flash") == "deprecated"
    assert classify_model_id("gemini-2.5-flash") == "deprecated"
    assert classify_model_id("gemini-3-flash-preview") == "preview"
    assert classify_model_id("gemini-3.5-flash") == "pinned"
    assert classify_model_id(None) == "unknown"


def test_default_judge_model_prefers_the_projects_own_model(tmp_path):
    (tmp_path / ".env").write_text("MODEL_NAME=gemini-pro-latest\nGOOGLE_API_KEY=x\n")
    backend = detect_backend(tmp_path, include_os_environ=False)
    assert default_judge_model(backend) == "gemini-pro-latest"
    assert default_judge_model(None) == "gemini-flash-latest"


def test_only_adk_2x_is_supported():
    assert version_is_supported(f"{ADK_TARGET}.0") is True
    assert version_is_supported("1.14.1") is False
    assert version_is_supported(None) is False


def test_the_oldest_supported_release_is_still_supported():
    """The plugin targets 2.12 and keeps working on 2.9: a 2.9.2 runner is not
    an error, it just reports fewer things (no efficiency metrics)."""
    assert ADK_MIN_SUPPORTED == "2.9" and ADK_TARGET == "2.12"
    assert version_is_supported("2.9.2") is True
    assert version_is_supported("2.10.0") is True
    assert version_at_least("2.9.2", ADK_TARGET) is False
    assert version_at_least("2.10.0", ADK_TARGET) is False
    assert version_at_least("2.12.0", ADK_TARGET) is True
    assert version_at_least("2.11.0", ADK_TARGET) is False


def test_the_install_pin_follows_the_targeted_release():
    assert ADK_REQUIREMENT == f'google-adk[eval]=={ADK_TARGET}.*'


def test_versions_compare_per_component_not_as_strings():
    """"2.9.2" < "2.10.0" is what a string comparison gets backwards, and that
    pair is exactly the one every 2.10-only behaviour turns on."""
    assert version_at_least("2.10.0", "2.10") is True
    assert version_at_least("2.10.1", "2.10") is True
    assert version_at_least("2.11", "2.10") is True
    assert version_at_least("2.9.2", "2.10") is False
    assert version_at_least("2.10.0rc1", "2.10") is True, "a pre-release is not downgraded"
    assert version_at_least(None, "2.10") is False, "an unknown runner is not assumed new"


def test_explicit_adk_python_is_probed_first(tmp_path, fixtures, monkeypatch):
    monkeypatch.setenv("ADK_PYTHON", str(fixtures / "bin" / "fake_adk.py"))
    runner = resolve_runner(tmp_path)
    assert runner.kind == "explicit"
    assert runner.adk_version == f"{ADK_TARGET}.0", "the stand-in reports the targeted release"
    assert runner.verified is True


def test_missing_runner_explains_how_to_get_one(tmp_path, monkeypatch):
    monkeypatch.delenv("ADK_PYTHON", raising=False)
    monkeypatch.setattr("adk_skills.env.candidate_pythons", lambda *a, **k: [])
    monkeypatch.setattr("adk_skills.env.shutil.which", lambda name: None)
    runner = resolve_runner(tmp_path)
    assert runner.available is False
    assert "uv" in (runner.note or "") and "venv" in (runner.note or "")


def test_unprobed_candidate_is_marked_unverified(tmp_path, monkeypatch):
    monkeypatch.setenv("ADK_PYTHON", "/usr/bin/python3")
    runner = resolve_runner(tmp_path, probe=False)
    assert runner.verified is False and runner.adk_version is None
