"""Context caching through the scripts, compare_runs.py, the cached column in
summarize_results.py, trajectory custom metrics, and the conformance file names."""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
#: Interpreters with a released ADK, for the checks that must import ADK. They
#: live beside the plugin checkout; a test that needs one is skipped without it.
VENVS = {
    version: Path(os.environ.get(f"ADK_SKILLS_PYTHON_{version.replace('.', '')}",
                                 PLUGIN_ROOT.parent / ".venvs" / f"adk{version.replace('.', '')}"
                                 / "bin" / "python"))
    for version in ("2.9.2", "2.11")
}
CACHE_IDS = {"AGT060", "AGT061", "AGT062", "AGT063", "AGT064", "AGT065", "AGT066", "AGT067"}
EFFICIENCY = "eval_history/app_efficiency_1790000000.123456.evalset_result.json"
CACHED = "eval_history/app_efficiency_cached_1790000100.5.evalset_result.json"


def finding_ids(payload: dict) -> list[str]:
    return [f["id"] for f in payload["findings"]]


def need_python(version: str) -> Path:
    python = VENVS[version]
    if not python.is_file():
        pytest.skip(f"no ADK {version} interpreter at {python}")
    return python


# -- lint and discover on the cached fixtures ---------------------------------

def test_the_cached_fixture_lints_clean(run_script, agent_dir):
    result = run_script("skills/agent-review/scripts/lint_agent.py", agent_dir("cached_agent"),
                        "--no-backend", "--json")
    payload = json.loads(result.stdout)
    assert result.returncode == 0, result.stdout
    assert not CACHE_IDS & set(finding_ids(payload))
    discovered = payload["discovered"]
    assert discovered["static_instruction_chars"] > 1500
    assert discovered["static_instruction_dynamic"] is False
    assert discovered["cache_prefix_tokens_estimate"] >= 2048
    assert discovered["context_cache_config"] == {
        "min_tokens": 2048, "ttl_seconds": 600, "cache_intervals": 5,
    }
    assert discovered["sop_rules"] > 5, "rules are read from the static instruction"


def test_the_broken_cached_fixture_reports_agt060_and_agt062(run_script, agent_dir):
    result = run_script("skills/agent-review/scripts/lint_agent.py",
                        agent_dir("cached_agent_broken"), "--no-backend", "--json")
    payload = json.loads(result.stdout)
    assert result.returncode == 1, "AGT062 is an error"
    assert {"AGT060", "AGT062", "AGT067"} <= set(finding_ids(payload))
    assert payload["discovered"]["static_instruction_dynamic"] is True


def test_discover_reports_the_cache_facts_in_the_agent_section(run_script, agent_dir):
    result = run_script("skills/adk/scripts/discover.py", agent_dir("cached_agent"),
                        "--json", "--no-probe")
    payload = json.loads(result.stdout)
    agent = payload["discovered"]["agent"]
    assert agent["static_instruction_chars"] > 1500
    assert agent["static_instruction_dynamic"] is False
    assert agent["context_cache_config"]["ttl_seconds"] == 600
    assert agent["cache_prefix_tokens_estimate"] >= 2048
    assert not CACHE_IDS & set(finding_ids(payload))


def test_a_suite_of_one_request_cases_gets_agt063(run_script, tmp_agent):
    agent = tmp_agent("cached_agent")
    evalset = agent / "claims.evalset.json"
    doc = json.loads(evalset.read_text())
    case = doc["eval_cases"][0]
    case["conversation"] = [case["conversation"][1]]          # one turn, no tool call
    evalset.write_text(json.dumps(doc))
    lint = json.loads(run_script("skills/agent-review/scripts/lint_agent.py", agent,
                                 "--no-backend", "--json").stdout)
    discover = json.loads(run_script("skills/adk/scripts/discover.py", agent,
                                     "--json", "--no-probe").stdout)
    assert "AGT063" in finding_ids(lint)
    assert "AGT063" in finding_ids(discover)


def test_a_plugin_newer_than_the_runner_is_an_error(run_script, tmp_agent, fixtures, monkeypatch):
    agent = tmp_agent("cached_agent")
    source = agent / "agent.py"
    source.write_text(source.read_text().replace(
        "from google.genai import types",
        "from google.genai import types\nfrom google.adk.plugins import ToolCallIntegrityPlugin",
    ))
    monkeypatch.setenv("ADK_PYTHON", str(fixtures / "bin" / "fake_adk.py"))
    monkeypatch.setenv("FAKE_ADK_VERSION", "2.10.0")
    old = json.loads(run_script("skills/adk/scripts/discover.py", agent, "--json").stdout)
    monkeypatch.setenv("FAKE_ADK_VERSION", "2.11.0")
    new = json.loads(run_script("skills/adk/scripts/discover.py", agent, "--json").stdout)
    finding = next(f for f in old["findings"] if f["id"] == "AGT066")
    assert finding["severity"] == "error" and "2.11" in finding["message"]
    assert "AGT066" not in finding_ids(new)


# -- scaffold --with-cache ----------------------------------------------------

def scaffold(run_script, target, layout, *extra):
    return run_script(
        "skills/agent-create/scripts/scaffold_agent.py",
        "--name", "claims_agent", "--dir", target, "--layout", layout, "--with-cache",
        "--description", "an agent that approves or rejects expense claims.",
        "--tools", "lookup_policy:category=literal(travel|meals);"
                   "submit_claim:claim_id=pattern(^CLM-[0-9]{6}$),amount_usd=float",
        "--json", *extra,
    )


@pytest.mark.parametrize("layout, agent_rel", [("recipes", "app"), ("flat", "claims")])
def test_with_cache_scaffolds_a_static_prefix_and_a_cache_config(run_script, tmp_path,
                                                                 layout, agent_rel):
    target = tmp_path / "claims"
    payload = json.loads(scaffold(run_script, target, layout).stdout)
    assert payload["with_cache"] is True
    source = (target / agent_rel / "agent.py").read_text()
    assert "static_instruction=STATIC_INSTRUCTION" in source
    assert "ContextCacheConfig(min_tokens=2048, ttl_seconds=600, cache_intervals=5)" in source
    assert "context_cache_config=CACHE_CONFIG" in source
    ast.parse(source)

    lint = run_script("skills/agent-review/scripts/lint_agent.py", target,
                      "--no-backend", "--json")
    review = json.loads(lint.stdout)
    assert lint.returncode == 0, review["findings"]
    # The placeholder procedure really is a few hundred tokens, so the honest
    # note is AGT067; nothing else about the caching setup is wrong.
    assert set(finding_ids(review)) & CACHE_IDS == {"AGT067"}
    assert review["discovered"]["context_cache_config"]["cache_intervals"] == 5
    assert review["discovered"]["sop_rules"] > 0

    evalsets = list(target.rglob("*.evalset.json")) + list(target.rglob("*.test.json"))
    cases = json.loads(evalsets[0].read_text())["eval_cases"]
    assert any(len(case["conversation"]) > 1 for case in cases), "a second turn shows the cache"


def test_with_cache_and_a_long_procedure_is_fully_clean(run_script, tmp_path, agent_dir):
    sop = tmp_path / "sop.md"
    source = (agent_dir("cached_agent") / "agent.py").read_text()
    procedure = source.split('CLAIMS_PROCEDURE = """', 1)[1].split('"""', 1)[0]
    sop.write_text(procedure + "\n\nAPPENDIX: THE SAME PROCEDURE, FOR AUDITORS\n\n" + procedure)
    target = tmp_path / "claims"
    scaffold(run_script, target, "recipes", "--sop-file", sop)
    review = json.loads(run_script("skills/agent-review/scripts/lint_agent.py", target,
                                   "--no-backend", "--json").stdout)
    assert not CACHE_IDS & set(finding_ids(review)), review["findings"]


def test_with_cache_does_not_combine_with_workflow(run_script, tmp_path):
    result = scaffold(run_script, tmp_path / "claims", "recipes", "--workflow")
    assert result.returncode == 2
    assert "--workflow" in result.stderr


@pytest.mark.parametrize("version", ["2.9.2", "2.11"])
@pytest.mark.parametrize("layout", ["recipes", "flat"])
def test_the_cached_scaffold_loads_under_adk(run_script, tmp_path, monkeypatch, version, layout):
    python = need_python(version)
    target = tmp_path / "claims"
    scaffold(run_script, target, layout)
    monkeypatch.setenv("ADK_PYTHON", str(python))
    result = run_script("skills/agent-review/scripts/load_agent.py", target, "--json")
    payload = json.loads(result.stdout)
    assert payload["loaded"] is True, payload
    assert payload["context_cache_config"] == {
        "cache_intervals": 5, "ttl_seconds": 600, "min_tokens": 2048,
    }
    assert payload["static_instruction_chars"] > 0


@pytest.mark.parametrize("version", ["2.9.2", "2.11"])
def test_the_cached_fixture_loads_under_adk(run_script, agent_dir, monkeypatch, version):
    monkeypatch.setenv("ADK_PYTHON", str(need_python(version)))
    payload = json.loads(run_script("skills/agent-review/scripts/load_agent.py",
                                    agent_dir("cached_agent"), "--json").stdout)
    assert payload["loaded"] is True, payload
    assert payload["static_instruction_chars"] > 1500, "Content(parts=[Part(text=...)])"


# -- summarize_results.py: the cached column ----------------------------------

def test_the_efficiency_table_has_a_cached_column_and_a_hit_ratio(run_script, fixtures):
    result = run_script("skills/eval-analyze/scripts/summarize_results.py", fixtures / CACHED)
    assert result.returncode == 0, result.stderr
    assert "| metric | value | unit | cached |" in result.stdout
    assert "| `token_usage_v1` | 300 | tokens | 190 |" in result.stdout
    assert "Cache-hit ratio: 79.8% of prompt tokens" in result.stdout
    assert "Context cache served 84.3% of this run's prompt tokens." in result.stdout


def test_the_cache_figures_reach_the_json(run_script, fixtures):
    payload = json.loads(run_script("skills/eval-analyze/scripts/summarize_results.py",
                                    fixtures / CACHED, "--json").stdout)[0]
    assert payload["run"]["cache_hit_ratio"] == pytest.approx(0.8433, abs=1e-4)
    assert payload["cases"][0]["cache"] == {
        "cached_tokens": 190.0, "prompt_tokens": 238.0, "cache_hit_ratio": 0.7983,
    }


def test_a_run_without_efficiency_metrics_has_no_cache_figures(run_script, fixtures):
    payload = json.loads(run_script(
        "skills/eval-analyze/scripts/summarize_results.py",
        fixtures / "eval_history/app_reliability_1730001000.evalset_result.json", "--json",
    ).stdout)[0]
    assert payload["run"]["cache_hit_ratio"] is None
    assert all(case["cache"] is None for case in payload["cases"])


# -- compare_runs.py ----------------------------------------------------------

def compare(run_script, *args):
    return run_script("skills/eval-analyze/scripts/compare_runs.py", *args)


def test_compare_runs_reports_the_cache_gain_and_no_regression(run_script, fixtures):
    result = compare(run_script, fixtures / EFFICIENCY, fixtures / CACHED)
    assert result.returncode == 0, result.stderr
    assert "NO REGRESSION" in result.stdout
    assert "uncached prompt tokens -78.3%" in result.stdout
    assert "| cache-hit ratio | 16.0% | 84.3% | +68.3 pts |" in result.stdout
    assert "`happy_equipment_then_follow_up`: new in the candidate" in result.stdout
    assert "the eval sets differ" in result.stdout


def test_compare_runs_json_carries_every_figure(run_script, fixtures):
    payload = json.loads(compare(run_script, fixtures / EFFICIENCY, fixtures / CACHED,
                                 "--json").stdout)
    deltas = payload["deltas"]
    assert set(deltas) == {
        "pass_rate", "prompt_tokens", "cached_tokens", "uncached_prompt_tokens",
        "output_tokens", "cache_hit_ratio", "duration_s", "tool_calls", "model_calls",
    }
    assert deltas["cached_tokens"] == {"baseline": 40.0, "candidate": 245.0, "delta": 205.0,
                                       "relative": 5.125}
    assert deltas["duration_s"]["delta"] == pytest.approx(-0.6)
    assert payload["regression"] is False and payload["flips"] == []
    assert payload["verdict"].startswith("NO REGRESSION")


def test_a_case_flipping_to_failed_is_a_regression(run_script, fixtures, tmp_path):
    candidate = tmp_path / "app_efficiency_1790000200.0.evalset_result.json"
    doc = json.loads((fixtures / EFFICIENCY).read_text())
    doc["eval_case_results"][0]["final_eval_status"] = 2
    candidate.write_text(json.dumps(doc))
    result = compare(run_script, fixtures / EFFICIENCY, candidate, "--json")
    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert payload["regressed_cases"] == ["happy_travel_within_policy"]
    assert payload["pass_rate_dropped"] is True
    assert payload["verdict"].startswith("REGRESSION")


def test_a_fixed_case_is_an_improvement_and_missing_efficiency_reads_na(run_script, fixtures):
    history = fixtures / "eval_history"
    result = compare(run_script, history / "app_reliability_1730001000.evalset_result.json",
                     history / "app_reliability_1730001001.evalset_result.json")
    assert result.returncode == 0
    assert "IMPROVED: pass rate 50.0% -> 100.0%, 1 case(s) fixed" in result.stdout
    assert "| prompt tokens / invocation | n/a | n/a | n/a |" in result.stdout


def test_a_history_dir_needs_latest_when_it_holds_several_runs(run_script, fixtures):
    history = fixtures / "eval_history"
    refused = compare(run_script, history, history, "--evalset", "reliability")
    assert refused.returncode == 2 and "--latest" in refused.stderr
    latest = compare(run_script, history, history, "--evalset", "reliability", "--latest",
                     "--json")
    payload = json.loads(latest.stdout)
    assert payload["baseline"]["runs"] == ["app_reliability_1730001004.evalset_result.json"]


def test_compare_runs_refuses_an_empty_side(run_script, tmp_path, fixtures):
    result = compare(run_script, fixtures / EFFICIENCY, tmp_path)
    assert result.returncode == 2
    assert "no eval results" in result.stderr


# -- gen_custom_metric.py: trajectory kinds and the config snippet ------------

def generate(run_script, out, *args):
    return run_script("skills/eval-criteria/scripts/gen_custom_metric.py",
                      "--name", out.stem, "--out", out, "--json", *args)


def test_trajectory_kinds_write_a_complete_metric_and_a_valid_snippet(run_script, tmp_path):
    out = tmp_path / "traj_precision.py"
    payload = json.loads(generate(run_script, out, "--kind", "trajectory-precision",
                                  "--threshold", "0.8", "--with-metric-info").stdout)
    snippet = payload["config_snippet"]
    assert snippet["criteria"] == {"traj_precision": 0.8}
    entry = snippet["custom_metrics"]["traj_precision"]
    assert entry["code_config"]["name"] == payload["dotted_path"]
    assert entry["metric_info"]["metric_value_info"]["interval"] == {
        "min_value": 0.0, "max_value": 1.0,
    }
    source = out.read_text()
    assert "def precision_recall(" in source and "return precision\n" in source
    assert "WITH_ARGS = False" in source
    assert "custom_function_path" not in source, "a criteria-level path is ignored by ADK"
    ast.parse(source)

    config = tmp_path / "eval_config.json"
    config.write_text(json.dumps(snippet))
    review = json.loads(run_script("skills/eval-criteria/scripts/validate_config.py", config,
                                   "--no-backend", "--no-probe", "--json").stdout)
    assert [f for f in review["findings"] if f["severity"] == "error"] == []
    assert not {"CFG051", "CFG057", "CFG058"} & set(finding_ids(review))


def test_with_args_needs_a_trajectory_kind(run_script, tmp_path):
    result = generate(run_script, tmp_path / "m.py", "--with-args")
    assert result.returncode == 2 and "--with-args" in result.stderr


def _scoring_helper(source: str):
    """The generated `precision_recall`, executed without importing ADK."""
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == "precision_recall")
    namespace: dict = {"Counter": Counter}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "<generated>", "exec"), namespace)
    return namespace["precision_recall"]


@pytest.mark.parametrize("actual, expected, scores", [
    ([("a", ""), ("b", ""), ("x", "")], [("a", ""), ("b", "")], (2 / 3, 1.0)),
    ([("a", "")], [("a", ""), ("b", "")], (1.0, 0.5)),
    ([("a", ""), ("a", "")], [("a", "")], (0.5, 1.0)),
    ([], [], (1.0, 1.0)),
    ([], [("a", "")], (0.0, 0.0)),
    ([("a", "")], [], (0.0, 1.0)),
])
def test_the_generated_scoring_helper(run_script, tmp_path, actual, expected, scores):
    out = tmp_path / "traj_recall.py"
    generate(run_script, out, "--kind", "trajectory-recall", "--with-args")
    assert "WITH_ARGS = True" in out.read_text()
    precision_recall = _scoring_helper(out.read_text())
    assert precision_recall(actual, expected) == pytest.approx(scores)


CHECK = """
import asyncio, importlib, json, sys
from google.adk.evaluation.eval_case import IntermediateData, Invocation
from google.adk.evaluation.eval_config import EvalConfig, get_eval_metrics_from_config
from google.adk.evaluation.metric_evaluator_registry import (
    MetricEvaluatorRegistry, register_custom_metrics_from_config)
from google.genai import types

def inv(calls):
    return Invocation(
        user_content=types.Content(role="user", parts=[types.Part(text="claim")]),
        intermediate_data=IntermediateData(tool_uses=[
            types.FunctionCall(name=name, args=args) for name, args in calls]))

actual = [inv([("lookup_policy", {"category": "travel"}), ("submit_claim", {"id": "1"}),
               ("reject_claim", {})])]
expected = [inv([("lookup_policy", {"category": "meals"}), ("submit_claim", {"id": "1"})])]
out = {}
for name in sys.argv[1:]:
    module = importlib.import_module(f"metrics_pkg.{name}")
    config = EvalConfig.model_validate(module.CONFIG_SNIPPET)
    registry = register_custom_metrics_from_config(config, MetricEvaluatorRegistry())
    metric = next(m for m in get_eval_metrics_from_config(config) if m.metric_name == name)
    evaluator = registry.get_evaluator(metric)
    scored = asyncio.run(evaluator.evaluate_invocations(actual, expected, None))
    unscored = asyncio.run(evaluator.evaluate_invocations(actual, None, None))
    out[name] = [scored.overall_score, scored.overall_eval_status.name,
                 [r.score for r in scored.per_invocation_results],
                 unscored.overall_eval_status.name]
print(json.dumps(out))
"""


@pytest.mark.parametrize("version", ["2.9.2", "2.11"])
def test_generated_metrics_score_a_synthetic_invocation_under_adk(run_script, tmp_path, version):
    python = need_python(version)
    package = tmp_path / "metrics_pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    generate(run_script, package / "precision_by_name.py", "--kind", "trajectory-precision",
             "--threshold", "0.5", "--with-metric-info")
    generate(run_script, package / "recall_by_args.py", "--kind", "trajectory-recall",
             "--with-args")
    generate(run_script, package / "skeleton_async.py", "--kind", "async", "--with-metric-info")
    (tmp_path / "check.py").write_text(CHECK)
    proc = subprocess.run(
        [str(python), "check.py", "precision_by_name", "recall_by_args", "skeleton_async"],
        cwd=tmp_path, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    scores = json.loads(proc.stdout.strip().splitlines()[-1])
    assert scores["precision_by_name"][0] == pytest.approx(2 / 3)
    assert scores["precision_by_name"][1:] == ["PASSED", [pytest.approx(2 / 3)], "NOT_EVALUATED"]
    assert scores["recall_by_args"] == [0.5, "FAILED", [0.5], "NOT_EVALUATED"]
    assert scores["skeleton_async"][1] == "PASSED"


# -- validate_config.py: the custom_metrics keys ------------------------------

def test_a_metric_key_with_min_max_is_reported_as_ignored(run_script, tmp_path):
    config = tmp_path / "eval_config.json"
    config.write_text(json.dumps({
        "criteria": {"m": 0.5},
        "custom_metrics": {"m": {
            "code_config": {"name": "pkg.mod.m"},
            "metric": {"metric_name": "m", "min_value": 0, "max_value": 1},
        }},
    }))
    payload = json.loads(run_script("skills/eval-criteria/scripts/validate_config.py", config,
                                    "--no-backend", "--no-probe", "--json").stdout)
    finding = next(f for f in payload["findings"] if f["id"] == "CFG057")
    assert finding["severity"] == "warn"
    assert "metric_info.metric_value_info" in finding["message"]


def test_a_metric_info_without_a_value_range_is_an_error(run_script, tmp_path):
    config = tmp_path / "eval_config.json"
    config.write_text(json.dumps({
        "criteria": {"m": 0.5},
        "custom_metrics": {"m": {"code_config": {"name": "pkg.mod.m"},
                                 "metric_info": {"metric_name": "m"}}},
    }))
    payload = json.loads(run_script("skills/eval-criteria/scripts/validate_config.py", config,
                                    "--no-backend", "--no-probe", "--json").stdout)
    finding = next(f for f in payload["findings"] if f["id"] == "CFG058")
    assert finding["severity"] == "error" and "metric_value_info" in finding["message"]


# -- conformance.py: the file names the released code reads -------------------

def test_a_case_named_after_the_stale_help_text_is_explained(run_script, fixtures, tmp_path):
    case = tmp_path / "tests" / "conformance" / "claims" / "travel"
    case.mkdir(parents=True)
    shutil.copy(fixtures / "conformance/spec.yaml", case / "input.yaml")
    for command in (["check", case], ["test", tmp_path / "tests"],
                    ["record", case, "--dry-run", "--no-port-check"]):
        result = run_script("skills/eval-run/scripts/conformance.py", *command)
        assert result.returncode == 2
        assert "CNF015" in result.stderr and "input.yaml" in result.stderr
        assert "spec.yaml" in result.stderr


@pytest.mark.parametrize("command", [
    ["record", "{case}", "--mode", "bidi", "--dry-run", "--no-port-check"],
    ["test", "{root}", "--streaming-mode", "bidi", "--dry-run"],
])
def test_bidi_is_refused_before_adk_raises_on_it(run_script, fixtures, tmp_path, command):
    case = tmp_path / "tests" / "conformance" / "claims" / "travel"
    case.mkdir(parents=True)
    shutil.copy(fixtures / "conformance/spec.yaml", case / "spec.yaml")
    args = [part.format(case=case, root=tmp_path / "tests") for part in command]
    result = run_script("skills/eval-run/scripts/conformance.py", *args)
    assert result.returncode == 2
    assert "CNF021" in result.stderr
