# Skills in ADK agents: progressive disclosure, lifecycles, evals

An ADK skill is a folder (or an inline object) holding a task's instructions and
the files that go with them, which the agent loads only when the task comes up.
`SkillToolset` turns a set of skills into tools. The point is the context window:
a long procedure the agent needs once a week should not sit in every request.
The format follows the agentskills.io spec, the one Claude Code skills use too.

## Anatomy of a skill

```
claim-policy/
├── SKILL.md          # frontmatter + instructions (required)
├── references/       # read on demand with load_skill_resource
├── assets/           # templates, data
└── scripts/          # run with run_skill_script
```

`SKILL.md` starts with YAML frontmatter, checked by `models.Frontmatter`:

| Field | Rule (2.12.0, measured) |
|---|---|
| `name` | required; lowercase kebab-case (`a-z`, `0-9`, single hyphens, none leading or trailing), at most 64 characters; **must equal the directory name** for `load_skill_from_dir` |
| `description` | required; non-empty, at most 1024 characters |
| `license`, `compatibility` | optional strings |
| `allowed_tools` | optional string; `allowed-tools` is accepted as the alias |
| `metadata` | optional dict; extra keys allowed. ADK reads `adk_additional_tools` (a list of tool names the skill contributes once loaded) and `adk_inject_state` (`true` substitutes `{var}` from session state into the instructions) |

Unknown top-level keys are allowed (`extra="allow"`). A bad name or an
over-long description raises a `ValidationError` at import, so the agent never
loads; a name that differs from its folder raises `ValueError: Skill name 'x'
does not match directory name 'y'`.

The body is the instructions. Keep it under about 500 lines and push detail into
`references/`, which the agent reads only when a step says to.

## Three levels of disclosure

| Level | What | When it is in context | Budget |
|---|---|---|---|
| **L1** metadata | each skill's `name` and `description` | always: `list_skills` output, or the system instruction in `EAGER` mode | about 100 tokens per skill |
| **L2** instructions | the `SKILL.md` body | after `load_skill` | under about 5k tokens |
| **L3** resources | `references/`, `assets/`, `scripts/` | after `load_skill_resource` or `run_skill_script` | only what the step needs |

The **description is the trigger**. It is all the model sees before deciding to
load, so it must say what the skill does and when to use it, in words a user's
request would match. "Helps with claims" never activates; "Checks an expense
claim against the travel and meals policy before submission" does.

## The tools `SkillToolset` generates

```python
from google.adk.skills import load_skill_from_dir, models
from google.adk.tools.skill_toolset import SkillToolset

policy = load_skill_from_dir(pathlib.Path(__file__).parent / "skills" / "claim-policy")
toolset = SkillToolset(skills=[policy])
root_agent = Agent(name="claims_agent", model=MODEL, instruction=..., tools=[toolset])
```

| Tool | Arguments | Present |
|---|---|---|
| `list_skills` | none | `LAZY` discovery only (the default) |
| `load_skill` | `skill_name` | always |
| `load_skill_resource` | `skill_name`, `file_path` (e.g. `references/limits.md`) | always |
| `run_skill_script` | `skill_name`, `file_path`, optional `args`, `short_options`, `positional_args` | always; needs a `code_executor` or an `environment` to actually run |
| `search_skills` | `query` | only with a `registry` |
| `unload_skill` | `skill_name` | only with `ADK_ENABLE_SKILL_LIFECYCLE=1` |

An eval case that expects a skill to activate expects the call
`load_skill` with `{"skill_name": "claim-policy"}`. The argument is
`skill_name`, not `name`.

Constructor (2.12.0): `SkillToolset(skills=None, *, registry, code_executor,
environment, skills_folder, script_timeout=300, save_output_artifacts=False,
additional_tools, tool_name_prefix, tool_filter, discovery_mode=LAZY,
lifecycle_config=None)`. `code_executor` and `environment` are mutually
exclusive; `skills_folder` needs an `environment` and an absolute path; duplicate
skill names raise. `discovery_mode` and `lifecycle_config` arrived in 2.10;
`save_output_artifacts` in 2.12. 2.9 has the rest.

## Four ways to supply skills

| Pattern | How | Use for |
|---|---|---|
| **Inline** | `models.Skill(frontmatter=models.Frontmatter(name=..., description=...), instructions="...", resources=models.Resources(references={...}, assets={...}, scripts={...}))` | a short skill that belongs with the code |
| **File-based** | `load_skill_from_dir(path)` for one folder, `load_skills_from_dir(path)` for every folder under it | the normal case; reviewable as Markdown |
| **External** | `load_skill_from_gcs_dir(bucket_name=..., skills_base_path=..., skill_id=...)`, or a `SkillRegistry` (e.g. `GCPSkillRegistry`) passed as `registry=` | skills shared across agents or teams |
| **Meta** | a skill whose instructions write new skills (a "skill creator") | letting the agent grow its own catalogue; review its output like code before loading it |

All loaders are in `google.adk.skills` (`_async` variants exist). Generated or
downloaded skills are code that runs inside your agent: read them, test them,
and pin the version you reviewed.

## Discovery and lifecycle (2.10+)

**Discovery.** `SkillDiscoveryMode.LAZY` (default) offers `list_skills`, costing
a model step before the first load and keeping names out of the system
instruction; right for a large or changing catalogue. `EAGER` drops `list_skills`
and injects the catalogue into the system instruction as XML, so the model can
call `load_skill` straight away; right for a few stable skills. Registry skills
are reachable only through `search_skills` in either mode.

**Lifecycle.** `lifecycle_config=SkillLifecycleConfig(...)`:

| Field | Default | Meaning |
|---|---|---|
| `enabled` | `True` | `False` keeps every skill active for the session regardless |
| `default_mode` | `PERSISTENT` | `PERSISTENT` stays active; `BOUNDED` also counts against the cap; `EPHEMERAL` is released when the turn that loaded it ends (under `run_live` it behaves as persistent) |
| `max_active_skills` | 5 | how many `BOUNDED` skills may be active per agent; loading past it releases the least recently loaded; below 1 raises `ValueError` |
| `skill_overrides` | `{}` | per-skill modes, by name |
| `revalidate_skills` | `False` | re-check active skills against their source each request; costs a lookup per skill per turn and invalidates the prompt cache when one changed |

**The experimental flag is narrower than it sounds.** `ADK_ENABLE_SKILL_LIFECYCLE=1`
(experimental, off by default) gates exactly three things: the `unload_skill`
tool, the sentence about it in the default skill instruction, and pruning an
unloaded skill's instructions from later requests. The modes, the cap and the
overrides work without it, measured on 2.12.0.

Lifecycles trade context for repeat loads. A skill released every turn is
reloaded every turn; put that in the eval (a two-turn case expecting
`load_skill` twice) before you rely on it.

## Script outputs as artifacts (2.12)

`SkillToolset(..., code_executor=..., save_output_artifacts=True)` saves files a
skill script writes as artifacts in the artifact service, instead of leaving them
in the sandbox. Caps: 5 MiB per file, 16 MiB per run, 50 files. Only supported
with `code_executor`; combined with `environment` it raises `ValueError`. The
run needs an artifact service; the dev UI and the eval generator create an
in-memory one when none is given. Artifacts keep large outputs out of the chat history, which is the
[context-engineering.md](context-engineering.md) principle.

## What the review checks

`/adk:agent-review` and `lint_agent.py` detect `SkillToolset(...)`, inline
`models.Skill(...)` and `load_skill_from_dir` / `load_skills_from_dir` with a
literal path, read each `SKILL.md`, and report:

| Id | Severity | Finding |
|---|---|---|
| `AGT070` | **error** | invalid skill frontmatter: no `SKILL.md` or the skill directory is missing; `name` missing, not lowercase kebab-case, over 64 characters, or different from the directory name; `description` empty or over 1024 characters; `compatibility` over 500; `metadata` values of the wrong type; ADK raises at import (`ValidationError` or `ValueError`), so the agent never loads |
| `AGT071` | warn | a skill body over 500 lines or about 5k tokens (characters / 4); it is loaded whole on `load_skill`; move detail into `references/` |
| `AGT072` | info | the agent instruction repeats at least 40% of a skill's distinct non-blank lines (12+ characters) verbatim; the procedure is paid for on every request, which defeats progressive disclosure; keep it in the skill only |
| `AGT073` | info | a skill description shorter than 40 characters; the description is all the model sees before loading, so it drives activation; say what the skill does and when |
| `AGT074` | info | `lifecycle_config` uses `BOUNDED` / `EPHEMERAL` or `skill_overrides` while the agent's `.env` lacks `ADK_ENABLE_SKILL_LIFECYCLE`; a known runner below 2.10 adds that `SkillLifecycleConfig` exists only from 2.10; the modes and the cap work without the flag; it adds `unload_skill` and pruning of released skills' instructions (experimental) |
| `AGT075` | **error** | `SkillLifecycleConfig(max_active_skills < 1)`, literal or through a constant; ADK raises `ValueError` at construction |

`load_skill`, `list_skills` and `load_skill_resource` count as known tools when
an evalset expects them, so coverage and tool cross-checks do not flag them.

## Evaluating a skill-equipped agent

Three scopes, from the Firebase team's eval-driven skill development
(`/adk:adk` `eval-engineering.md` maps them onto agents without skills):

1. **One skill**, 5 to 10 cases: given the skill is loaded, does the agent follow
   it? Expect the tool calls the skill's steps prescribe.
2. **Activation**: does the right request load the right skill, and an unrelated
   request load none? This is the description under test.
3. **End to end**: the whole journey, skills and all, as an ordinary evalset.

Activation has its own category in `/adk:eval-create`. In a `gen_cases.py` spec a
turn's `tools` list can name a skill instead of a tool call:

```json
[{"eval_id": "activation_claim_policy_hotel",
  "turns": [{"user": "Is a 900 EUR hotel night within policy?",
             "tools": [{"skill": "claim-policy"},
                       {"name": "lookup_limit", "args": {"category": "hotel"}}],
             "response": ""}]}]
```

`{"skill": "claim-policy"}` expands to the call `load_skill` with
`{"skill_name": "claim-policy"}` (prefixed when the toolset sets
`tool_name_prefix`; `"skill": "..."` on the turn itself puts the load first). The template
`eval-create/assets/templates/evalsets/activation.evalset.json` holds a positive
and a negative case. `coverage_matrix.py` reports a registered skill no case
activates (`EVS073`), and `validate_evalset.py` warns when a case expects
`load_skill` for a skill the agent does not register (`EVS074`). Score activation
with `tool_trajectory_avg_score` in `IN_ORDER` or `ANY_ORDER` mode, so a
`list_skills` call before the load does not fail the case.

---

Sources: Nigam and Saboo, "Developer's guide to building ADK agents with
skills", Google Developers Blog, 2026-04-01,
https://developers.googleblog.com/developers-guide-to-building-adk-agents-with-skills/
(the format, L1/L2/L3 and their budgets, the four patterns; paraphrased);
Charlotte Liang, "Eval-driven development: how we build better agent skills for
Firebase", Firebase blog, 2026-08-11,
https://firebase.blog/posts/2026/08/eval-driven-development-agent-skills (the
three scopes). `adk-python: tools/skill_toolset.py`
(tools, `SkillDiscoveryMode`, `SkillLifecycleMode`, `SkillLifecycleConfig`, the
artifact caps; `save_output_artifacts` is commit `beaef0d`, in `v2.12.0`),
`skills/models.py`, `skills/_utils.py`, `features/_feature_registry.py`
(`SKILL_LIFECYCLE`), `contributing/samples/environment_and_skills/`; every
signature, tool schema and validation error above measured on the installed
2.12.0 wheel, with 2.9.2 and 2.11.0 compared for the constructor.
