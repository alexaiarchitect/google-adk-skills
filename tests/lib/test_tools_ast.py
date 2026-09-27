"""Static extraction of tools, schemas, agents, and review findings."""

from adk_skills.tools_ast import extract_module, tool_param_constraints


def ids(facts) -> set[str]:
    return {f.id for f in facts.findings}


def test_clean_agent_yields_tools_models_and_no_serious_findings(agent_dir):
    facts = extract_module(agent_dir("flat_good"))
    assert facts.bound_tool_names == {
        "lookup_policy", "check_cost_centre_funds", "submit_claim", "reject_claim"
    }
    assert set(facts.models) == {"PolicyRequest", "FundsRequest", "ClaimRequest", "RejectionRequest"}
    assert not [f for f in facts.findings if f.severity == "error"]


def test_agent_facts_include_the_env_driven_model_and_instruction(agent_dir):
    agent = extract_module(agent_dir("flat_good")).root_agent()
    assert agent.name == "claims_agent"
    assert agent.model_from_env is True
    assert agent.instruction_len > 500, "instruction held in a module constant must resolve"


def test_pydantic_constraints_flatten_to_the_shape_eval_cases_write(agent_dir):
    facts = extract_module(agent_dir("flat_good"))
    constraints = tool_param_constraints(facts.tools["submit_claim"], facts.models)
    assert set(constraints) == {"req.claim_id", "req.cost_centre", "req.amount_usd"}
    assert constraints["req.claim_id"].pattern == r"^CLM-[0-9]{6}$"
    assert constraints["req.cost_centre"].literal_values == [
        "engineering", "sales", "marketing", "operations"
    ]
    assert constraints["req.amount_usd"].ge == 0.0


def test_app_export_is_detected_in_the_recipes_layout(agent_dir):
    facts = extract_module(agent_dir("recipes_good"))
    assert facts.app is not None and facts.app.name == "app"
    assert "AGT014" not in ids(facts), "no complaint when an App is exported"


def test_missing_app_is_only_an_informational_note(agent_dir):
    facts = extract_module(agent_dir("flat_good"))
    note = [f for f in facts.findings if f.id == "AGT014"][0]
    assert note.severity == "info"


def test_drift_agent_reports_every_planted_problem(agent_dir):
    found = ids(extract_module(agent_dir("flat_drift")))
    assert "AGT001" in found, "SequentialAgent superseded by Workflow"
    assert "AGT002" in found, "global_instruction deprecated"
    assert "AGT003" in found, "generate_content_config carrying system_instruction"
    assert "AGT005" in found, "deprecated model id"
    assert "AGT011" in found, 'agent named "user"'
    assert "AGT013" in found, "direct session.state write"
    assert "AGT015" in found, "duplicate sub_agent names"
    assert "AGT016" in found, "temperature passed to the agent constructor"
    assert "AGT040" in found or "AGT041" in found, "thin tool docstring"
    assert "AGT043" in found, "untyped tool parameter"
    assert "AGT044" in found, "tool raises instead of returning an error"
    assert "AGT047" in found, "blocking call in a sync tool"


def test_construction_breaking_problems_are_errors_not_warnings(agent_dir):
    facts = extract_module(agent_dir("flat_drift"))
    severities = {f.id: f.severity for f in facts.findings}
    assert severities["AGT003"] == "error"
    assert severities["AGT016"] == "error"
    assert severities["AGT011"] == "error"
    assert severities["AGT001"] == "warn", "deprecated, but it still runs"


def test_tool_docstring_and_return_shape_are_captured(agent_dir):
    tool = extract_module(agent_dir("flat_good")).tools["lookup_policy"]
    assert tool.docstring and "Args:" in tool.docstring
    assert tool.returns_dict and "status" in tool.dict_keys
    assert tool.raises == [], "a schema gate returns errors rather than raising"


def test_injected_context_parameters_are_excluded_from_the_schema(tmp_path):
    (tmp_path / "agent.py").write_text(
        "from google.adk.agents import Agent\n"
        "from google.adk.tools import ToolContext\n"
        "def remember(value: str, tool_context: ToolContext) -> dict:\n"
        "    '''Remember a value.\\n\\n    Args:\\n        value: the value.\\n\\n"
        "    Returns:\\n        A dict with status.\\n    '''\n"
        "    return {'status': 'ok'}\n"
        "root_agent = Agent(name='a', model='gemini-flash-latest',"
        " instruction='Remember things.', tools=[remember])\n"
    )
    facts = extract_module(tmp_path)
    names = [p.name for p in facts.tools["remember"].schema_params]
    assert names == ["value"], "ToolContext is injected, not part of the declaration"


def test_a_file_that_cannot_be_parsed_is_reported_not_raised(tmp_path):
    (tmp_path / "agent.py").write_text("def broken(:\n")
    facts = extract_module(tmp_path)
    assert facts.syntax_errors


def test_extraction_never_executes_the_agent(tmp_path):
    (tmp_path / "agent.py").write_text(
        "raise SystemExit('module-level side effect')\n"
        "root_agent = None\n"
    )
    extract_module(tmp_path)          # must not raise


def test_an_agent_built_from_kwargs_is_reported_as_unreadable(agent_dir):
    """`Agent(**kwargs)` plus `root_agent = wrapper._agent`: name, model,
    instruction and tools are unknown, and saying nothing would read as "absent"."""
    facts = extract_module(agent_dir("kwargs_agent"))
    assert facts.agent_unresolved is True
    reason = facts.agent_unresolved_reason
    assert "Agent(**kwargs)" in reason
    assert "_claims_app._agent" in reason
    assert "attribute of an object" in reason
    agent = facts.root_agent()
    assert agent.kwargs_unresolved is True
    assert (agent.name, agent.model, agent.instruction_len, agent.tools) == (None, None, 0, [])
    assert facts.unresolved_exports == {"root_agent": "_claims_app._agent"}
    assert facts.to_dict()["agent_unresolved"] is True


def test_the_unreadable_agent_gets_one_note_and_no_invented_faults(agent_dir):
    facts = extract_module(agent_dir("kwargs_agent"))
    ids = [f.id for f in facts.findings]
    assert ids.count("AGT055") == 1
    message = [f.message for f in facts.findings if f.id == "AGT055"][0]
    assert "not statically readable" in message and "load_agent.py" in message
    # The checks that need those fields must stay quiet rather than guess.
    for absent in ("AGT012", "AGT020", "AGT007", "AGT004"):
        assert absent not in ids, absent


def test_nothing_is_ruled_out_while_the_agent_cannot_be_read(agent_dir):
    """A tool bound only under a feature flag is still a name a run can emit."""
    from adk_skills.tools_ast import resolve_tool

    facts = extract_module(agent_dir("kwargs_agent"))
    assert facts.tools_unresolved is True
    assert resolve_tool(facts, "export_to_ledger").status == "unresolved"
    assert resolve_tool(facts, "export_to_ledger").is_real
    assert resolve_tool(facts, "made_up_tool").is_real, "absence proves nothing here"


def test_a_readable_agent_is_not_marked_unresolved(agent_dir):
    facts = extract_module(agent_dir("flat_good"))
    assert facts.agent_unresolved is False
    assert facts.agent_unresolved_reason is None
    assert facts.tools_unresolved is False
    assert "AGT055" not in [f.id for f in facts.findings]


def test_a_defined_tool_is_not_ruled_out_when_the_tool_list_is_built_at_runtime(tmp_path):
    """long-horizon-harness shape: `tools=compact(tools)` hides the binding, and
    every tool the project defines must then resolve as possible, not absent."""
    from adk_skills.tools_ast import extract_module, resolve_tool

    (tmp_path / "__init__.py").write_text("from . import agent\n")
    (tmp_path / "agent.py").write_text(
        "from google.adk.agents import Agent\n"
        "from .tools import process, bash\n\n"
        "def compact(tools):\n    return tools\n\n"
        "def build():\n"
        "    tools = [process, bash]\n"
        "    return Agent(name='h', model='gemini-flash-latest', instruction='x',\n"
        "                 tools=compact(tools))\n\n"
        "root_agent = build()\n"
    )
    (tmp_path / "tools.py").write_text(
        "async def process(command: str) -> dict:\n    '''Spawn.\\n\\n    Args:\\n        command: what to run.\\n    '''\n    return {}\n\n"
        "def bash(command: str) -> dict:\n    '''Run.\\n\\n    Args:\\n        command: what to run.\\n    '''\n    return {}\n"
    )
    facts = extract_module(tmp_path)
    assert facts.tools_unresolved
    assert resolve_tool(facts, "process").is_real
    assert resolve_tool(facts, "bash").is_real
    assert resolve_tool(facts, "process").status == "unresolved"
    assert resolve_tool(facts, "made_up").is_real, "absence proves nothing here"
