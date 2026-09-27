"""Conventions that hold across the whole plugin.

These are the rules that stop the plugin drifting as it grows: every skill has a
usable description, every reference cites its sources, every script supports
`--json`, every linked file exists, every script path a skill prints resolves —
including the cross-skill ones — no bare `SKILL_DIR` placeholder survives, and
nothing from a non-redistributable source has been copied in.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILLS = sorted(p for p in (PLUGIN_ROOT / "skills").iterdir() if p.is_dir())
SKILL_FILES = [p / "SKILL.md" for p in SKILLS]
REFERENCES = sorted((PLUGIN_ROOT / "skills").rglob("references/*.md"))
SCRIPTS = sorted(
    p for p in (PLUGIN_ROOT / "skills").rglob("scripts/*.py") if not p.name.startswith("_")
)
LIB_MODULES = sorted((PLUGIN_ROOT / "lib" / "adk_skills").glob("*.py"))

EXPECTED_SKILLS = {
    "adk", "agent-create", "agent-review", "agent-debug", "eval-create",
    "eval-criteria", "eval-review", "eval-run", "eval-analyze",
    "eval-simulation", "eval-ci",
}


def frontmatter(path: Path) -> dict[str, str]:
    """Parse the YAML-ish frontmatter without a YAML dependency."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path}: no frontmatter"
    block = text.split("---\n", 2)[1]
    fields: dict[str, str] = {}
    key = None
    for line in block.splitlines():
        match = re.match(r"^([a-z-]+):\s*(.*)$", line)
        if match:
            key = match.group(1)
            fields[key] = match.group(2).strip()
        elif key and line.strip():
            fields[key] = f"{fields[key]} {line.strip()}".strip()
    return fields


# -- structure -------------------------------------------------------------

def test_the_expected_skills_are_all_present():
    assert {p.name for p in SKILLS} == EXPECTED_SKILLS


def test_the_plugin_manifest_is_valid():
    manifest = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "adk"
    assert manifest["description"]
    assert re.match(r"^\d+\.\d+\.\d+$", manifest["version"])


def test_licensing_files_are_present():
    assert (PLUGIN_ROOT / "LICENSE").is_file()
    notices = (PLUGIN_ROOT / "THIRD_PARTY_NOTICES.md").read_text()
    for project in ("adk-python", "adk-docs", "adk-recipes"):
        assert project in notices


# -- SKILL.md --------------------------------------------------------------

@pytest.mark.parametrize("skill", SKILL_FILES, ids=lambda p: p.parent.name)
def test_every_skill_has_a_name_matching_its_directory(skill):
    assert frontmatter(skill)["name"] == skill.parent.name


@pytest.mark.parametrize("skill", SKILL_FILES, ids=lambda p: p.parent.name)
def test_every_description_says_when_to_use_and_when_not_to(skill):
    description = frontmatter(skill)["description"]
    assert len(description) > 200, "too short to route on"
    assert "Use when" in description
    assert "Don't use" in description, "a description needs to say what it is not for"


@pytest.mark.parametrize("skill", SKILL_FILES, ids=lambda p: p.parent.name)
def test_every_skill_declares_the_tools_it_needs(skill):
    tools = frontmatter(skill)["allowed-tools"]
    assert "AskUserQuestion" in tools, "the confirm step needs it"
    assert "Bash(python3 *)" in tools, "every skill runs its own scripts"


@pytest.mark.parametrize("skill", SKILL_FILES, ids=lambda p: p.parent.name)
def test_no_skill_forks_its_context(skill):
    """A forked context cannot ask the user anything, and every skill here
    confirms before acting."""
    assert "context" not in frontmatter(skill)


@pytest.mark.parametrize("skill", SKILL_FILES, ids=lambda p: p.parent.name)
def test_skill_bodies_stay_short_enough_to_load(skill):
    lines = skill.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 200, f"{len(lines)} lines; move detail into references/"


@pytest.mark.parametrize("skill", SKILL_FILES, ids=lambda p: p.parent.name)
def test_every_skill_has_a_completion_checklist(skill):
    assert "## Completion checklist" in skill.read_text(encoding="utf-8")


@pytest.mark.parametrize("skill", SKILL_FILES, ids=lambda p: p.parent.name)
def test_every_linked_reference_exists(skill):
    text = skill.read_text(encoding="utf-8")
    for link in re.findall(r"\]\((references/[^)]+)\)", text):
        assert (skill.parent / link).is_file(), f"{skill.parent.name}: dead link to {link}"


@pytest.mark.parametrize("skill", SKILL_FILES, ids=lambda p: p.parent.name)
def test_every_script_a_skill_names_exists(skill):
    text = skill.read_text(encoding="utf-8")
    for match in re.findall(r"(?:\$\{CLAUDE_SKILL_DIR\}/|`)(scripts/[a-z_]+\.py)", text):
        assert (skill.parent / match).is_file(), f"{skill.parent.name}: names a missing {match}"


#: `${CLAUDE_SKILL_DIR}/../<skill>/scripts/<name>.py`
CROSS_SKILL_SCRIPT = re.compile(
    r"\$\{CLAUDE_SKILL_DIR\}/\.\./([a-z-]+)/(scripts/[a-z_]+\.py)"
)


@pytest.mark.parametrize("skill", SKILL_FILES, ids=lambda p: p.parent.name)
def test_every_cross_skill_script_a_skill_names_exists(skill):
    """One skill calling another's script by path is how this plugin composes.
    A path that does not resolve is a dead command the model will still run."""
    text = skill.read_text(encoding="utf-8")
    for other, script in CROSS_SKILL_SCRIPT.findall(text):
        target = skill.parent.parent / other / script
        assert target.is_file(), f"{skill.parent.name}: names a missing {other}/{script}"


#: Every file that may print a command referring to the skill's own directory.
COMMAND_BEARING = SKILL_FILES + REFERENCES

#: A `SKILL_DIR` that is not the `${CLAUDE_...}` form the runtime actually sets.
BARE_SKILL_DIR = re.compile(r"(?<!\{CLAUDE_)\bSKILL_DIR\b")


@pytest.mark.parametrize(
    "document", COMMAND_BEARING, ids=lambda p: f"{p.parent.parent.name}/{p.name}"
)
def test_no_bare_skill_dir_placeholder_survives(document):
    """`SKILL_DIR` alone is not set by anything. A command containing it fails
    with an empty path, or worse, silently resolves relative to the cwd."""
    text = document.read_text(encoding="utf-8")
    offenders = [
        line.strip()
        for line in text.splitlines()
        if BARE_SKILL_DIR.search(line)
    ]
    assert offenders == [], f"use ${{CLAUDE_SKILL_DIR}}: {offenders}"


# -- references ------------------------------------------------------------

@pytest.mark.parametrize("reference", REFERENCES, ids=lambda p: f"{p.parent.parent.name}/{p.name}")
def test_every_reference_cites_its_sources(reference):
    """A claim about ADK behaviour the reader cannot check is a claim they have to
    take on trust."""
    text = reference.read_text(encoding="utf-8")
    assert "Sources:" in text, "add a Sources: footer"
    assert text.rstrip().endswith((".", ")", "`")), "the footer looks truncated"


@pytest.mark.parametrize("reference", REFERENCES, ids=lambda p: f"{p.parent.parent.name}/{p.name}")
def test_references_are_substantial_but_not_sprawling(reference):
    lines = reference.read_text(encoding="utf-8").splitlines()
    assert 25 <= len(lines) <= 250, f"{len(lines)} lines"


# -- scripts ---------------------------------------------------------------

@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: f"{p.parent.parent.name}/{p.name}")
def test_every_script_has_help_and_a_docstring(script):
    result = subprocess.run(
        [sys.executable, str(script), "--help"], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert len(result.stdout) > 200, "the help text should explain why, not just how"


#: Scripts that hand another tool's output straight through, so a JSON mode of
#: their own would misrepresent it.
PASSTHROUGH_SCRIPTS = {"run_pytest_eval.py"}


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: f"{p.parent.parent.name}/{p.name}")
def test_every_script_speaks_json(script):
    """Skills branch on the JSON; humans read the markdown."""
    if script.name in PASSTHROUGH_SCRIPTS:
        pytest.skip("passes another tool's output and exit code through unchanged")
    result = subprocess.run(
        [sys.executable, str(script), "--help"], capture_output=True, text=True, timeout=60
    )
    assert "--json" in result.stdout


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: f"{p.parent.parent.name}/{p.name}")
def test_every_script_finds_the_shared_library_the_same_way(script):
    text = script.read_text(encoding="utf-8")
    assert 'parents[3] / "lib"' in text, "scripts locate lib relative to their own path"


def code_outside_strings(path: Path) -> str:
    """The module's source with string literals blanked out.

    Several scripts carry probe source as a string, to be executed by a *different*
    interpreter that does have ADK. That is not an import here, so the
    dependency checks look at real code only.
    """
    import ast as _ast
    import io
    import tokenize

    source = path.read_text(encoding="utf-8")
    # From 3.12 an f-string is several tokens, so STRING alone is not enough.
    dropped = {tokenize.STRING, tokenize.COMMENT}
    for name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END"):
        if hasattr(tokenize, name):
            dropped.add(getattr(tokenize, name))
    kept: list[str] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type in dropped:
                continue
            kept.append(token.string)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return source
    _ast.parse(source)          # the file must still be valid Python
    return " ".join(kept)


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: f"{p.parent.parent.name}/{p.name}")
def test_scripts_import_no_third_party_package(script):
    """They must run on a bare interpreter, before the project's environment
    works."""
    code = code_outside_strings(script)
    banned = ("import yaml", "import pydantic", "import requests", "google.adk")
    for name in banned:
        assert name not in code, f"{script.name} imports {name} in real code"


# -- library ---------------------------------------------------------------

@pytest.mark.parametrize("module", LIB_MODULES, ids=lambda p: p.name)
def test_the_library_is_standard_library_only(module):
    code = code_outside_strings(module)
    for name in ("import yaml", "import pydantic", "import requests", "google.adk"):
        assert name not in code, f"{module.name} imports {name} in real code"


@pytest.mark.parametrize("module", LIB_MODULES, ids=lambda p: p.name)
def test_every_library_module_explains_itself(module):
    text = module.read_text(encoding="utf-8")
    assert text.lstrip().startswith('"""'), f"{module.name} has no module docstring"


def test_the_library_never_executes_agent_code():
    """Reviews must work on a project whose imports are the problem, so the
    extractor parses and never runs what it reads.

    `ast.literal_eval` is fine: it evaluates literals only, and executes nothing.
    """
    code = code_outside_strings(PLUGIN_ROOT / "lib/adk_skills/tools_ast.py")
    code = code.replace("ast . literal_eval", "").replace("ast.literal_eval", "")
    for name in ("exec(", "eval(", "importlib", "__import__", "subprocess"):
        assert name not in code, f"tools_ast.py uses {name}"


# -- provenance ------------------------------------------------------------

def test_nothing_from_the_course_repository_was_copied():
    """The LinkedIn Learning exercise files are not redistributable. The
    methodology is described in this plugin's own words, with original fixtures.
    """
    markers = (
        "procurement", "laptop_pro_16", "laptop_std_14", "hardware_mfa_token",
        "check_catalog", "quantum_laptop", "rack_server", "legacy_vpn_appliance",
        "mock_data.json",
    )
    offenders: list[str] = []
    for path in PLUGIN_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in (".md", ".py", ".json", ".yml", ".tmpl", ".yaml"):
            continue
        if ".git" in path.parts or path.name == "test_plugin_hygiene.py":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for marker in markers:
            if marker in text:
                offenders.append(f"{path.relative_to(PLUGIN_ROOT)}: {marker}")
    assert offenders == [], offenders


#: Phrasings this plugin deliberately retired when its prose was rewritten in
#: independent wording. They are not course text — they are the shapes that were
#: close enough to someone else's framing to be worth never drifting back into.
RETIRED_PHRASINGS = (
    "three tests wearing",
    "3 a.m.",
    "comfort blanket",
    "the evalset is the specification",
)


def test_retired_phrasings_do_not_come_back():
    offenders: list[str] = []
    for path in PLUGIN_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in (".md", ".py", ".json", ".yml", ".tmpl", ".yaml"):
            continue
        if ".git" in path.parts or path.name == "test_plugin_hygiene.py":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for marker in RETIRED_PHRASINGS:
            if marker in text:
                offenders.append(f"{path.relative_to(PLUGIN_ROOT)}: {marker}")
    assert offenders == [], offenders


def test_the_course_is_credited_where_its_methodology_is_used():
    credited = [
        p for p in REFERENCES
        if "LinkedIn Learning" in p.read_text(encoding="utf-8")
    ]
    assert len(credited) >= 8, "attribution belongs on every reference that uses the methodology"


def test_no_shipped_asset_is_gitignored():
    """The plugin's own `.gitignore` once matched `.env.example.tmpl` through the
    `.env.*` rule, so the published repo lacked the scaffold's env template.
    Every file under an assets directory must be publishable."""
    import shutil
    import subprocess

    if not shutil.which("git") or not (PLUGIN_ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    assets = sorted(
        str(p.relative_to(PLUGIN_ROOT))
        for p in PLUGIN_ROOT.glob("skills/*/assets/**/*") if p.is_file()
    )
    assert assets
    proc = subprocess.run(
        ["git", "check-ignore", "--no-index", *assets],
        cwd=PLUGIN_ROOT, capture_output=True, text=True,
    )
    assert proc.stdout.strip() == "", f"ignored shipped assets:\n{proc.stdout}"
