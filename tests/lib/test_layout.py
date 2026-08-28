"""Layout detection across every shape the ADK loader accepts."""

import pytest

from adk_skills.layout import detect_layout, find_agent_dirs, is_agent_dir, scan_exports


def test_flat_layout_is_detected_with_its_export(agent_dir):
    info = detect_layout(agent_dir("flat_good"))
    assert info.style == "flat"
    assert info.app_name == "flat_good"
    assert info.exports is not None and info.exports.preferred_export == "root_agent"
    assert info.agent_module_file.endswith("agent.py")


def test_recipes_layout_finds_the_app_package_and_its_eval_files(agent_dir):
    info = detect_layout(agent_dir("recipes_good"))
    assert info.style == "recipes"
    assert info.app_name == "app"
    assert info.declared_app_name == "app"
    assert info.exports.preferred_export == "app", "an App export takes precedence"
    assert len(info.evalsets) == 2
    assert any(p.endswith("eval_config.json") for p in info.configs)


def test_recipes_config_is_not_auto_discovered_by_adk(agent_dir):
    info = detect_layout(agent_dir("recipes_good"))
    assert info.eval_command_needs_explicit_config() is True


def test_flat_config_beside_the_evalset_is_auto_discovered(tmp_path):
    (tmp_path / "agent.py").write_text("root_agent = object()\n")
    (tmp_path / "cases.test.json").write_text("{}")
    (tmp_path / "test_config.json").write_text("{}")
    info = detect_layout(tmp_path)
    assert info.eval_command_needs_explicit_config() is False


def test_flat_agent_does_not_absorb_sibling_agents_eval_files(tmp_path):
    for name in ("one", "two"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "agent.py").write_text("root_agent = object()\n")
        (directory / f"{name}.test.json").write_text("{}")
    info = detect_layout(tmp_path / "one")
    assert [p.split("/")[-1] for p in info.evalsets] == ["one.test.json"]


def test_directory_of_several_agents_is_reported_as_a_collection(tmp_path):
    for name in ("alpha", "beta"):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "agent.py").write_text("root_agent = object()\n")
    info = detect_layout(tmp_path)
    assert info.style == "collection"
    assert len(info.sibling_agents) == 2
    assert find_agent_dirs(tmp_path) == sorted(info.sibling_agents)


def test_yaml_config_agent_is_flagged_as_deprecated(agent_dir):
    info = detect_layout(agent_dir("yaml_agent"))
    assert info.style == "yaml"
    assert any("deprecated" in note for note in info.notes)


def test_a_package_that_re_exports_its_agent_is_loadable(agent_dir):
    """`from .claims_agent.agent import app` is how a package shim exposes an
    agent; ADK imports the package and finds the name."""
    info = detect_layout(agent_dir("shim"))
    assert info.exports.has_app is True
    assert info.exports.preferred_export == "app"
    assert info.app_name == "shim", "the loader names the app after the directory"


def test_a_declared_app_name_is_what_eval_cases_must_match(agent_dir):
    """`Runner.app_name = app_name or app.name`, so an App that declares a name
    decides what the run resolves to - not the directory it was loaded from."""
    info = detect_layout(agent_dir("shim"))
    assert info.declared_app_name == "expense_claims", "found in the submodule that builds the App"
    assert info.expected_app_name() == "expense_claims"
    assert info.app_name_mismatch() == ("expense_claims", "shim")


def test_without_a_declared_app_name_the_directory_decides(agent_dir):
    info = detect_layout(agent_dir("flat_good"))
    assert info.declared_app_name is None
    assert info.expected_app_name() == "flat_good"


def test_missing_target_is_reported_not_raised(tmp_path):
    info = detect_layout(tmp_path / "nope")
    assert info.style == "unknown" and info.is_agent is False


def test_empty_directory_is_not_an_agent(tmp_path):
    assert is_agent_dir(tmp_path) is False
    assert detect_layout(tmp_path).notes


def test_scan_exports_reads_the_app_name_without_importing(tmp_path):
    source = tmp_path / "agent.py"
    source.write_text(
        "from google.adk.apps import App\n"
        "root_agent = Agent(name='x')\n"
        "app = App(name='claims', root_agent=root_agent)\n"
        "raise SystemExit('this file must never be executed')\n"
    )
    exports = scan_exports(source)
    assert exports.has_app and exports.declared_app_name == "claims"
    assert exports.imports_adk is True


def test_syntax_error_does_not_crash_export_scanning(tmp_path):
    bad = tmp_path / "agent.py"
    bad.write_text("def broken(:\n")
    assert scan_exports(bad).loadable is False
