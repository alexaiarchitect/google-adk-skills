"""Rules added against ADK main through 2026-11-02 (round 8): the top-level
`parallelism` key (CFG077), app directory names the loader refuses (ENV015),
memory tools inside an AgentTool (AGT082), and the agents-cli config note."""

import json
import shutil
import textwrap
from pathlib import Path

import pytest

from adk_skills.eval_config import LoadedConfig, load_config, validate_config
from adk_skills.layout import detect_layout
from adk_skills.tools_ast import agent_tool_memory_findings, extract_module

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
CRITERIA = {"tool_trajectory_avg_score": 1.0}


def _cfg077(doc, adk_version=None):
    report = validate_config(LoadedConfig("cfg.json", doc), adk_version=adk_version)
    return ([f for f in report.findings if f.id == "CFG077"],
            [f for f in report.findings if f.id == "CFG004"])


# -- CFG077 --------------------------------------------------------------------

@pytest.mark.parametrize("version", [None, "2.9.2", "2.13", "2.13.0"])
def test_parallelism_warns_through_2_13(version):
    [finding], cfg004 = _cfg077({"criteria": CRITERIA, "parallelism": 8}, version)
    assert finding.severity == "warn"
    assert finding.message == (
        "`parallelism` is read only by ADK main after 2.13.0 (`e9e8f6156`); every released "
        "version drops it silently, so cases still run 4 at a time")
    assert finding.locator == "parallelism"
    assert not cfg004


@pytest.mark.parametrize("version", ["2.13.1", "2.14.0", "3.0", "main"])
def test_parallelism_is_an_info_on_a_newer_runner(version):
    [finding], _ = _cfg077({"criteria": CRITERIA, "parallelism": 8}, version)
    assert finding.severity == "info"
    assert f"runner {version} is newer" in finding.message


@pytest.mark.parametrize("value, needle", [
    (0, "Input should be greater than or equal to 1"),
    (-2, "Input should be greater than or equal to 1"),
    (False, "Input should be greater than or equal to 1"),
    ("0", "Input should be greater than or equal to 1"),
    (2.5, "got a number with a fractional part"),
    ("many", "unable to parse string as an integer"),
    ([4], "Input should be a valid integer"),
])
@pytest.mark.parametrize("version", [None, "2.9.2", "2.13.0", "2.14.0"])
def test_a_value_main_rejects_is_an_error_on_every_version(value, needle, version):
    [finding], _ = _cfg077({"criteria": CRITERIA, "parallelism": value}, version)
    assert finding.severity == "error"
    assert needle in finding.message and "ValidationError" in finding.message


@pytest.mark.parametrize("value", [1, 4, "8", 8.0, True])
def test_values_main_accepts_are_not_errors(value):
    [finding], _ = _cfg077({"criteria": CRITERIA, "parallelism": value})
    assert finding.severity == "warn"


def test_null_parallelism_and_no_key_are_silent():
    assert _cfg077({"criteria": CRITERIA, "parallelism": None}) == ([], [])
    assert _cfg077({"criteria": CRITERIA}) == ([], [])


def test_other_unknown_keys_still_trip_cfg004():
    cfg077, [cfg004] = _cfg077({"criteria": CRITERIA, "parallelism": 2, "paralelism": 2})
    assert cfg004.message == "unrecognized top-level key(s): paralelism"
    assert [f.severity for f in cfg077] == ["warn"]


def test_parallelism_is_not_the_judge_parallelism_limit():
    doc = {"criteria": {"final_response_match_v2": {
        "threshold": 0.8,
        "judge_model_options": {"judge_model": "gemini-flash-latest", "parallelism_limit": 2}}}}
    assert _cfg077(doc) == ([], [])


def test_fixture_configs(fixtures):
    for name, severity in (("parallelism.json", "warn"), ("parallelism_zero.json", "error")):
        report = validate_config(load_config(fixtures / "eval_configs" / name))
        assert [f.severity for f in report.findings if f.id == "CFG077"] == [severity]


CONFIG_TEMPLATES = sorted(
    [*PLUGIN_ROOT.glob("skills/*/assets/templates/configs/*.json"),
     PLUGIN_ROOT / "skills/eval-simulation/assets/templates/user_simulator_config.json"])
TMPL_CONFIGS = [
    PLUGIN_ROOT / "skills/agent-create/assets/templates/flat/test_config.json.tmpl",
    PLUGIN_ROOT / "skills/agent-create/assets/templates/recipes/tests/eval/eval_config.json.tmpl",
]


@pytest.mark.parametrize("path", CONFIG_TEMPLATES + TMPL_CONFIGS, ids=lambda p: p.name)
def test_no_template_sets_parallelism_or_trips_cfg004(path):
    text = path.read_text(encoding="utf-8")
    assert "parallelism\"" not in text.replace("parallelism_limit\"", "")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        pytest.skip("template with placeholders")
    report = validate_config(LoadedConfig(str(path), doc), adk_version="2.13.0")
    assert not [f for f in report.findings if f.id in ("CFG077", "CFG004")]


# -- ENV015 --------------------------------------------------------------------

def _agent(root: Path, name: str) -> Path:
    package = root / name
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("from . import agent\n")
    (package / "agent.py").write_text(textwrap.dedent('''
        from google.adk.agents import Agent
        root_agent = Agent(name="policy_agent", model="gemini-flash-latest",
                           instruction="Answer expense policy questions.")
    '''))
    return package


@pytest.mark.parametrize("name", ["my-agent", "claims.agent", "claims agent", "café"])
def test_a_name_the_loader_refuses_is_an_error(tmp_path, name):
    finding = detect_layout(_agent(tmp_path, name)).app_dir_name_finding()
    assert finding["id"] == "ENV015" and finding["severity"] == "error"
    assert f"Invalid agent name: {name!r}" in finding["message"]
    assert "Python identifier such as" in finding["fix"]


@pytest.mark.parametrize("name, why", [
    ("class", "a Python keyword"),
    ("1agent", "starts with a digit"),
])
def test_a_name_only_main_create_refuses_is_an_info(tmp_path, name, why):
    finding = detect_layout(_agent(tmp_path, name)).app_dir_name_finding()
    assert finding["severity"] == "info" and why in finding["message"]
    assert "loads today" in finding["message"] and "3655990f6" in finding["message"]


@pytest.mark.parametrize("name", ["claims_agent", "app", "Agent2", "_private"])
def test_identifiers_are_clean(tmp_path, name):
    assert detect_layout(_agent(tmp_path, name)).app_dir_name_finding() is None


def test_the_project_directory_above_the_agent_does_not_count(tmp_path):
    project = tmp_path / "expense-claims"
    _agent(project, "claims_agent")
    layout = detect_layout(project)
    assert layout.app_name == "claims_agent" and layout.app_dir_name_finding() is None


def test_a_src_layout_with_a_valid_package_is_skipped(tmp_path, fixtures):
    project = tmp_path / "claims-project"
    shutil.copytree(fixtures / "projects/src_layout", project)
    layout = detect_layout(project)
    assert layout.style == "src" and layout.app_dir_name_finding() is None


def test_a_kebab_module_file_is_an_error(tmp_path):
    module = tmp_path / "my-agent.py"
    module.write_text("from google.adk.agents import Agent\nroot_agent = Agent(name='a')\n")
    finding = detect_layout(module).app_dir_name_finding()
    assert finding["severity"] == "error" and "app module 'my-agent'" in finding["message"]


def test_fixture_projects(fixtures):
    kebab = detect_layout(fixtures / "projects/kebab_app").app_dir_name_finding()
    keyword = detect_layout(fixtures / "projects/keyword_app").app_dir_name_finding()
    assert kebab["severity"] == "error" and keyword["severity"] == "info"


# -- AGT082 --------------------------------------------------------------------

def _write(tmp_path: Path, body: str) -> Path:
    (tmp_path / "agent.py").write_text(textwrap.dedent(body))
    return tmp_path


def test_memory_tool_in_an_agent_tool_warns(agent_dir):
    facts = extract_module(agent_dir("agent_tool_memory"))
    [finding] = agent_tool_memory_findings(facts)
    assert finding.id == "AGT082" and finding.severity == "warn"
    assert finding.message.startswith(
        "'claims_agent' wraps 'policy_lookup' in an AgentTool, and that agent binds "
        "`load_memory`: through ADK 2.13.0")
    assert "4e0ac64e3" in finding.message


@pytest.mark.parametrize("version, severity", [
    (None, "warn"), ("2.9.2", "warn"), ("2.13.0", "warn"), ("2.13.1", "info"),
    ("2.14.0", "info"), ("main", "info"),
])
def test_agt082_is_version_aware(agent_dir, version, severity):
    facts = extract_module(agent_dir("agent_tool_memory"))
    [finding] = agent_tool_memory_findings(facts, adk_version=version)
    assert finding.severity == severity


@pytest.mark.parametrize("tools", ["[preload_memory]", "[PreloadMemoryTool()]",
                                   "[LoadMemoryTool(), lookup]"])
def test_every_memory_tool_counts_also_through_a_constant(tmp_path, tools):
    path = _write(tmp_path, f'''
        from google.adk.agents import Agent
        from google.adk.tools import preload_memory, LoadMemoryTool, PreloadMemoryTool
        from google.adk.tools.agent_tool import AgentTool

        def lookup(claim_id: str) -> dict:
            """Look up a claim."""
            return {{"status": "ok"}}

        history = Agent(name="history", model="gemini-flash-latest", tools={tools})
        HISTORY_TOOL = AgentTool(agent=history)
        root_agent = Agent(name="claims_agent", model="gemini-flash-latest",
                           tools=[HISTORY_TOOL])
    ''')
    [finding] = agent_tool_memory_findings(extract_module(path))
    assert "'history'" in finding.message


def test_memory_in_a_sub_agent_of_the_wrapped_agent_counts(tmp_path):
    path = _write(tmp_path, '''
        from google.adk.agents import Agent
        from google.adk.tools import load_memory
        from google.adk.tools.agent_tool import AgentTool

        recall = Agent(name="recall", model="gemini-flash-latest", tools=[load_memory])
        desk = Agent(name="desk", model="gemini-flash-latest", sub_agents=[recall])
        root_agent = Agent(name="claims_agent", model="gemini-flash-latest",
                           tools=[AgentTool(agent=desk)])
    ''')
    [finding] = agent_tool_memory_findings(extract_module(path))
    assert "'desk'" in finding.message and "`load_memory`" in finding.message


def test_memory_on_the_parent_or_a_transfer_target_is_clean(tmp_path):
    path = _write(tmp_path, '''
        from google.adk.agents import Agent
        from google.adk.tools import load_memory
        from google.adk.tools.agent_tool import AgentTool

        recall = Agent(name="recall", model="gemini-flash-latest", tools=[load_memory])
        checker = Agent(name="checker", model="gemini-flash-latest")
        root_agent = Agent(name="claims_agent", model="gemini-flash-latest",
                           tools=[load_memory, AgentTool(agent=checker)],
                           sub_agents=[recall])
    ''')
    assert agent_tool_memory_findings(extract_module(path)) == []


@pytest.mark.parametrize("name", ["wrapped_tools", "flat_good", "skilled_agent",
                                  "stateful_agent", "live_agent", "search_agent"])
def test_existing_fixtures_stay_clean(agent_dir, name):
    assert agent_tool_memory_findings(extract_module(agent_dir(name))) == []


# -- agents-cli configs --------------------------------------------------------

def _agents_cli_project(tmp_path: Path, configs: list[str]) -> Path:
    project = tmp_path / "voice"
    _agent(project, "app")
    datasets = project / "tests/eval/datasets"
    datasets.mkdir(parents=True)
    for name in ("system1.json", "system2.json"):
        (datasets / name).write_text("{}")
    for name in configs:
        (project / "tests/eval" / name).write_text("metrics_to_run: []\n")
    return project


def test_several_agents_cli_configs_are_named_in_the_note(tmp_path):
    layout = detect_layout(_agents_cli_project(
        tmp_path, ["eval_config_system1.yaml", "eval_config_system2.yaml"]))
    assert [Path(p).name for p in layout.agents_cli_configs] == [
        "eval_config_system1.yaml", "eval_config_system2.yaml"]
    [note] = [n for n in layout.notes if "agents-cli" in n]
    assert note.startswith("2 agents-cli eval dataset(s) under tests/eval/datasets")
    assert ("with one of 2 configs: tests/eval/eval_config_system1.yaml, "
            "tests/eval/eval_config_system2.yaml") in note


def test_one_agents_cli_config_keeps_the_old_note(tmp_path):
    layout = detect_layout(_agents_cli_project(tmp_path, ["eval_config.yaml"]))
    [note] = [n for n in layout.notes if "agents-cli" in n]
    assert "(graded by `agents-cli eval` with tests/eval/eval_config.yaml)" in note
