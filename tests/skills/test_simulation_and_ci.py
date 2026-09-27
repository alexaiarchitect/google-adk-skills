"""eval-simulation and eval-ci scripts."""

import json


def test_scenario_cases_are_written_with_their_plan(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    spec = json.dumps([{
        "eval_id": "memory_simulated_repeat_claims",
        "starting_prompt": "I need to claim expenses from last week.",
        "conversation_plan": "Say you work in engineering. Ask about two claims without "
                             "repeating the cost centre. Confirm both, then stop.",
        "persona": "NOVICE",
    }])
    result = run_script(
        "skills/eval-simulation/scripts/gen_scenarios.py", agent,
        "--spec", spec, "--new", "simulated", "--json",
    )
    payload = json.loads(result.stdout)
    assert payload["summary"]["scenario_cases"] == 1
    written = json.loads(open(payload["written"][0]).read())
    scenario = written["eval_cases"][0]["conversation_scenario"]
    assert scenario["user_persona"] == "NOVICE"
    assert "conversation" not in written["eval_cases"][0], "a case is scenario or turns, never both"


def test_a_scenario_with_a_reference_config_is_an_error(run_script, tmp_agent):
    """A simulated conversation has no fixed reference, so the deterministic
    config cannot score it."""
    agent = tmp_agent("recipes_good")
    result = run_script(
        "skills/eval-simulation/scripts/gen_scenarios.py", agent,
        "--spec", json.dumps([{"eval_id": "memory_sim", "starting_prompt": "hi",
                               "conversation_plan": "Ask about a claim, then stop."}]),
        "--new", "simulated", "--json",
    )
    found = {f["id"] for f in json.loads(result.stdout)["validation"]["findings"]}
    assert "CFG030" in found
    assert result.returncode == 1


def test_a_scenario_needs_a_plan(run_script, agent_dir):
    result = run_script(
        "skills/eval-simulation/scripts/gen_scenarios.py", agent_dir("recipes_good"),
        "--spec", json.dumps([{"eval_id": "x", "starting_prompt": "hi"}]), "--new", "s",
    )
    assert result.returncode == 2 and "conversation_plan" in result.stderr


def test_a_user_simulator_config_is_generated_with_a_cap_and_a_model(run_script):
    result = run_script(
        "skills/eval-simulation/scripts/gen_sim_config.py", "--kind", "user",
        "--model", "gemini-flash-latest", "--json",
    )
    config = json.loads(result.stdout)["config"]["user_simulator_config"]
    assert config["type"] == "llm_backed"
    assert config["model"] == "gemini-flash-latest"
    assert config["max_allowed_invocations"] > 0, "an uncapped conversation costs unboundedly"


def _env_config(run_script) -> dict:
    result = run_script(
        "skills/eval-simulation/scripts/gen_sim_config.py", "--kind", "env",
        "--tools", "lookup_policy,submit_claim", "--json",
    )
    return json.loads(result.stdout)["config"]


def test_environment_simulation_uses_the_real_adk_field_names(run_script):
    """The names ADK declares, checked against 2.9.2 and 2.10.0 alike. `mock_strategy` and `probability`
    do not exist: a config using them validates as extras and simulates
    nothing."""
    config = _env_config(run_script)
    rendered = json.dumps(config)
    assert '"mock_strategy"' not in rendered, "the field is mock_strategy_type"
    assert '"probability"' not in rendered, "the field is injection_probability"

    tools = config["tool_simulation_configs"]
    assert len(tools) == 2, "one entry per named tool, and it must not be empty"
    assert {t["tool_name"] for t in tools} == {"lookup_policy", "submit_claim"}

    for tool in tools:
        assert "mock_strategy_type" in tool
        for injection in tool["injection_configs"]:
            assert "injection_probability" in injection


def test_environment_simulation_serializes_the_strategy_as_an_integer(run_script):
    """MockStrategy is a plain enum.Enum with integer values, so pydantic
    validates by value. The member name is rejected outright."""
    for tool in _env_config(run_script)["tool_simulation_configs"]:
        strategy = tool["mock_strategy_type"]
        assert isinstance(strategy, int) and not isinstance(strategy, bool)
        assert strategy in (0, 1, 2)


def test_environment_simulation_keeps_injections_off_by_default(run_script):
    """injection_probability defaults to 1.0, so "off" has to be written down.
    Random failures make a suite flaky, not robust."""
    for tool in _env_config(run_script)["tool_simulation_configs"]:
        assert tool["injection_configs"], "an entry with no injections needs a strategy"
        for injection in tool["injection_configs"]:
            assert injection["injection_probability"] == 0.0


def test_every_injection_carries_exactly_one_outcome(run_script):
    """ADK rejects an InjectionConfig with both injected_error and
    injected_response, and one with neither - so a latency-only entry is not a
    legal way to say "slow but fine"."""
    for tool in _env_config(run_script)["tool_simulation_configs"]:
        for injection in tool["injection_configs"]:
            outcomes = [k for k in ("injected_error", "injected_response") if injection.get(k)]
            assert len(outcomes) == 1, f"exactly one outcome per entry, got {outcomes}"

            error = injection.get("injected_error")
            if error:
                assert isinstance(error["injected_http_error_code"], int)
                assert error["error_message"].strip(), "both fields are required"

            latency = injection.get("injected_latency_seconds", 0.0)
            assert latency <= 120.0, "ADK caps injected latency at 120 seconds"


def test_environment_simulation_needs_tools(run_script):
    result = run_script("skills/eval-simulation/scripts/gen_sim_config.py", "--kind", "env")
    assert result.returncode == 2 and "--tools" in result.stderr


def test_validation_catches_scenarios_with_no_simulator(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    run_script(
        "skills/eval-simulation/scripts/gen_scenarios.py", agent,
        "--spec", json.dumps([{"eval_id": "memory_sim", "starting_prompt": "hi",
                               "conversation_plan": "Ask, then stop."}]),
        "--new", "simulated",
    )
    result = run_script("skills/eval-simulation/scripts/validate_sim_config.py", agent, "--json")
    found = {f["id"] for f in json.loads(result.stdout)["findings"]}
    assert "SIM001" in found, "scenario cases with no user_simulator_config"
    assert "SIM003" in found, "reference metrics cannot score a simulated run"
    assert result.returncode == 1


def test_a_simulation_config_validates_clean(run_script, tmp_agent, plugin_root):
    import shutil

    agent = tmp_agent("recipes_good")
    config = agent / "tests/eval/eval_config.sim.json"
    shutil.copy(
        plugin_root / "skills/eval-simulation/assets/templates/user_simulator_config.json", config
    )
    run_script(
        "skills/eval-simulation/scripts/gen_scenarios.py", agent,
        "--spec", json.dumps([{"eval_id": "memory_sim", "starting_prompt": "hi",
                               "conversation_plan": "Ask, then stop."}]),
        "--new", "simulated",
    )
    result = run_script(
        "skills/eval-simulation/scripts/validate_sim_config.py", agent, "--config", config, "--json"
    )
    payload = json.loads(result.stdout)
    assert payload["discovered"]["uses_user_simulation"] is True
    assert payload["discovered"]["simulation_compatible_metrics"]
    assert "SIM003" not in {f["id"] for f in payload["findings"]}


# -- eval-ci ---------------------------------------------------------------

def test_both_tiers_are_generated(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    result = run_script("skills/eval-ci/scripts/gen_workflows.py", agent, "--tier", "both", "--json")
    payload = json.loads(result.stdout)
    names = {p.split("/")[-1] for p in payload["written"]}
    assert names == {"adk-evals-pr.yml", "adk-evals-nightly.yml"}


def test_the_generated_pr_workflow_gates_on_a_real_exit_code(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    run_script("skills/eval-ci/scripts/gen_workflows.py", agent, "--tier", "pr")
    workflow = (agent / ".github/workflows/adk-evals-pr.yml").read_text()
    assert "run_evals.py" in workflow
    assert "--not-evaluated fail" in workflow
    assert "id-token: write" in workflow
    assert "upload-artifact" in workflow


def test_the_generated_workflows_pass_the_linter(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    run_script("skills/eval-ci/scripts/gen_workflows.py", agent, "--tier", "both")
    result = run_script("skills/eval-ci/scripts/lint_workflows.py", agent)
    assert result.returncode == 0, result.stdout


def test_api_key_auth_omits_the_workload_identity_step(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    run_script("skills/eval-ci/scripts/gen_workflows.py", agent, "--tier", "pr", "--auth", "api_key")
    workflow = (agent / ".github/workflows/adk-evals-pr.yml").read_text()
    assert "google-github-actions/auth" not in workflow
    assert "GOOGLE_API_KEY" in workflow


def test_a_missing_nightly_config_is_reported_as_a_warning(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    result = run_script(
        "skills/eval-ci/scripts/gen_workflows.py", agent, "--tier", "nightly", "--json"
    )
    payload = json.loads(result.stdout)
    assert any("nightly" in w for w in payload["warnings"])
    assert result.returncode == 1


def test_generation_requires_a_config_to_gate_on(run_script, tmp_agent):
    agent = tmp_agent("flat_good")
    result = run_script("skills/eval-ci/scripts/gen_workflows.py", agent, "--tier", "pr")
    assert result.returncode == 2 and "eval-criteria" in result.stderr


def test_the_linter_catches_a_workflow_that_cannot_fail(run_script, tmp_path):
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "eval.yml").write_text(
        "name: evals\non: [pull_request]\njobs:\n  e:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - uses: google-github-actions/auth@v2\n"
        "      - env:\n          GOOGLE_API_KEY: AIzaSyFAKEKEY1234567890\n"
        "        run: adk eval app cases.evalset.json\n"
    )
    result = run_script("skills/eval-ci/scripts/lint_workflows.py", tmp_path, "--json")
    found = {f["id"] for f in json.loads(result.stdout)["findings"]}
    assert "CI001" in found, "bare adk eval always exits 0"
    assert "CI003" in found, "Workload Identity without id-token: write"
    assert "CI004" in found, "a hardcoded credential"
    assert result.returncode == 1


def test_maturity_starts_at_zero_without_cases(run_script, tmp_agent):
    agent = tmp_agent("flat_good")
    result = run_script("skills/eval-ci/scripts/assess_maturity.py", agent, "--json")
    payload = json.loads(result.stdout)
    assert payload["level"] == 0
    assert payload["next_steps"]


def test_cases_alone_reach_level_one(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    payload = json.loads(
        run_script("skills/eval-ci/scripts/assess_maturity.py", agent, "--json").stdout
    )
    assert payload["level"] == 1
    assert payload["facts"]["cases"] == 3
    assert any("eval-ci" in step for step in payload["next_steps"])


def test_a_gated_workflow_reaches_level_two(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    run_script("skills/eval-ci/scripts/gen_workflows.py", agent, "--tier", "pr")
    payload = json.loads(
        run_script("skills/eval-ci/scripts/assess_maturity.py", agent, "--json").stdout
    )
    assert payload["level"] == 2
    assert payload["facts"]["gated"] is True


def test_maturity_names_the_missing_adversarial_layer(run_script, tmp_agent):
    agent = tmp_agent("recipes_good")
    payload = json.loads(
        run_script("skills/eval-ci/scripts/assess_maturity.py", agent, "--json").stdout
    )
    assert any("adversarial" in gap for gap in payload["gaps"])
