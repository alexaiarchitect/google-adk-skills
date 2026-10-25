"""Read an ADK agent statically: tools, schemas, agents, and known problems.

Everything here works on the syntax tree. Nothing is imported, so a broken
dependency, a missing virtualenv or a side effect in module scope cannot affect
a review, and reviews run in any Python 3.10+.

What the model sees of a tool is its **name, docstring and type hints** - ADK
derives the function declaration from exactly those. So a tool whose docstring
does not say when to use it, or whose parameters are untyped, is a tool the
model has to guess about, and guessing shows up as a trajectory failure.

Three things decide whether a name in an eval trajectory is real:

* **bound** - the name reached an agent's ``tools=`` list. Only a bound name can
  appear in a trajectory.
* **defined** - the function is in this project's source, so its contract can be
  checked.
* **opaque** - a toolset, an ``AgentTool`` or a built-in tool object. It is real,
  it has a trajectory name, and nothing about its arguments is knowable here.

:func:`resolve_tool` is the single place those three are decided, so every
caller that reports "the agent does not define this tool" reports the same set.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

from . import DEPRECATED_MODELS
from .env import classify_model_id, is_live_model_id
from .report import Finding
from .skills import (
    MAX_INSTRUCTION_LINES, MAX_INSTRUCTION_TOKENS, MIN_DESCRIPTION_CHARS, UNREADABLE,
    SkillInfo, inline_skill, load_skill_dir, load_skills_dir, repeated_share,
)

#: Constructors that build an LLM agent.
LLM_AGENT_CALLS = {"Agent", "LlmAgent"}
#: Workflow shells superseded by `google.adk.workflow.Workflow` in ADK 2.x.
DEPRECATED_AGENT_CALLS = {"SequentialAgent", "ParallelAgent", "LoopAgent"}
AGENT_CALLS = LLM_AGENT_CALLS | DEPRECATED_AGENT_CALLS | {"Workflow"}

#: Module-level names the ADK loader looks for. An assignment to one of these
#: whose right-hand side is an attribute of some object (`wrapper._agent`) hides
#: the agent behind a runtime object, so nothing about it can be read here.
EXPORT_NAMES = ("root_agent", "app", "agent")

#: Wrappers whose bound trajectory name is the *wrapped function's* name.
#: `FunctionTool(func=fn)` is reported to the model as `fn`, so an eval case
#: names `fn`, not the variable the wrapper was assigned to.
WRAPPER_TOOL_CALLS = {"FunctionTool", "LongRunningFunctionTool"}

#: Tool objects whose contract is not visible in this project's source. They are
#: real tools with real trajectory names, so they must never be reported as
#: "the agent does not define this".
OPAQUE_TOOL_CALLS = {
    "McpToolset", "VertexAiSearchTool", "PreloadMemoryTool", "LoadMemoryTool",
    "ExampleTool", "APIHubToolset", "OpenAPIToolset", "BigQueryToolset",
    "ApplicationIntegrationToolset", "LangchainTool", "CrewaiTool",
    "GoogleApiToolset", "ComputerUseToolset", "AgentTool", "VertexAiSearchTool",
    "GoogleSearchTool", "UrlContextTool", "BuiltInCodeExecutionTool",
}

#: Bare tool objects ADK exports ready-made.
OPAQUE_TOOL_NAMES = {
    "google_search", "built_in_code_execution", "url_context", "exit_loop",
    "transfer_to_agent", "load_artifacts", "load_memory", "preload_memory",
    "enterprise_web_search", "google_maps_grounding",
}

#: Parameter names and annotations ADK fills in; never part of the declaration.
IMPLICIT_SELF = ("self", "cls")

#: Annotations that mark a function as a plugin / callback hook rather than a
#: tool, which is where a direct `session.state[...]` write is legitimate.
HOOK_ANNOTATIONS = ("InvocationContext", "CallbackContext")
HOOK_PREFIXES = ("before_", "after_", "on_")

#: Kwargs `generate_content_config` must never carry: ADK owns them.
FORBIDDEN_GCC_FIELDS = {"tools", "system_instruction", "response_schema"}
#: Sampling kwargs that belong inside `generate_content_config`.
MISPLACED_MODEL_KWARGS = {
    "temperature", "top_p", "top_k", "max_output_tokens", "candidate_count",
    "stop_sequences", "seed", "safety_settings", "response_mime_type",
}

BLOCKING_CALLS = {
    "time.sleep": "time.sleep",
    "requests.get": "requests", "requests.post": "requests", "requests.put": "requests",
    "requests.delete": "requests", "requests.request": "requests",
    "urllib.request.urlopen": "urllib",
    "subprocess.run": "subprocess", "subprocess.check_output": "subprocess",
    "socket.create_connection": "socket",
}

#: `ContextCacheConfig` fields and their pydantic bounds, read off
#: `agents/context_cache_config.py` (byte-identical in 2.9.2 and 2.11.0;
#: `extra="forbid"`). `None` means no numeric bound.
CACHE_CONFIG_BOUNDS: dict[str, tuple[str, int] | tuple[str, int, int] | None] = {
    "cache_intervals": ("range", 1, 100),
    "ttl_seconds": ("gt", 0),
    "min_tokens": ("ge", 0),
    "create_http_options": None,
}
#: An instruction at least this long (about 2,000 tokens at ~4 chars a token,
#: the Gemini 2.5 cache floor) is worth caching in a multi-turn session.
LARGE_INSTRUCTION_CHARS = 8000
#: The smallest cacheable prefix Gemini accepts: 2048 tokens on Gemini 2.5,
#: 4096 on Gemini 3 (`agents/context_cache_config.py`).
CACHE_FLOOR_TOKENS = {"Gemini 2.5": 2048, "Gemini 3": 4096}
#: Rough size of one tool declaration in the request, in tokens.
TOOL_DECLARATION_TOKENS = 60
#: Characters per token, for an order-of-magnitude estimate.
CHARS_PER_TOKEN = 4
#: Wrappers that hold `static_instruction` text: `types.Content(parts=[...])`,
#: `types.Part(text=...)`, `types.Part.from_text(text=...)`.
CONTENT_WRAPPER_CALLS = {"Content", "UserContent", "ModelContent", "Part", "from_text"}
#: Module roots whose calls make text differ between runs or requests.
DYNAMIC_CALL_ROOTS = {"datetime", "date", "time", "uuid", "random", "secrets"}
#: ADK's state placeholder syntax in an instruction: `{var}`, `{var?}`,
#: `{app:var}`, `{artifact.name}`. Only `instruction` is ever filled.
PLACEHOLDER_RE = re.compile(r"\{[A-Za-z_][\w.:]*\??\}")

#: Plugins `google.adk.plugins` exports, with the release that added them.
#: Several live in private modules and are lazily re-exported, so the public
#: import path is the package, never the module.
PLUGIN_EXPORTS: dict[str, str] = {
    "BasePlugin": "2.0",
    "DebugLoggingPlugin": "2.0",
    "LoggingPlugin": "2.0",
    "ReflectAndRetryToolPlugin": "2.0",
    "ReflectAndRetryModelPlugin": "2.0",
    "ToolCallIntegrityPlugin": "2.11",
    "ToolCallIntegrityError": "2.11",
}

#: `SkillToolset` and what it is built from (`tools/skill_toolset.py`,
#: `skills/models.py`, `skills/_utils.py`; 2.9.2 and 2.12.0 alike for the
#: shapes read here, with the lifecycle and discovery enums from 2.10).
SKILL_TOOLSET_CALLS = {"SkillToolset"}
SKILL_CALLS = {"Skill"}
SKILL_DIR_LOADERS = {"load_skill_from_dir": "one", "load_skills_from_dir": "many"}
#: Loaders whose skills exist only at runtime.
SKILL_REMOTE_LOADERS = {
    "load_skill_from_gcs_dir": "gcs", "load_skills_from_gcs_dir": "gcs",
    "list_skills_in_gcs_dir": "gcs",
}
#: The tools a SkillToolset generates. `list_skills` is dropped under
#: `SkillDiscoveryMode.EAGER`; `unload_skill` exists only with
#: `ADK_ENABLE_SKILL_LIFECYCLE`; `search_skills` only with a `registry`.
SKILL_TOOL_NAMES = (
    "list_skills", "load_skill", "load_skill_resource", "run_skill_script",
    "unload_skill", "search_skills",
)
SKILL_LIFECYCLE_MODES = ("PERSISTENT", "BOUNDED", "EPHEMERAL")
#: The release that added `SkillLifecycleConfig` / `SkillLifecycleMode` /
#: `SkillDiscoveryMode` (first tag v2.10.0).
SKILL_LIFECYCLE_SINCE = "2.10"

#: `EventsCompactionConfig` fields and bounds (`apps/_configs.py`, identical in
#: 2.9.2, 2.11.0 and 2.12.0; `extra="forbid"`, every field defaults to None, and
#: a model validator wants each trigger pair set together and at least one).
COMPACTION_CONFIG_BOUNDS: dict[str, tuple[str, int] | None] = {
    "summarizer": None,
    "compaction_interval": ("gt", 0),
    "overlap_size": ("ge", 0),
    "token_threshold": ("gt", 0),
    "event_retention_size": ("ge", 0),
}
COMPACTION_PAIRS = (("compaction_interval", "overlap_size"),
                    ("token_threshold", "event_retention_size"))
#: A conversation this long is where uncompacted history starts to dominate the
#: request; a user-simulation scenario can run longer still.
LONG_CONVERSATION_INVOCATIONS = 6
#: A tool result this large in chat history is re-sent on every later request.
LARGE_TOOL_RETURN_CHARS = 2000

_SRC_SKILL_TOOLSET = "adk-python: tools/skill_toolset.py"
_SRC_SKILL_MODELS = "adk-python: skills/models.py"
_SRC_COMPACTION = "adk-python: apps/_configs.py (EventsCompactionConfig)"
_SRC_LLM_AGENT = "adk-python: agents/llm_agent.py"
_SRC_CACHE_CONFIG = "adk-python: agents/context_cache_config.py"
_SRC_CACHE_DOC = "adk-docs: docs/context/caching.md"
_SRC_BASE_AGENT = "adk-python: agents/base_agent.py"
_SRC_FUNCTION_TOOL = "adk-python: tools/function_tool.py"
_SRC_FUNCTION_DOC = "adk-docs: docs/tools-custom/function-tools.md"
_SRC_STATE_DOC = "adk-docs: docs/sessions/state.md"
_SRC_WORKFLOW = "adk-docs: docs/2.0/index.md"


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------

@dataclass
class FieldSpec:
    """One Pydantic model field and the constraints an eval case must respect."""

    name: str
    annotation: str | None = None
    required: bool = True
    literal_values: list[Any] = field(default_factory=list)
    pattern: str | None = None
    ge: float | None = None
    le: float | None = None
    gt: float | None = None
    lt: float | None = None
    min_length: int | None = None
    max_length: int | None = None
    default: Any = None
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, [], False) or k in ("name", "required")}


@dataclass
class ModelSpec:
    name: str
    fields: list[FieldSpec] = field(default_factory=list)
    base: str | None = None
    lineno: int = 0

    def field_map(self) -> dict[str, FieldSpec]:
        return {f.name: f for f in self.fields}

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "base": self.base,
            "fields": [f.to_dict() for f in self.fields],
        }


@dataclass
class ParamSpec:
    name: str
    annotation: str | None = None
    has_default: bool = False
    model: str | None = None          # a Pydantic model used as the parameter type
    injected: bool = False            # ToolContext / CallbackContext, not part of the schema

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, False)} | {"name": self.name}


@dataclass
class ToolSpec:
    name: str
    params: list[ParamSpec] = field(default_factory=list)
    docstring: str | None = None
    returns: str | None = None
    is_async: bool = False
    lineno: int = 0
    raises: list[str] = field(default_factory=list)
    returns_dict: bool = False
    dict_keys: list[str] = field(default_factory=list)
    blocking_calls: list[str] = field(default_factory=list)
    file: str | None = None
    resolved: bool = True
    #: `*args` / `**kwargs`: the declaration is open, so an argument this
    #: extractor cannot see is not necessarily an argument the tool rejects.
    has_var_args: bool = False
    has_var_kwargs: bool = False
    decorators: list[str] = field(default_factory=list)
    #: The longest string literal this tool returns (directly, or as a value of
    #: a returned dict), in characters.
    large_return_chars: int = 0
    #: Names returned bare (`return POLICY_TEXT`), resolved against module
    #: string constants once every file is read.
    returned_names: list[str] = field(default_factory=list)
    #: `async def` with `yield` in its own body (or annotated `AsyncGenerator` /
    #: `AsyncIterator`): a streaming tool, NON_BLOCKING only under `run_live`.
    is_streaming: bool = False
    #: `yield` statements in the body, counted statically (a loop counts once).
    yield_count: int = 0

    @property
    def schema_params(self) -> list[ParamSpec]:
        return [p for p in self.params if not p.injected]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "params": [p.to_dict() for p in self.params],
            "has_docstring": bool(self.docstring),
            "returns": self.returns,
            "is_async": self.is_async,
            "returns_dict": self.returns_dict,
            "dict_keys": self.dict_keys,
            "raises": self.raises,
            "blocking_calls": self.blocking_calls,
            "resolved": self.resolved,
            "has_var_args": self.has_var_args,
            "has_var_kwargs": self.has_var_kwargs,
            "large_return_chars": self.large_return_chars,
            "is_streaming": self.is_streaming,
        }


@dataclass
class AgentSpec:
    var: str | None
    call: str
    name: str | None = None
    model: str | None = None
    model_expr: str | None = None
    model_from_env: bool = False
    instruction_len: int = 0
    instruction_text: str | None = None
    has_instruction: bool = False
    #: True when `instruction=` is a callable (an `InstructionProvider`) rather
    #: than text: the specification is built at runtime and cannot be read here.
    instruction_is_dynamic: bool = False
    #: `static_instruction=` is set. ADK sends it verbatim as the system
    #: instruction (no placeholder filling) and it is the cacheable prefix.
    has_static_instruction: bool = False
    static_instruction_len: int = 0
    static_instruction_text: str | None = None
    #: Why the static instruction is not byte-identical from run to run
    #: (f-string, `.format`, `%`, a clock or random call, a `{var}`
    #: placeholder, an InstructionProvider). Empty when it is a plain literal.
    static_instruction_dynamic: list[str] = field(default_factory=list)
    has_global_instruction: bool = False
    tools: list[str] = field(default_factory=list)
    #: Toolsets, `AgentTool`s and built-in tool objects bound to this agent.
    opaque_tools: list[str] = field(default_factory=list)
    #: True when `tools=` is an expression this extractor cannot read, e.g.
    #: `tools=build_tools()`. Absence of a name then proves nothing.
    tools_unresolved: bool = False
    #: True for `Agent(**kwargs)`: every kwarg is decided at runtime, so a
    #: missing `name` or `instruction` here is unknowable rather than absent.
    kwargs_unresolved: bool = False
    sub_agents: list[str] = field(default_factory=list)
    output_schema: str | None = None
    input_schema: str | None = None
    output_key: str | None = None
    callbacks: list[str] = field(default_factory=list)
    kwargs: list[str] = field(default_factory=list)
    gcc_forbidden: list[str] = field(default_factory=list)
    #: `include_contents=` as written: "default", "none", the source text of
    #: an expression, or None when unset (ADK's default is "default").
    include_contents: str | None = None
    #: `model=CascadeLive(...)` (google.adk.live, 2.13): runs under `run_live`
    #: although the inner model id is a text model.
    model_is_cascade: bool = False
    #: A module string constant `model=` names, kept apart from `model` so the
    #: model-policy rules keep their meaning; used only to tell live from text.
    model_constant: str | None = None
    lineno: int = 0
    file: str | None = None

    @property
    def spec_text(self) -> str | None:
        """The text the agent's rules are written in: the static instruction
        (the cached system prefix) followed by the instruction, when readable."""
        parts = [t for t in (self.static_instruction_text, self.instruction_text) if t]
        return "\n\n".join(parts) if parts else None

    @property
    def cache_prefix_chars(self) -> int:
        """Characters of instruction in the cacheable prefix: the static
        instruction when there is one, else the instruction."""
        return self.static_instruction_len if self.has_static_instruction else self.instruction_len

    @property
    def cache_prefix_tokens_estimate(self) -> int:
        """Rough tokens in the prefix a context cache would store: instruction
        characters / 4, plus ~60 tokens per bound tool declaration."""
        tools = len(self.tools) + len(self.opaque_tools)
        return self.cache_prefix_chars // CHARS_PER_TOKEN + TOOL_DECLARATION_TOKENS * tools

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("instruction_text", None)
        data.pop("static_instruction_text", None)
        data["cache_prefix_tokens_estimate"] = self.cache_prefix_tokens_estimate
        return data


@dataclass
class AppSpec:
    var: str
    name: str | None = None
    plugins: list[str] = field(default_factory=list)
    root_agent: str | None = None
    kwargs: list[str] = field(default_factory=list)
    #: `context_cache_config=` is set to something other than `None`.
    context_cache_set: bool = False
    #: The `ContextCacheConfig(...)` kwargs, literal values where readable and
    #: the source text otherwise. None when unset or not a readable call.
    context_cache_config: dict[str, Any] | None = None
    #: Source text when the config is set but not a readable constructor call.
    context_cache_expr: str | None = None
    #: `events_compaction_config=` is set to something other than `None`.
    events_compaction_set: bool = False
    #: The `EventsCompactionConfig(...)` kwargs, as for the cache config.
    events_compaction_config: dict[str, Any] | None = None
    events_compaction_expr: str | None = None
    lineno: int = 0
    file: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SkillToolsetSpec:
    """One `SkillToolset(...)` and the skills it registers."""

    var: str | None
    file: str | None = None
    lineno: int = 0
    skills: list[SkillInfo] = field(default_factory=list)
    #: True when the registered set cannot be read: a registry, GCS, or an
    #: expression built at runtime. EVS074 / EVS073 then stay silent.
    skills_unresolved: bool = False
    unresolved_reason: str | None = None
    registry: bool = False
    #: "LAZY", "EAGER", the source text of an expression, or None (LAZY).
    discovery_mode: str | None = None
    #: `SkillLifecycleConfig(...)` kwargs: literals, mode names ("BOUNDED"),
    #: `{skill: mode}` for `skill_overrides`, source text otherwise.
    lifecycle_config: dict[str, Any] | None = None
    lifecycle_expr: str | None = None
    tool_name_prefix: str | None = None
    kwargs: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        """The name this toolset carries in an agent's `opaque_tools`."""
        return self.var or "SkillToolset"

    @property
    def tool_names(self) -> list[str]:
        """The tools this toolset can put in a trajectory, prefixed as ADK does."""
        names = [n for n in SKILL_TOOL_NAMES
                 if not (n == "list_skills" and self.discovery_mode == "EAGER")
                 and not (n == "search_skills" and not self.registry)]
        if self.tool_name_prefix:
            names = [f"{self.tool_name_prefix}_{n}" for n in names]
        return names

    def to_dict(self) -> dict[str, Any]:
        return {
            "var": self.var,
            "file": self.file,
            "lineno": self.lineno,
            "skills": [skill.to_dict() for skill in self.skills],
            "skills_unresolved": self.skills_unresolved,
            "unresolved_reason": self.unresolved_reason,
            "registry": self.registry,
            "discovery_mode": self.discovery_mode,
            "lifecycle_config": self.lifecycle_config,
            "lifecycle_expr": self.lifecycle_expr,
            "tool_name_prefix": self.tool_name_prefix,
            "tools": self.tool_names,
        }


@dataclass
class StateWrite:
    """One direct `session.state[...] = ...`, and where it sits."""

    path: str
    lineno: int
    text: str
    enclosing: str | None = None
    is_hook: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolBinding:
    """What a trajectory name actually refers to in this project."""

    name: str
    #: defined | opaque | defined_not_bound | unresolved | unknown
    status: str
    spec: ToolSpec | None = None
    reason: str | None = None

    @property
    def is_real(self) -> bool:
        """Could a run legitimately emit this name?"""
        return self.status in ("defined", "opaque", "unresolved")

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "reason": self.reason}


@dataclass
class ModuleFacts:
    """Everything statically knowable about one agent project."""

    files: list[str] = field(default_factory=list)
    tools: dict[str, ToolSpec] = field(default_factory=dict)
    models: dict[str, ModelSpec] = field(default_factory=dict)
    agents: list[AgentSpec] = field(default_factory=list)
    apps: list[AppSpec] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    imports: set[str] = field(default_factory=set)
    #: Module-level string constants, so `instruction=INSTRUCTION` and
    #: `instruction=prompt.SYSTEM` resolve to their text.
    str_constants: dict[str, str] = field(default_factory=dict)
    #: Module-level constants whose value comes from the environment, e.g.
    #: `MODEL = os.getenv("MODEL_NAME", "gemini-flash-latest")`, mapped to their
    #: fallback literal when there is one. `model=MODEL` is then env-driven.
    env_constants: dict[str, str | None] = field(default_factory=dict)
    #: Every module-level `NAME = <expr>`, so `static_instruction=STATIC` and
    #: `context_cache_config=CACHE` can be followed to what they hold.
    expr_constants: dict[str, ast.expr] = field(default_factory=dict)
    #: `from google.adk.plugins._private import X` style imports of a plugin
    #: the package re-exports: (file, line, module, name).
    private_plugin_imports: list[tuple[str, int, str, str]] = field(default_factory=list)
    #: Module-level `TOOLS = [...]`, so `tools=TOOLS` and `tools=[*TOOLS]`
    #: resolve. Values are the raw element expressions.
    list_constants: dict[str, list[Any]] = field(default_factory=dict)
    #: `CHECK = FunctionTool(func=check_condition)` -> {"CHECK": "check_condition"}.
    #: The trajectory carries the wrapped function's name, not the variable's.
    tool_aliases: dict[str, str] = field(default_factory=dict)
    #: `SKILLS = McpToolset(...)` -> {"SKILLS": "toolset"}.
    opaque_constants: dict[str, str] = field(default_factory=dict)
    #: Trajectory name -> why nothing about its contract is knowable here.
    opaque_tools: dict[str, str] = field(default_factory=dict)
    #: `AgentTool(agent=x)` bindings still to be resolved to the agent's `name`.
    _agent_tool_refs: list[str] = field(default_factory=list)
    #: Every definition seen for a name, before collision resolution.
    tool_candidates: dict[str, list[ToolSpec]] = field(default_factory=dict)
    #: file -> the `from module import name` pairs it declares, as "module.name".
    file_imports: dict[str, set[str]] = field(default_factory=dict)
    #: A name defined in more than one module, with the files it came from.
    tool_collisions: dict[str, list[str]] = field(default_factory=dict)
    #: Every module-level function name, so `instruction=build_prompt` can be
    #: told apart from `instruction=PROMPT`.
    callables: set[str] = field(default_factory=set)
    state_writes: list[StateWrite] = field(default_factory=list)
    syntax_errors: list[str] = field(default_factory=list)
    #: Export name -> the expression it was assigned, for exports that resolve to
    #: an object's attribute rather than to an `Agent(...)`/`App(...)` call, e.g.
    #: ``{"root_agent": "coordinator_wrapper._agent"}``.
    unresolved_exports: dict[str, str] = field(default_factory=dict)
    skill_toolsets: list[SkillToolsetSpec] = field(default_factory=list)
    #: `NAME.append(x)` / `NAME.extend(xs)` anywhere in a file, so a skills
    #: list filled in a loop (`skills.append(load_skill_from_gcs_dir(...))`)
    #: is seen to be filled.
    list_mutations: dict[str, list[ast.expr]] = field(default_factory=dict)

    @property
    def skills(self) -> list[SkillInfo]:
        return [skill for toolset in self.skill_toolsets for skill in toolset.skills]

    @property
    def registered_skill_names(self) -> set[str] | None:
        """Every skill name the agent's toolsets register, or None when there is
        no SkillToolset at all (then `load_skill` is not a tool, which EVS036
        reports) or the set is unknowable (a registry, GCS, or a list built at
        run time)."""
        if not self.skill_toolsets:
            return None
        if any(toolset.skills_unresolved for toolset in self.skill_toolsets):
            return None
        return {skill.name for skill in self.skills if skill.name}

    @property
    def app(self) -> AppSpec | None:
        """The App a loader would find.

        A project may build several (a factory plus a test double); the loader
        imports `agent.py` or the package `__init__.py`, so prefer one declared
        there before falling back to the first seen.
        """
        for app in self.apps:
            if Path(app.file or "").name in ("agent.py", "__init__.py"):
                return app
        return self.apps[0] if self.apps else None

    @property
    def agent_unresolved_reason(self) -> str | None:
        """Why the exported agent cannot be read from the source, if it cannot.

        Two shapes defeat static reading, and both are in real projects:

        * ``Agent(**kwargs)`` - the kwargs dict is assembled at runtime, often
          conditionally, so `name`, `model`, `instruction` and `tools` are all
          unknowable rather than absent;
        * ``root_agent = wrapper._agent`` - the export is an attribute of an
          object built at import time, so there is no constructor call to read.

        Returning a reason is what lets every check that depends on those fields
        say it was skipped instead of quietly reporting nothing.
        """
        reasons: list[str] = []
        agent = self.root_agent()
        if agent is not None and agent.kwargs_unresolved:
            reasons.append(
                f"`{agent.call}(**kwargs)` at {Path(agent.file or '?').name}:{agent.lineno} "
                "builds every field at runtime"
            )
        for var, expr in sorted(self.unresolved_exports.items()):
            reasons.append(
                f"`{var} = {expr}` exports an attribute of an object, not an "
                f"{'App' if var == 'app' else 'Agent'}(...) call"
            )
        return "; ".join(reasons) or None

    @property
    def agent_unresolved(self) -> bool:
        """True when the exported agent's construction is not statically readable."""
        return self.agent_unresolved_reason is not None

    @property
    def tools_unresolved(self) -> bool:
        """Did any agent bind tools through an expression we could not read?

        An unreadable agent construction counts: a tool list that is never
        written down cannot be enumerated, so the absence of a name proves
        nothing about the run.
        """
        return any(agent.tools_unresolved for agent in self.agents) or self.agent_unresolved

    @property
    def bound_tool_names(self) -> set[str]:
        """Function-like tool names passed to an agent.

        Deliberately excludes opaque tools: this set drives tool *coverage* and
        the AGT04x contract review, both of which only make sense for a tool
        whose source is here.
        """
        names: set[str] = set()
        for agent in self.agents:
            names.update(agent.tools)
        return names

    @property
    def opaque_tool_names(self) -> set[str]:
        return set(self.opaque_tools)

    @property
    def trajectory_tool_names(self) -> set[str]:
        """Every name a real run could emit: bound functions plus opaque tools."""
        return self.bound_tool_names | self.opaque_tool_names

    @property
    def tool_names(self) -> set[str]:
        return set(self.bound_tool_names) | set(self.tools)

    def root_agent(self) -> AgentSpec | None:
        for agent in self.agents:
            if agent.var == "root_agent":
                return agent
        return self.agents[0] if self.agents else None

    def resolve(self, name: str) -> ToolBinding:
        return resolve_tool(self, name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "files": self.files,
            "tools": {name: spec.to_dict() for name, spec in self.tools.items()},
            "models": {name: spec.to_dict() for name, spec in self.models.items()},
            "agents": [agent.to_dict() for agent in self.agents],
            "app": self.app.to_dict() if self.app else None,
            "bound_tools": sorted(self.bound_tool_names),
            "opaque_tools": dict(sorted(self.opaque_tools.items())),
            "tools_unresolved": self.tools_unresolved,
            "agent_unresolved": self.agent_unresolved,
            "agent_unresolved_reason": self.agent_unresolved_reason,
            "unresolved_exports": dict(sorted(self.unresolved_exports.items())),
            "tool_collisions": self.tool_collisions,
            "state_writes": [w.to_dict() for w in self.state_writes],
            "syntax_errors": self.syntax_errors,
            "skill_toolsets": [t.to_dict() for t in self.skill_toolsets],
            "skills": [skill.to_dict() for skill in self.skills],
        }


# ---------------------------------------------------------------------------
# small AST helpers
# ---------------------------------------------------------------------------

def _literal(node: ast.AST | None) -> Any:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def _name_of(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _name_of(node.func)
    return None


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return ""


def _unparse(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return None


def _kwarg(call: ast.Call, name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _list_names(node: ast.AST | None) -> list[str]:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return []
    names: list[str] = []
    for element in node.elts:
        name = _name_of(element)
        if name:
            names.append(name)
    return names


def _is_opaque_call(call_name: str | None) -> bool:
    """Is this constructor a tool object whose contract lives outside the source?"""
    if not call_name:
        return False
    if call_name in WRAPPER_TOOL_CALLS:
        return False
    # `build_skill_toolset(...)` is as much a toolset factory as `McpToolset`:
    # both hand back an object whose tool names are decided at runtime.
    return call_name in OPAQUE_TOOL_CALLS or call_name.lower().endswith("toolset")


def _wrapped_function(call: ast.Call) -> str | None:
    """The function inside `FunctionTool(fn)` / `FunctionTool(func=fn)`."""
    inner = _kwarg(call, "func")
    if inner is None and call.args:
        inner = call.args[0]
    return _name_of(inner)


def _opaque_label(call: ast.Call, call_name: str, fallback: str | None = None) -> str:
    """The trajectory-visible name of an opaque tool object, best effort."""
    literal = _literal(_kwarg(call, "name"))
    if isinstance(literal, str) and literal:
        return literal
    if call_name == "AgentTool":
        agent = _kwarg(call, "agent") or (call.args[0] if call.args else None)
        agent_name = _name_of(agent)
        if agent_name:
            return agent_name
    return fallback or call_name


@dataclass
class Binding:
    """The result of reading one `tools=` expression."""

    bound: list[str] = field(default_factory=list)
    opaque: list[tuple[str, str]] = field(default_factory=list)
    #: False when part of the expression could not be read at all.
    resolved: bool = True


def _binding_names(node: ast.AST | None, facts: ModuleFacts, _depth: int = 0) -> Binding:
    """Read an agent's `tools=` expression into trajectory names.

    Handles the shapes real agents use: a literal list, a module-level constant,
    `[*BASE, extra]`, `FunctionTool(func=fn)` and its module-level alias,
    `AgentTool(agent=x)`, toolsets, and ADK's ready-made tool objects. Anything
    else - `tools=build_tools()`, a comprehension - leaves `resolved` False,
    which is the difference between "this agent has no such tool" and "we could
    not tell".
    """
    result = Binding()
    if node is None or _depth > 6:
        return result

    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        for element in node.elts:
            inner = _binding_names(element, facts, _depth + 1)
            result.bound.extend(inner.bound)
            result.opaque.extend(inner.opaque)
            result.resolved = result.resolved and inner.resolved
        return result

    if isinstance(node, ast.Starred):
        return _binding_names(node.value, facts, _depth + 1)

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _binding_names(node.left, facts, _depth + 1)
        right = _binding_names(node.right, facts, _depth + 1)
        return Binding(
            bound=left.bound + right.bound,
            opaque=left.opaque + right.opaque,
            resolved=left.resolved and right.resolved,
        )

    if isinstance(node, (ast.Name, ast.Attribute)):
        name = _name_of(node)
        if not name:
            return Binding(resolved=False)
        if name in facts.tool_aliases:
            return Binding(bound=[facts.tool_aliases[name]])
        if name in facts.opaque_constants:
            return Binding(opaque=[(name, facts.opaque_constants[name])])
        if name in OPAQUE_TOOL_NAMES:
            return Binding(opaque=[(name, "builtin")])
        if name in facts.list_constants:
            inner = Binding()
            for element in facts.list_constants[name]:
                part = _binding_names(element, facts, _depth + 1)
                inner.bound.extend(part.bound)
                inner.opaque.extend(part.opaque)
                inner.resolved = inner.resolved and part.resolved
            return inner
        return Binding(bound=[name])

    if isinstance(node, ast.Call):
        call_name = _name_of(node.func)
        if call_name in WRAPPER_TOOL_CALLS:
            wrapped = _wrapped_function(node)
            return Binding(bound=[wrapped]) if wrapped else Binding(resolved=False)
        if call_name == "AgentTool":
            label = _opaque_label(node, call_name)
            # The agent's declared `name=` may live in a file not parsed yet, so
            # record the variable and swap it for the name in a later pass.
            facts._agent_tool_refs.append(label)
            return Binding(opaque=[(label, "agent_tool")])
        if _is_opaque_call(call_name):
            return Binding(opaque=[(_opaque_label(node, call_name or ""), "toolset")])
        return Binding(resolved=False)

    return Binding(resolved=False)


def _string_value(node: ast.AST | None, constants: dict[str, str] | None = None) -> str | None:
    """Best-effort text of a string expression.

    Handles literals, f-strings, concatenation, `join`/`dedent`, and references
    to module-level string constants (including `prompt.SYSTEM` style
    attributes), which is how most real agents hold their instruction.
    """
    constants = constants or {}
    literal = _literal(node)
    if isinstance(literal, str):
        return literal
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.Attribute):
        return constants.get(node.attr)
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value for part in node.values if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _string_value(node.left, constants) or ""
        right = _string_value(node.right, constants) or ""
        return left + right
    if isinstance(node, ast.Call):
        # "".join([...]) and textwrap.dedent("""...""")
        if isinstance(node.func, ast.Attribute) and node.func.attr in ("join", "dedent", "format"):
            parts = [_string_value(arg, constants) for arg in node.args]
            if isinstance(node.func.value, (ast.List, ast.Tuple)):
                parts = [_string_value(e, constants) for e in node.func.value.elts]
            return "".join(p for p in parts if p)
    return None


def _env_fallback(node: ast.AST | None) -> str | None:
    """The default in `os.getenv("X", "default")`, when there is one."""
    if isinstance(node, ast.Call) and _name_of(node) in ("getenv", "get"):
        if len(node.args) >= 2:
            fallback = _literal(node.args[1])
            return fallback if isinstance(fallback, str) else None
    return None


def _env_lookup(node: ast.AST | None) -> bool:
    """Is this expression reading the model id from the environment?"""
    if node is None:
        return False
    text = _unparse(node) or ""
    return "environ" in text or "getenv" in text


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------

def _is_pydantic_model(node: ast.ClassDef) -> str | None:
    for base in node.bases:
        name = _name_of(base)
        if name in ("BaseModel", "RootModel"):
            return name
    return None


def _field_spec(name: str, annotation: ast.expr | None, value: ast.expr | None) -> FieldSpec:
    spec = FieldSpec(name=name, annotation=_unparse(annotation))

    if annotation is not None:
        text = spec.annotation or ""
        if isinstance(annotation, ast.Subscript) and _name_of(annotation.value) == "Literal":
            spec.literal_values = [
                _literal(element)
                for element in (
                    annotation.slice.elts if isinstance(annotation.slice, ast.Tuple) else [annotation.slice]
                )
            ]
        elif "Literal[" in text:
            inner = text[text.index("Literal[") + 8:]
            inner = inner.rsplit("]", 1)[0]
            try:
                spec.literal_values = list(ast.literal_eval(f"[{inner}]"))
            except Exception:
                pass
        if "Optional" in text or text.endswith("| None") or "None |" in text:
            spec.required = False

    if value is None:
        return spec

    if isinstance(value, ast.Call) and _name_of(value) == "Field":
        positional_default = value.args[0] if value.args else None
        if positional_default is not None:
            default = _literal(positional_default)
            is_ellipsis = isinstance(positional_default, ast.Constant) and positional_default.value is Ellipsis
            if not is_ellipsis:
                spec.required = False
                spec.default = default
        for keyword in value.keywords:
            target = keyword.arg
            if target == "pattern" or target == "regex":
                spec.pattern = _literal(keyword.value)
            elif target in ("ge", "le", "gt", "lt", "min_length", "max_length"):
                setattr(spec, target, _literal(keyword.value))
            elif target == "description":
                spec.description = _literal(keyword.value)
            elif target == "default":
                spec.required = False
                spec.default = _literal(keyword.value)
            elif target == "default_factory":
                spec.required = False
    else:
        spec.required = False
        spec.default = _literal(value)
    return spec


def _collect_model(node: ast.ClassDef, base: str) -> ModelSpec:
    spec = ModelSpec(name=node.name, base=base, lineno=node.lineno)
    for item in node.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            spec.fields.append(_field_spec(item.target.id, item.annotation, item.value))
        elif isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("model_config"):
                    spec.fields.append(_field_spec(target.id, None, item.value))
    return spec


INJECTED_ANNOTATIONS = {"ToolContext", "CallbackContext", "Context", "InputStream"}


def _is_hook(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Is this a plugin or callback hook rather than ordinary agent code?

    ADK hands a hook the invocation context precisely so it can read and write
    session state, so a direct `session.state[...]` write inside one is the
    documented shape, not the mistake AGT013 is looking for.
    """
    if node.name.startswith(HOOK_PREFIXES):
        return True
    for arg in [*node.args.args, *node.args.kwonlyargs, *node.args.posonlyargs]:
        annotation = _unparse(arg.annotation) or ""
        if any(marker in annotation for marker in HOOK_ANNOTATIONS):
            return True
    return False


def _collect_tool(node: ast.FunctionDef | ast.AsyncFunctionDef, models: dict[str, ModelSpec]) -> ToolSpec:
    spec = ToolSpec(
        name=node.name,
        docstring=ast.get_docstring(node),
        returns=_unparse(node.returns),
        is_async=isinstance(node, ast.AsyncFunctionDef),
        lineno=node.lineno,
    )

    args = node.args
    spec.has_var_args = args.vararg is not None
    spec.has_var_kwargs = args.kwarg is not None
    defaults_start = len(args.args) - len(args.defaults)
    for index, arg in enumerate(args.args):
        # A bound method reaches here only when a module-level function shares
        # its name; `self` is never part of the declaration ADK builds.
        if index == 0 and arg.arg in IMPLICIT_SELF:
            continue
        annotation = _unparse(arg.annotation)
        param = ParamSpec(
            name=arg.arg,
            annotation=annotation,
            has_default=index >= defaults_start,
        )
        base_annotation = (annotation or "").replace("Optional[", "").rstrip("]")
        if base_annotation in INJECTED_ANNOTATIONS or arg.arg in ("tool_context", "callback_context"):
            param.injected = True
        if base_annotation in models:
            param.model = base_annotation
        spec.params.append(param)
    for arg in args.kwonlyargs:
        annotation = _unparse(arg.annotation)
        param = ParamSpec(name=arg.arg, annotation=annotation, has_default=True)
        if (annotation or "") in INJECTED_ANNOTATIONS or arg.arg in ("tool_context", "callback_context"):
            param.injected = True
        spec.params.append(param)

    for child in ast.walk(node):
        if isinstance(child, ast.Raise):
            exc = _name_of(child.exc) if child.exc is not None else "re-raise"
            spec.raises.append(exc or "raise")
        elif isinstance(child, ast.Return):
            returned = [child.value] if child.value is not None else []
            if isinstance(child.value, ast.Dict):
                returned.extend(v for v in child.value.values if v is not None)
            for value in returned:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    spec.large_return_chars = max(spec.large_return_chars, len(value.value))
                elif isinstance(value, ast.JoinedStr):
                    size = sum(len(p.value) for p in value.values
                               if isinstance(p, ast.Constant) and isinstance(p.value, str))
                    spec.large_return_chars = max(spec.large_return_chars, size)
                elif isinstance(value, ast.BinOp) and isinstance(value.op, ast.Mult):
                    text, count = _literal(value.left), _literal(value.right)
                    if isinstance(count, str):
                        text, count = count, text
                    if isinstance(text, str) and isinstance(count, int) and not isinstance(count, bool):
                        spec.large_return_chars = max(spec.large_return_chars, len(text) * count)
                elif isinstance(value, ast.Name) and value.id not in spec.returned_names:
                    spec.returned_names.append(value.id)
            if isinstance(child.value, ast.Dict):
                spec.returns_dict = True
                for key in child.value.keys:
                    literal = _literal(key)
                    if isinstance(literal, str) and literal not in spec.dict_keys:
                        spec.dict_keys.append(literal)
        elif isinstance(child, ast.Call):
            dotted = _dotted(child.func)
            if dotted in BLOCKING_CALLS and not spec.is_async:
                spec.blocking_calls.append(dotted)
    if (spec.returns or "").startswith(("dict", "Dict", "typing.Dict")):
        spec.returns_dict = True
    if spec.is_async:
        spec.yield_count = _own_yields(node)
        annotated = re.search(r"\bAsync(?:Generator|Iterator)\b", spec.returns or "")
        spec.is_streaming = spec.yield_count > 0 or bool(annotated)
    return spec


def _own_yields(node: ast.AST) -> int:
    """`yield` expressions in a function's own body, not in nested functions."""
    count = 0
    stack = list(ast.iter_child_nodes(node))
    while stack:
        child = stack.pop()
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(child, (ast.Yield, ast.YieldFrom)):
            count += 1
        stack.extend(ast.iter_child_nodes(child))
    return count


def _resolve_model(
    node: ast.expr | None, env_constants: dict[str, str | None]
) -> tuple[str | None, bool]:
    """(model id, came from the environment) for a `model=` expression.

    Handles a literal, `os.getenv("MODEL_NAME", "default")` inline, a wrapper
    such as `Gemini(model=...)`, and a module-level constant holding either.
    """
    if node is None:
        return None, False

    if isinstance(node, ast.Name):
        if node.id in env_constants:
            return env_constants[node.id], True
        return None, False

    literal = _literal(node)
    if isinstance(literal, str):
        return literal, False

    if isinstance(node, ast.Call):
        if _name_of(node) in ("getenv", "get") and _env_lookup(node):
            # The useful id is the fallback, not the variable's name.
            return _env_fallback(node), True
        inner = _kwarg(node, "model")
        if inner is None and node.args:
            inner = node.args[0]
        return _resolve_model(inner, env_constants)

    return None, _env_lookup(node)


def _is_instruction_provider(node: ast.expr, facts: ModuleFacts | None) -> bool:
    """Does `instruction=` point at a callable rather than at text?

    ADK accepts an `InstructionProvider` - a callable given the context and
    returning the instruction for that turn. The specification then exists only
    at runtime, so coverage cannot be computed from it.
    """
    if isinstance(node, ast.Lambda):
        return True
    if isinstance(node, ast.Call):
        return False                      # a call that builds text, e.g. dedent(...)
    name = _name_of(node)
    if not name or facts is None:
        return False
    if name in facts.str_constants or name in facts.env_constants:
        return False
    return name in facts.callables


def _dynamic_call(node: ast.AST) -> str | None:
    """`datetime.now()`, `time.time()`, `uuid.uuid4()`, `random.choice(...)`:
    a call whose result differs on every run, by dotted name."""
    if not isinstance(node, ast.Call):
        return None
    dotted = _dotted(node.func)
    if not dotted:
        return None
    parts = dotted.split(".")
    if parts[0] in DYNAMIC_CALL_ROOTS and len(parts) > 1:
        return dotted
    # `from datetime import datetime; datetime.now()` is covered above;
    # `from time import time; time()` and `from uuid import uuid4; uuid4()`:
    if len(parts) == 1 and parts[0] in ("uuid4", "uuid1", "now", "today", "utcnow"):
        return dotted
    return None


def scan_static_instruction(
    node: ast.AST | None, facts: ModuleFacts | None = None,
) -> tuple[str, list[str]]:
    """(readable text, reasons it is not byte-identical) for `static_instruction=`.

    `static_instruction` is the cacheable prefix: ADK sends it verbatim as the
    system instruction, never fills `{var}` placeholders in it, and the context
    cache fingerprints it. So anything that varies - an f-string, `.format`,
    `%`, a clock or random call, a callable - breaks the cache on every request
    and, for placeholders, reaches the model unfilled.

    Follows the shapes real agents use: a literal, a module constant (`Name`
    or `module.NAME`), `types.Content(parts=[types.Part(text=...)])`,
    `types.Part.from_text(text=...)`, lists of parts, concatenation and
    `textwrap.dedent`.
    """
    texts: list[str] = []
    reasons: list[str] = []
    expr_constants = facts.expr_constants if facts else {}
    str_constants = facts.str_constants if facts else {}
    callables = facts.callables if facts else set()

    def note(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    def scan_calls(n: ast.AST) -> None:
        for child in ast.walk(n):
            dotted = _dynamic_call(child)
            if dotted:
                note(f"a call to `{dotted}()`")

    def visit(n: ast.AST | None, depth: int, seen: frozenset[str]) -> None:
        if n is None or depth > 10:
            return
        if isinstance(n, ast.Constant):
            if isinstance(n.value, str):
                texts.append(n.value)
                match = PLACEHOLDER_RE.search(n.value)
                if match:
                    note(f"a `{match.group(0)}` placeholder (never filled in a static instruction)")
            return
        if isinstance(n, ast.JoinedStr):
            note("an f-string")
            for part in n.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    texts.append(part.value)
            scan_calls(n)
            return
        if isinstance(n, ast.Lambda):
            note("an InstructionProvider (lambda)")
            return
        if isinstance(n, (ast.Name, ast.Attribute)):
            name = _name_of(n)
            if not name or name in seen:
                return
            if name in expr_constants:
                visit(expr_constants[name], depth + 1, seen | {name})
            elif name in callables:
                note(f"an InstructionProvider callable `{name}`")
            elif name in str_constants:
                texts.append(str_constants[name])
            return
        if isinstance(n, (ast.List, ast.Tuple)):
            for element in n.elts:
                visit(element, depth + 1, seen)
            return
        if isinstance(n, ast.BinOp):
            if isinstance(n.op, ast.Mod):
                note("`%` formatting")
                visit(n.left, depth + 1, seen)
                scan_calls(n.right)
            elif isinstance(n.op, ast.Add):
                visit(n.left, depth + 1, seen)
                visit(n.right, depth + 1, seen)
            else:
                scan_calls(n)
            return
        if isinstance(n, ast.Call):
            dotted = _dynamic_call(n)
            if dotted:
                note(f"a call to `{dotted}()`")
                return
            func_name = _name_of(n.func)
            if isinstance(n.func, ast.Attribute) and n.func.attr == "format":
                note("`.format()`")
                visit(n.func.value, depth + 1, seen)
                scan_calls(n)
                return
            if func_name in CONTENT_WRAPPER_CALLS:
                for keyword in n.keywords:
                    if keyword.arg in ("parts", "text"):
                        visit(keyword.value, depth + 1, seen)
                for arg in n.args:
                    visit(arg, depth + 1, seen)
                return
            if func_name in ("dedent", "strip", "cleandoc"):
                for arg in n.args:
                    visit(arg, depth + 1, seen)
                if isinstance(n.func, ast.Attribute) and func_name == "strip":
                    visit(n.func.value, depth + 1, seen)
                return
            if func_name == "join":
                for arg in n.args:
                    visit(arg, depth + 1, seen)
                return
            # Anything else (a helper building the text at import time) is
            # unreadable; if it reads the clock inline, the scan says so.
            scan_calls(n)
            return
        scan_calls(n)

    visit(node, 0, frozenset())
    return "".join(texts), reasons


def _collect_agent(
    var: str | None, call: ast.Call, call_name: str,
    constants: dict[str, str] | None = None,
    env_constants: dict[str, str | None] | None = None,
    facts: ModuleFacts | None = None,
) -> AgentSpec:
    spec = AgentSpec(var=var, call=call_name, lineno=call.lineno)
    spec.kwargs = [kw.arg for kw in call.keywords if kw.arg]
    spec.kwargs_unresolved = any(kw.arg is None for kw in call.keywords)
    name_node = _kwarg(call, "name")
    spec.name = _literal(name_node)
    if spec.name is None and name_node is not None:
        # `name=constants.AGENT_NAME` is a name, not a missing one.
        spec.name = _string_value(name_node, constants)

    model_node = _kwarg(call, "model")
    spec.model_expr = _unparse(model_node)
    spec.model, spec.model_from_env = _resolve_model(model_node, env_constants or {})
    target = model_node
    if isinstance(target, ast.Name) and facts is not None:
        target = facts.expr_constants.get(target.id, target)
        if spec.model is None and target is not model_node:
            literal = _literal(target)
            if isinstance(literal, str):
                spec.model_constant = literal      # `MODEL = "gemini-3.8-live"`
    spec.model_is_cascade = (isinstance(target, ast.Call)
                             and _name_of(target.func) == "CascadeLive")

    instruction_node = _kwarg(call, "instruction")
    if instruction_node is not None:
        spec.has_instruction = True
        text = _string_value(instruction_node, constants)
        if text:
            spec.instruction_text = text
            spec.instruction_len = len(text)
        elif _is_instruction_provider(instruction_node, facts):
            spec.instruction_is_dynamic = True

    static_node = _kwarg(call, "static_instruction")
    if static_node is not None and not (
        isinstance(static_node, ast.Constant) and static_node.value is None
    ):
        spec.has_static_instruction = True
        text, reasons = scan_static_instruction(static_node, facts)
        spec.static_instruction_dynamic = reasons
        if text:
            spec.static_instruction_text = text
            spec.static_instruction_len = len(text)

    global_node = _kwarg(call, "global_instruction")
    spec.has_global_instruction = global_node is not None and not (
        isinstance(global_node, ast.Constant) and global_node.value is None
    )

    tools_node = _kwarg(call, "tools")
    if tools_node is not None and facts is not None:
        binding = _binding_names(tools_node, facts)
        spec.tools = [name for name in binding.bound if name]
        spec.opaque_tools = [name for name, _ in binding.opaque]
        spec.tools_unresolved = not binding.resolved
        for name, kind in binding.opaque:
            facts.opaque_tools.setdefault(name, kind)
    spec.sub_agents = _list_names(_kwarg(call, "sub_agents"))
    spec.output_schema = _name_of(_kwarg(call, "output_schema"))
    spec.input_schema = _name_of(_kwarg(call, "input_schema"))
    spec.output_key = _literal(_kwarg(call, "output_key"))
    spec.callbacks = [kw for kw in spec.kwargs if kw and "callback" in kw]

    gcc = _kwarg(call, "generate_content_config")
    if isinstance(gcc, ast.Call):
        spec.gcc_forbidden = [kw.arg for kw in gcc.keywords if kw.arg in FORBIDDEN_GCC_FIELDS]

    contents = _kwarg(call, "include_contents")
    if contents is not None:
        literal = _literal(contents)
        if literal is None and facts is not None and isinstance(contents, ast.Name):
            literal = _literal(facts.expr_constants.get(contents.id))
        spec.include_contents = literal if isinstance(literal, str) else _unparse(contents)
    return spec


def _collect_app(var: str, call: ast.Call, facts: ModuleFacts | None = None) -> AppSpec:
    spec = AppSpec(var=var, lineno=call.lineno)
    spec.name = _literal(_kwarg(call, "name"))
    spec.plugins = _list_names(_kwarg(call, "plugins"))
    root = _kwarg(call, "root_agent")
    spec.root_agent = _name_of(root) or _unparse(root)
    spec.kwargs = [kw.arg for kw in call.keywords if kw.arg]

    cache = _kwarg(call, "context_cache_config")
    if cache is not None and not (isinstance(cache, ast.Constant) and cache.value is None):
        spec.context_cache_set = True
        resolved: ast.AST = cache
        seen: set[str] = set()
        expr_constants = facts.expr_constants if facts else {}
        while isinstance(resolved, (ast.Name, ast.Attribute)):
            name = _name_of(resolved)
            if not name or name in seen or name not in expr_constants:
                break
            seen.add(name)
            resolved = expr_constants[name]
        if isinstance(resolved, ast.Constant) and resolved.value is None:
            spec.context_cache_set = False
        elif isinstance(resolved, ast.Call) and _name_of(resolved.func) == "ContextCacheConfig" \
                and all(kw.arg for kw in resolved.keywords) and not resolved.args:
            config: dict[str, Any] = {}
            for keyword in resolved.keywords:
                literal = _literal(keyword.value)
                config[keyword.arg] = literal if literal is not None else _unparse(keyword.value)
            spec.context_cache_config = config
        else:
            spec.context_cache_expr = _unparse(cache)

    compaction = _kwarg(call, "events_compaction_config")
    if compaction is not None and not (isinstance(compaction, ast.Constant) and compaction.value is None):
        spec.events_compaction_set = True
        resolved = _follow_constant(compaction, facts)
        if isinstance(resolved, ast.Constant) and resolved.value is None:
            spec.events_compaction_set = False
        elif isinstance(resolved, ast.Call) and _name_of(resolved.func) == "EventsCompactionConfig" \
                and all(kw.arg for kw in resolved.keywords) and not resolved.args:
            config = {}
            for keyword in resolved.keywords:
                value = _follow_constant(keyword.value, facts)
                literal = _literal(value)
                config[keyword.arg] = literal if literal is not None else _unparse(keyword.value)
            spec.events_compaction_config = config
        else:
            spec.events_compaction_expr = _unparse(compaction)
    return spec


def _follow_constant(node: ast.AST | None, facts: ModuleFacts | None, _seen: frozenset[str] = frozenset()) -> ast.AST | None:
    """Follow `NAME` (or `module.NAME`) to the module-level expression it holds."""
    expr_constants = facts.expr_constants if facts else {}
    while isinstance(node, (ast.Name, ast.Attribute)):
        name = _name_of(node)
        if not name or name in _seen or name not in expr_constants:
            break
        _seen = _seen | {name}
        node = expr_constants[name]
    return node


# ---------------------------------------------------------------------------
# skills
# ---------------------------------------------------------------------------

def _resolve_path(node: ast.AST | None, file: str | None, facts: ModuleFacts | None,
                  _depth: int = 0) -> tuple[Path | None, bool]:
    """(path, anchored) for a path expression, when it can be read.

    `anchored` is True when the path is built from `__file__`, so it does not
    depend on the directory the agent is started from. Follows the shapes real
    agents use: `pathlib.Path(__file__).parent / "skills" / "x"`,
    `os.path.join(os.path.dirname(__file__), "skills")`, `.resolve()`,
    `.joinpath(...)`, a module constant holding any of those, and a plain
    string (relative to the working directory at run time).
    """
    if node is None or _depth > 12:
        return None, False
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return Path(node.value), Path(node.value).is_absolute()
    if isinstance(node, ast.Name):
        if node.id == "__file__" and file:
            return Path(file).resolve(), True
        if facts is not None and node.id in facts.expr_constants:
            return _resolve_path(facts.expr_constants[node.id], file, facts, _depth + 1)
        return None, False
    if isinstance(node, ast.Attribute):
        if node.attr == "parent":
            base, anchored = _resolve_path(node.value, file, facts, _depth + 1)
            return (base.parent if base is not None else None), anchored
        if facts is not None and node.attr in facts.expr_constants:
            return _resolve_path(facts.expr_constants[node.attr], file, facts, _depth + 1)
        return None, False
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left, anchored = _resolve_path(node.left, file, facts, _depth + 1)
        right, _ = _resolve_path(node.right, file, facts, _depth + 1)
        if left is None or right is None:
            return None, False
        return left / right, anchored
    if isinstance(node, ast.Call):
        func = node.func
        name = _name_of(func)
        dotted = _dotted(func)
        if name in ("Path", "PurePath", "PosixPath", "str", "fspath", "abspath", "realpath",
                    "normpath", "expanduser") and node.args:
            if len(node.args) > 1 and name in ("Path", "PurePath", "PosixPath"):
                parts = [_resolve_path(a, file, facts, _depth + 1) for a in node.args]
                if any(p is None for p, _ in parts):
                    return None, False
                joined = parts[0][0]
                for part, _ in parts[1:]:
                    joined = joined / part
                return joined, parts[0][1]
            return _resolve_path(node.args[0], file, facts, _depth + 1)
        if name == "dirname" and node.args:
            base, anchored = _resolve_path(node.args[0], file, facts, _depth + 1)
            return (base.parent if base is not None else None), anchored
        if (name == "join" and dotted.endswith("path.join")) or name == "joinpath":
            args = list(node.args)
            if name == "joinpath" and isinstance(func, ast.Attribute):
                args = [func.value, *args]
            parts = [_resolve_path(a, file, facts, _depth + 1) for a in args]
            if not parts or any(p is None for p, _ in parts):
                return None, False
            joined = parts[0][0]
            for part, _ in parts[1:]:
                joined = joined / part
            return joined, parts[0][1]
        if name in ("resolve", "absolute") and isinstance(func, ast.Attribute):
            return _resolve_path(func.value, file, facts, _depth + 1)
    return None, False


def _locate(path: Path, anchored: bool, file: str | None) -> Path | None:
    """An absolute location for a path read from source, or None when it
    depends on the working directory and no plausible one exists."""
    if anchored or path.is_absolute():
        return path
    if not file:
        return None
    here = Path(file).resolve().parent
    for base in (here, here.parent, here.parent.parent):
        candidate = base / path
        if candidate.exists():
            return candidate
    return None


def _frontmatter_value(node: ast.AST, facts: ModuleFacts) -> Any:
    text = _string_value(node, facts.str_constants)
    if text is not None:
        return text
    literal = _literal(_follow_constant(node, facts))
    if literal is not None or (isinstance(node, ast.Constant) and node.value is None):
        return literal
    if isinstance(node, ast.Dict):
        out: dict[Any, Any] = {}
        for key, value in zip(node.keys, node.values):
            k = _literal(key)
            if isinstance(k, str):
                v = _literal(value)
                out[k] = v if v is not None else UNREADABLE
        return out
    return UNREADABLE


def _resource_counts(node: ast.AST | None, facts: ModuleFacts) -> dict[str, int]:
    node = _follow_constant(node, facts)
    counts = {"references": 0, "assets": 0, "scripts": 0}
    if isinstance(node, ast.Call):
        for keyword in node.keywords:
            if keyword.arg in counts:
                value = _follow_constant(keyword.value, facts)
                if isinstance(value, ast.Dict):
                    counts[keyword.arg] = len(value.keys)
    return counts


def _inline_skill(call: ast.Call, file: str | None, facts: ModuleFacts) -> SkillInfo:
    fm_node = _follow_constant(_kwarg(call, "frontmatter") or (call.args[0] if call.args else None), facts)
    frontmatter: dict[str, Any] = {}
    if isinstance(fm_node, ast.Call) and all(kw.arg for kw in fm_node.keywords):
        for keyword in fm_node.keywords:
            frontmatter[keyword.arg] = _frontmatter_value(keyword.value, facts)
    elif isinstance(fm_node, ast.Dict):
        value = _frontmatter_value(fm_node, facts)
        frontmatter = value if isinstance(value, dict) else {}
    else:
        frontmatter = {"name": UNREADABLE, "description": UNREADABLE}
    if isinstance(fm_node, ast.Call) and fm_node.args:
        frontmatter.setdefault("name", UNREADABLE)
    instructions_node = _kwarg(call, "instructions")
    if instructions_node is None and len(call.args) > 1:
        instructions_node = call.args[1]
    instructions = _string_value(instructions_node, facts.str_constants) if instructions_node is not None else None
    unreadable = instructions_node is not None and instructions is None
    return inline_skill(
        frontmatter, instructions, _resource_counts(_kwarg(call, "resources"), facts),
        file=file, lineno=call.lineno, unreadable=unreadable,
    )


def _resolve_skills(node: ast.AST | None, file: str | None, facts: ModuleFacts,
                    _seen: frozenset[str] = frozenset(), _depth: int = 0,
                    ) -> tuple[list[SkillInfo], str | None]:
    """(skills, why the set is not fully known) for a `skills=` expression."""
    if node is None or (isinstance(node, ast.Constant) and node.value is None):
        return [], None
    if _depth > 8:
        return [], "the skills expression nests too deeply to read"
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        found: list[SkillInfo] = []
        reasons: list[str] = []
        for element in node.elts:
            skills, reason = _resolve_skills(element, file, facts, _seen, _depth + 1)
            found.extend(skills)
            if reason:
                reasons.append(reason)
        return found, "; ".join(dict.fromkeys(reasons)) or None
    if isinstance(node, ast.Starred):
        return _resolve_skills(node.value, file, facts, _seen, _depth + 1)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, lr = _resolve_skills(node.left, file, facts, _seen, _depth + 1)
        right, rr = _resolve_skills(node.right, file, facts, _seen, _depth + 1)
        return left + right, "; ".join(r for r in (lr, rr) if r) or None
    if isinstance(node, (ast.Name, ast.Attribute)):
        name = _name_of(node)
        if not name or name in _seen or name not in facts.expr_constants:
            return [], f"`{_unparse(node)}` is not a module-level value this review can read"
        skills, reason = _resolve_skills(facts.expr_constants[name], file, facts,
                                         _seen | {name}, _depth + 1)
        reasons = [reason] if reason else []
        for added in facts.list_mutations.get(name, []):
            more, more_reason = _resolve_skills(added, file, facts, _seen | {name}, _depth + 1)
            skills = skills + more
            if more_reason:
                reasons.append(more_reason)
        return skills, "; ".join(dict.fromkeys(reasons)) or None
    if isinstance(node, ast.Call):
        call_name = _name_of(node.func)
        if call_name in SKILL_CALLS:
            return [_inline_skill(node, file, facts)], None
        if call_name in SKILL_DIR_LOADERS:
            target = _kwarg(node, "skill_dir") or _kwarg(node, "skills_dir") or \
                (node.args[0] if node.args else None)
            path, anchored = _resolve_path(target, file, facts)
            located = _locate(path, anchored, file) if path is not None else None
            if located is None:
                return [], f"`{call_name}({_unparse(target)})` takes a path this review cannot resolve"
            if SKILL_DIR_LOADERS[call_name] == "one":
                return [load_skill_dir(located, file=file, lineno=node.lineno)], None
            return load_skills_dir(located, file=file, lineno=node.lineno), None
        if call_name in SKILL_REMOTE_LOADERS or (call_name and "gcs" in call_name.lower()):
            return [SkillInfo(name=None, source="gcs", file=file, lineno=node.lineno,
                              unreadable=True)], "skills are loaded from GCS at run time"
        if call_name in ("list", "sorted", "tuple") and node.args:
            return _resolve_skills(node.args[0], file, facts, _seen, _depth + 1)
    return [], f"`{_unparse(node)}` is built at run time"


def _lifecycle_value(node: ast.AST, facts: ModuleFacts) -> Any:
    node = _follow_constant(node, facts)
    if isinstance(node, ast.Attribute) and node.attr in SKILL_LIFECYCLE_MODES:
        return node.attr
    if isinstance(node, ast.Dict):
        out: dict[str, Any] = {}
        for key, value in zip(node.keys, node.values):
            k = _literal(key)
            if isinstance(k, str):
                out[k] = _lifecycle_value(value, facts)
        return out
    literal = _literal(node)
    if literal is not None or (isinstance(node, ast.Constant) and node.value is None):
        return literal
    return _unparse(node)


def _collect_skill_toolset(var: str | None, call: ast.Call, file: str | None,
                           facts: ModuleFacts) -> SkillToolsetSpec:
    spec = SkillToolsetSpec(var=var, file=file, lineno=call.lineno,
                            kwargs=[kw.arg for kw in call.keywords if kw.arg])
    skills_node = _kwarg(call, "skills") or (call.args[0] if call.args else None)
    spec.skills, reason = _resolve_skills(skills_node, file, facts)
    registry = _kwarg(call, "registry")
    spec.registry = registry is not None and not (isinstance(registry, ast.Constant) and registry.value is None)
    reasons = [reason] if reason else []
    if spec.registry:
        spec.skills.append(SkillInfo(name=None, source="registry", file=file,
                                     lineno=call.lineno, unreadable=True))
        reasons.append("a skill registry adds skills at run time (`search_skills`)")
    if any(kw.arg is None for kw in call.keywords):
        reasons.append("`**kwargs` decides the rest at run time")
    if reasons:
        spec.skills_unresolved = True
        spec.unresolved_reason = "; ".join(dict.fromkeys(reasons))

    mode = _kwarg(call, "discovery_mode")
    if mode is not None:
        resolved = _follow_constant(mode, facts)
        if isinstance(resolved, ast.Attribute) and resolved.attr in ("LAZY", "EAGER"):
            spec.discovery_mode = resolved.attr
        elif isinstance(_literal(resolved), str) and _literal(resolved).upper() in ("LAZY", "EAGER"):
            spec.discovery_mode = _literal(resolved).upper()
        else:
            spec.discovery_mode = _unparse(mode)

    lifecycle = _kwarg(call, "lifecycle_config")
    if lifecycle is not None and not (isinstance(lifecycle, ast.Constant) and lifecycle.value is None):
        resolved = _follow_constant(lifecycle, facts)
        if isinstance(resolved, ast.Call) and _name_of(resolved.func) == "SkillLifecycleConfig" \
                and all(kw.arg for kw in resolved.keywords) and not resolved.args:
            spec.lifecycle_config = {
                kw.arg: _lifecycle_value(kw.value, facts) for kw in resolved.keywords
            }
        else:
            spec.lifecycle_expr = _unparse(lifecycle)

    prefix = _literal(_follow_constant(_kwarg(call, "tool_name_prefix"), facts))
    spec.tool_name_prefix = prefix if isinstance(prefix, str) and prefix else None
    return spec


def _register_skill_tools(facts: ModuleFacts) -> None:
    """A SkillToolset's generated tools are real trajectory names: opaque, not
    unknown, so EVS036 and tool coverage treat `load_skill` like any toolset
    tool."""
    for toolset in facts.skill_toolsets:
        for name in toolset.tool_names:
            facts.opaque_tools.setdefault(name, "skill_toolset")


def _collect_list_mutations(tree: ast.Module, facts: ModuleFacts) -> None:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ("append", "extend", "insert") or not node.args:
            continue
        owner = node.func.value
        if not isinstance(owner, ast.Name):
            continue
        added = node.args[-1]
        if node.func.attr == "extend":
            facts.list_mutations.setdefault(owner.id, []).append(added)
        else:
            facts.list_mutations.setdefault(owner.id, []).append(ast.List(elts=[added], ctx=ast.Load()))


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------

def collect_constants(path: Path | str, facts: ModuleFacts) -> None:
    """Record the module-level names an agent's kwargs refer to.

    Runs over every file before agents are extracted, because an agent in
    `agent.py` routinely takes its instruction from `prompt.py` and its tool
    list from a constant defined beside it.

    Four kinds are recorded: string constants (instructions and `name=`),
    environment-backed constants (`model=`), list constants (`tools=`), and
    tool objects assigned to a name - a `FunctionTool` wrapper, whose bound
    trajectory name is the function it wraps, or a toolset, which is opaque.
    """
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return

    def targets_of(node: ast.Assign | ast.AnnAssign) -> list[str]:
        if isinstance(node, ast.AnnAssign):
            return [node.target.id] if isinstance(node.target, ast.Name) else []
        return [t.id for t in node.targets if isinstance(t, ast.Name)]

    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        names = targets_of(node)
        if not names or node.value is None:
            continue
        value = node.value
        for name in names:
            facts.expr_constants[name] = value

        if isinstance(value, (ast.List, ast.Tuple)):
            for name in names:
                facts.list_constants[name] = list(value.elts)

        if isinstance(value, ast.Call):
            call_name = _name_of(value.func)
            if call_name in WRAPPER_TOOL_CALLS:
                wrapped = _wrapped_function(value)
                if wrapped:
                    for name in names:
                        facts.tool_aliases[name] = wrapped
                continue
            if call_name == "AgentTool":
                for name in names:
                    facts.opaque_constants[name] = "agent_tool"
                continue
            if _is_opaque_call(call_name):
                for name in names:
                    facts.opaque_constants[name] = "toolset"
                continue

        if isinstance(value, ast.Name) and value.id in OPAQUE_TOOL_NAMES:
            for name in names:
                facts.opaque_constants[name] = "builtin"

        if isinstance(node, ast.Assign) and _env_lookup(value):
            fallback = _env_fallback(value)
            for name in names:
                facts.env_constants[name] = fallback
            continue

        text = _string_value(value, facts.str_constants)
        if isinstance(text, str) and text:
            for name in names:
                facts.str_constants[name] = text


def extract_file(path: Path | str, facts: ModuleFacts | None = None) -> ModuleFacts:
    """Add one Python file's facts to a (possibly shared) ModuleFacts."""
    facts = facts or ModuleFacts()
    target = Path(path)
    try:
        source = target.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(target))
    except (OSError, UnicodeDecodeError) as exc:
        facts.syntax_errors.append(f"{target}: {exc}")
        return facts
    except SyntaxError as exc:
        facts.syntax_errors.append(f"{target}:{exc.lineno}: {exc.msg}")
        return facts

    facts.files.append(str(target))
    file_key = str(target)
    imports_here = facts.file_imports.setdefault(file_key, set())

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            facts.imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            facts.imports.add(module)
            for alias in node.names:
                facts.imports.add(f"{module}.{alias.name}")
                imports_here.add(f"{module}.{alias.name}")
                if (module.startswith("google.adk.plugins.") and alias.name in PLUGIN_EXPORTS
                        and any(part.startswith("_") for part in module.split("."))):
                    facts.private_plugin_imports.append(
                        (file_key, node.lineno, module, alias.name))

    # Pass 1: Pydantic models, so tool parameters can resolve to them.
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            base = _is_pydantic_model(node)
            if base:
                facts.models[node.name] = _collect_model(node, base)

    # Pass 2: tool candidates. Only module-level functions: a class method is
    # reached through an instance, never registered by name, and a nested
    # helper is not reachable at all. Collecting either invents tools that do
    # not exist and drags `self` into their schemas.
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            facts.callables.add(node.name)
            if node.name.startswith("_"):
                continue
            tool = _collect_tool(node, facts.models)
            tool.file = file_key
            tool.decorators = [d for d in (_name_of(dec) for dec in node.decorator_list) if d]
            facts.tool_candidates.setdefault(node.name, []).append(tool)

    _collect_list_mutations(tree, facts)

    # Skill toolsets: every `SkillToolset(...)`, assigned or inline in `tools=`.
    assigned: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned[id(node.value)] = target.id
                    break
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _name_of(node.func) in SKILL_TOOLSET_CALLS:
            facts.skill_toolsets.append(
                _collect_skill_toolset(assigned.get(id(node)), node, file_key, facts))

    # Pass 3: agents, apps and state writes.
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            value = node.value
            call_name = _name_of(value) if isinstance(value, ast.Call) else None
            var = None
            for t in node.targets:
                if isinstance(t, ast.Name):
                    var = t.id
                    break
            if isinstance(value, ast.Call) and call_name in AGENT_CALLS:
                agent = _collect_agent(
                    var, value, call_name, facts.str_constants, facts.env_constants,
                    facts=facts,
                )
                agent.file = file_key
                facts.agents.append(agent)
            elif isinstance(value, ast.Call) and call_name == "App" and var:
                app = _collect_app(var, value, facts)
                app.file = file_key
                facts.apps.append(app)
        elif isinstance(node, ast.Return) and isinstance(node.value, ast.Call):
            returned = _name_of(node.value)
            if returned in AGENT_CALLS:
                agent = _collect_agent(
                    None, node.value, returned or "",
                    facts.str_constants, facts.env_constants, facts=facts,
                )
                agent.file = file_key
                facts.agents.append(agent)
            elif returned == "App":
                # A factory: `def build() -> App: return App(name=..., ...)`,
                # then `app = build()`. The App is exported all the same, and
                # its declared name is what the runner falls back to.
                app = _collect_app("app", node.value, facts)
                app.file = file_key
                facts.apps.append(app)

    # Pass 4: exports that hide the agent behind a runtime object, e.g.
    # `root_agent = coordinator_wrapper._agent`. Module level only: an attribute
    # assignment inside a function or a class body is not an export.
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Attribute):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in EXPORT_NAMES:
                facts.unresolved_exports[target.id] = _unparse(node.value) or "?"

    # `session.state["x"] = ...` bypasses the event history.
    _collect_state_writes(tree, file_key, facts)
    return facts


def _collect_state_writes(tree: ast.Module, file_key: str, facts: ModuleFacts) -> None:
    """Find direct session-state writes, and note which are inside a hook."""

    def walk(node: ast.AST, enclosing: str | None, in_hook: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, child.name, in_hook or _is_hook(child))
                continue
            if isinstance(child, ast.Assign):
                for target in child.targets:
                    if not isinstance(target, ast.Subscript):
                        continue
                    text = _unparse(target) or ""
                    if ".state[" in text and text.split(".state[")[0].endswith("session"):
                        facts.state_writes.append(StateWrite(
                            path=file_key, lineno=child.lineno, text=text,
                            enclosing=enclosing, is_hook=in_hook,
                        ))
            walk(child, enclosing, in_hook)

    walk(tree, None, False)


def _resolve_tool_candidates(facts: ModuleFacts) -> None:
    """Pick one definition per tool name.

    Two modules in a repository routinely define `search` or `read`. The one
    that matters is the one the agent's own file imports; last-file-wins picks
    at random and then reports the wrong signature. Anything still ambiguous is
    recorded so the report can say so rather than pretend.
    """
    agent = facts.root_agent()
    agent_files = {a.file for a in facts.agents if a.file}
    if agent and agent.file:
        agent_files = {agent.file} | agent_files
    imported: set[str] = set()
    for agent_file in agent_files:
        imported |= facts.file_imports.get(agent_file or "", set())

    for name, candidates in facts.tool_candidates.items():
        if len(candidates) == 1:
            facts.tools[name] = candidates[0]
            continue
        facts.tool_collisions[name] = sorted(c.file or "?" for c in candidates)
        preferred = None
        for candidate in candidates:
            module = Path(candidate.file or "").stem
            if any(dotted.endswith(f".{name}") and f".{module}." in f".{dotted}."
                   for dotted in imported):
                preferred = candidate
                break
        if preferred is None:
            # Prefer a definition sitting beside the agent, then the shortest
            # path: a top-level module beats something buried in a subpackage.
            preferred = sorted(
                candidates,
                key=lambda c: (c.file not in agent_files, len(Path(c.file or "").parts)),
            )[0]
        facts.tools[name] = preferred


def _resolve_agent_tool_names(facts: ModuleFacts) -> None:
    """Swap an `AgentTool(agent=x)` variable for the agent's declared name.

    The trajectory carries `x.name`, not the Python variable, and the agent may
    be built in a file parsed after the one that wraps it.
    """
    by_var = {a.var: a.name for a in facts.agents if a.var and a.name}
    for variable in facts._agent_tool_refs:
        declared = by_var.get(variable)
        if not declared or declared == variable:
            continue
        kind = facts.opaque_tools.pop(variable, "agent_tool")
        facts.opaque_tools[declared] = kind
        for agent in facts.agents:
            agent.opaque_tools = [
                declared if t == variable else t for t in agent.opaque_tools
            ]


def resolve_tool(facts: ModuleFacts, name: str) -> ToolBinding:
    """What a trajectory name refers to. The one place EVS036 is decided.

    `unknown` - and only `unknown` - means the agent could never emit this name.
    """
    if not name:
        return ToolBinding(name=name, status="unknown", reason="empty name")

    if name in facts.opaque_tools:
        return ToolBinding(name=name, status="opaque", reason=facts.opaque_tools[name])

    bound = facts.bound_tool_names
    spec = facts.tools.get(name)
    if name in bound:
        if spec is not None:
            return ToolBinding(name=name, status="defined", spec=spec)
        return ToolBinding(name=name, status="opaque", reason="imported")
    if spec is not None:
        if facts.tools_unresolved:
            return ToolBinding(
                name=name, status="unresolved", spec=spec,
                reason="defined in this project; the tool list is built at runtime, "
                       "so whether it is bound cannot be read",
            )
        return ToolBinding(name=name, status="defined_not_bound", spec=spec)
    if facts.tools_unresolved:
        return ToolBinding(
            name=name, status="unresolved",
            reason="this agent builds its tool list at runtime, so absence proves nothing",
        )
    return ToolBinding(name=name, status="unknown")


def tool_resolver(facts: ModuleFacts):
    """A one-argument resolver, for callers that only have a name to check."""

    def _resolve(name: str) -> ToolBinding:
        return resolve_tool(facts, name)

    return _resolve


def extract_project(paths: Iterable[Path | str]) -> ModuleFacts:
    facts = ModuleFacts()
    files = list(paths)
    for path in files:               # constants first: agent.py may import prompt.py
        collect_constants(path, facts)
    for path in files:
        extract_file(path, facts)
    _resolve_tool_candidates(facts)
    _resolve_agent_tool_names(facts)
    _register_skill_tools(facts)
    for tool in facts.tools.values():
        for name in tool.returned_names:
            text = facts.str_constants.get(name)
            if text:
                tool.large_return_chars = max(tool.large_return_chars, len(text))
    analyze(facts)
    return facts


def extract_module(path: Path | str) -> ModuleFacts:
    """Facts for one agent directory (or file), including review findings."""
    target = Path(path)
    if target.is_file():
        files = [target]
    else:
        files = [
            p for p in sorted(target.rglob("*.py"))
            if "__pycache__" not in p.parts and ".venv" not in p.parts
        ]
    return extract_project(files)


# ---------------------------------------------------------------------------
# review rules
# ---------------------------------------------------------------------------

def analyze(facts: ModuleFacts) -> list[Finding]:
    """Findings that follow from the extracted facts. Appended to `facts`."""
    findings: list[Finding] = []

    def add(fid: str, severity: str, message: str, *, path: str | None = None,
            locator: str | None = None, fix: str | None = None, source: str | None = None) -> None:
        findings.append(Finding(id=fid, severity=severity, area="agent", message=message,
                                path=path, locator=locator, fix=fix, source=source))

    bound = facts.bound_tool_names

    for agent in facts.agents:
        where = f"{agent.call}({agent.var or 'inline'})"
        who = f"{agent.call} {agent.name!r}" if agent.name else where
        locator = f"line {agent.lineno}"

        if agent.call in DEPRECATED_AGENT_CALLS:
            add("AGT001", "warn",
                f"{agent.call} is superseded by google.adk.workflow.Workflow in ADK 2.x",
                path=agent.file, locator=locator,
                fix="port to a Workflow graph; keep the shell only where an LlmAgent must be "
                    "able to transfer into the orchestrated block, which Workflow cannot yet do",
                source=_SRC_WORKFLOW)

        if agent.name and not agent.name.isidentifier():
            add("AGT011", "error", f"agent name {agent.name!r} is not a valid Python identifier",
                path=agent.file, locator=locator,
                fix="use letters, digits and underscores, starting with a letter",
                source=_SRC_BASE_AGENT)
        elif agent.name == "user":
            add("AGT011", "error", 'agent name "user" is reserved and will be rejected',
                path=agent.file, locator=locator, fix="rename the agent", source=_SRC_BASE_AGENT)
        elif ("name" not in agent.kwargs and agent.call in LLM_AGENT_CALLS
              and not agent.kwargs_unresolved):
            add("AGT012", "error", f"{where} has no `name`, which is required",
                path=agent.file, locator=locator, source=_SRC_LLM_AGENT)

        if "global_instruction" in agent.kwargs:
            add("AGT002", "warn", "`global_instruction` is deprecated",
                path=agent.file, locator=locator,
                fix="move it to GlobalInstructionPlugin on the App", source=_SRC_LLM_AGENT)

        if agent.static_instruction_dynamic:
            add("AGT060", "warn",
                f"{who}: `static_instruction` is not byte-identical between requests "
                f"({'; '.join(agent.static_instruction_dynamic)})",
                path=agent.file, locator=locator,
                fix="keep `static_instruction` a plain literal (or a module constant holding "
                    "one): it is the cached prefix, so any change invalidates the cache, and "
                    "ADK sends it verbatim, so `{var}` placeholders are never filled there. "
                    "Move the varying part to `instruction=`, which supports placeholders "
                    "and an InstructionProvider and is sent after the static prefix",
                source=f"{_SRC_LLM_AGENT} (static_instruction); {_SRC_CACHE_DOC}")

        if (agent.call in LLM_AGENT_CALLS and agent.instruction_len >= LARGE_INSTRUCTION_CHARS
                and not agent.has_static_instruction
                and not any(app.context_cache_set for app in facts.apps)):
            add("AGT061", "info",
                f"{who}: the instruction is {agent.instruction_len:,} characters "
                f"(about {agent.instruction_len // 4:,} tokens) and nothing caches it",
                path=agent.file, locator=locator,
                fix="move the unchanging part into `static_instruction=` and set "
                    "`context_cache_config=ContextCacheConfig(...)` on the App; caching starts "
                    "on a session's second turn and needs a 2,048-token (Gemini 2.5) or "
                    "4,096-token (Gemini 3) prefix, so a single-turn agent gains nothing",
                source=f"{_SRC_CACHE_CONFIG}; {_SRC_CACHE_DOC}")

        misplaced = sorted(set(agent.kwargs) & MISPLACED_MODEL_KWARGS)
        if misplaced:
            add("AGT016", "error",
                f"{', '.join(misplaced)} passed straight to {agent.call}, which raises at construction",
                path=agent.file, locator=locator,
                fix="move them into generate_content_config=types.GenerateContentConfig(...)",
                source=_SRC_LLM_AGENT)

        if agent.call in LLM_AGENT_CALLS:
            if (not agent.has_instruction and not agent.has_static_instruction
                    and not agent.kwargs_unresolved):
                add("AGT020", "warn", f"{where} has no `instruction`",
                    path=agent.file, locator=locator,
                    fix="the instruction is the agent's contract; without it behaviour is "
                        "whatever the model guesses, and nothing is testable",
                    source=_SRC_LLM_AGENT)
            grade = classify_model_id(agent.model)
            if agent.model and grade == "deprecated":
                add("AGT005", "warn",
                    f"{who}: model {agent.model!r} is deprecated",
                    path=agent.file, locator=locator,
                    fix="read the id from the environment (MODEL_NAME) and default to an alias "
                        "such as gemini-flash-latest; the id still resolves today, so this is a "
                        "clock running down rather than a break",
                    source="adk-recipes/AGENTS.md")
            elif agent.model and grade == "preview":
                add("AGT006", "warn",
                    f"{who}: model {agent.model!r} is a preview id that will be withdrawn",
                    path=agent.file, locator=locator, fix="prefer an alias id, or read MODEL_NAME",
                    source="adk-recipes/AGENTS.md")
            elif agent.model and grade == "pinned" and not agent.model_from_env:
                add("AGT004", "warn", f"model {agent.model!r} is hardcoded",
                    path=agent.file, locator=locator,
                    fix="read it from MODEL_NAME so the agent survives a model retirement",
                    source="model policy: skills/agent-review/references/model-policy.md")
            elif (agent.model is None and not agent.model_from_env
                  and "model" not in agent.kwargs and not agent.kwargs_unresolved):
                add("AGT007", "info", f"{who} sets no model, so it inherits one",
                    path=agent.file, locator=locator,
                    fix="fine for a sub-agent; the root agent should be explicit",
                    source=_SRC_LLM_AGENT)

        if agent.gcc_forbidden:
            add("AGT003", "error",
                f"generate_content_config carries {', '.join(sorted(agent.gcc_forbidden))}, "
                "which ADK rejects at construction",
                path=agent.file, locator=locator,
                fix="tools go in `tools=`, the system prompt in `instruction=`, and a response "
                    "schema in `output_schema=`",
                source=_SRC_LLM_AGENT)

        duplicates = {name for name in agent.sub_agents if agent.sub_agents.count(name) > 1}
        if duplicates:
            add("AGT015", "warn",
                f"duplicate sub_agent name(s): {', '.join(sorted(duplicates))}",
                path=agent.file, locator=locator,
                fix=DUPLICATE_NAME_FIX,
                source=_SRC_BASE_AGENT)

        if agent.output_schema and agent.sub_agents:
            add("AGT021", "warn",
                f"{who} sets both `output_schema` and `sub_agents`",
                path=agent.file, locator=locator,
                fix="structured output competes with delegation: the model is asked to emit "
                    "a fixed schema and to hand the turn to a sub-agent in the same breath, "
                    "so routing becomes unpredictable. Keep the schema on a leaf agent and "
                    "let the parent route, or drop the sub-agents from this one",
                source=_SRC_LLM_AGENT)

        if agent.output_schema and agent.tools:
            add("AGT019", "info",
                f"{where} sets both output_schema and tools; supported in ADK 2.x, but the "
                "model may answer with structured output instead of calling a tool",
                path=agent.file, locator=locator,
                fix="if a tool is skipped, check the trajectory before blaming the prompt",
                source=_SRC_LLM_AGENT)

    for name in sorted(bound):
        binding = resolve_tool(facts, name)
        if binding.status == "defined" and binding.spec is not None:
            findings.extend(_tool_findings(binding.spec, facts.models))
        elif binding.status == "opaque" and binding.reason == "imported":
            # A toolset or a built-in tool object is bound on purpose and has no
            # contract to read here, so it is not worth a note; a plain imported
            # function is, because its contract exists somewhere reviewable.
            add("AGT030", "info",
                f"tool {name!r} is bound but not defined in this project (imported)",
                fix="static checks cannot see its contract; review it where it is defined")

    for app in facts.apps:
        for problem in cache_config_problems(app.context_cache_config):
            add("AGT062", "error",
                f"App {app.name or app.var!r}: ContextCacheConfig {problem}, so building the "
                "App raises a ValidationError",
                path=app.file, locator=f"line {app.lineno}",
                fix="cache_intervals must be 1..100 (default 10), ttl_seconds > 0 (default "
                    "1800), min_tokens >= 0 (default 0); the only other field is "
                    "create_http_options",
                source=_SRC_CACHE_CONFIG)

    for app in facts.apps:
        if not app.context_cache_set:
            continue
        agent = next((a for a in facts.agents if a.var and a.var == app.root_agent), None) \
            or facts.root_agent()
        if agent is None or agent.call not in LLM_AGENT_CALLS or agent.kwargs_unresolved:
            continue
        if agent.instruction_is_dynamic and not agent.has_static_instruction:
            continue                       # the prefix is built at runtime: unknowable
        min_tokens = (app.context_cache_config or {}).get("min_tokens")
        if not isinstance(min_tokens, int) or isinstance(min_tokens, bool):
            min_tokens = 0
        floor = CACHE_FLOOR_TOKENS["Gemini 2.5"]
        estimate = agent.cache_prefix_tokens_estimate
        if estimate >= max(min_tokens, floor):
            continue
        tools = len(agent.tools) + len(agent.opaque_tools)
        add("AGT067", "info",
            f"context caching is enabled on App {app.name or app.var!r} with "
            f"min_tokens={min_tokens}, but the static prefix is about {estimate:,} tokens "
            f"({agent.cache_prefix_chars:,} chars of instruction plus {tools} tool "
            f"declaration{'s' if tools != 1 else ''}): ADK only creates a cache once the "
            "previous request reached min_tokens, and the model floor is "
            f"{CACHE_FLOOR_TOKENS['Gemini 2.5']} (Gemini 2.5) or "
            f"{CACHE_FLOOR_TOKENS['Gemini 3']} (Gemini 3), so early turns never qualify and "
            "later hits are mostly history, not the prefix",
            path=app.file, locator=f"line {app.lineno}",
            fix="grow the static procedure (it is what the cache is for); lower min_tokens "
                "only if the prefix really is above the model floor; or drop "
                "context_cache_config and rely on the provider's implicit caching. The "
                "break-even in the caching guidance is around 32k tokens of static context",
            source=f"{_SRC_CACHE_CONFIG}; {_SRC_CACHE_DOC}")

    for app in facts.apps:
        for problem in compaction_config_problems(app.events_compaction_config):
            add("AGT076", "error",
                f"App {app.name or app.var!r}: EventsCompactionConfig {problem}, so building "
                "the App raises a ValidationError",
                path=app.file, locator=f"line {app.lineno}",
                fix="set the sliding window as a pair (compaction_interval > 0 with "
                    "overlap_size >= 0), the token trigger as a pair (token_threshold > 0 with "
                    "event_retention_size >= 0), or both; the only other field is summarizer",
                source=_SRC_COMPACTION)

    findings.extend(_skill_findings(facts))
    findings.extend(_sub_agent_scope_findings(facts))
    findings.extend(_duplicate_name_findings(facts))

    for file_key, lineno, module, name in facts.private_plugin_imports:
        since = PLUGIN_EXPORTS.get(name, "2.0")
        add("AGT065", "warn",
            f"`{name}` is imported from the private module `{module}`",
            path=file_key, locator=f"line {lineno}",
            fix=f"import it from the package: `from google.adk.plugins import {name}`; the "
                "package re-exports it lazily and the private module may move"
                + (f". It needs ADK {since} or later" if since != "2.0" else ""),
            source="adk-python: plugins/__init__.py")

    for write in facts.state_writes:
        if write.is_hook:
            # A plugin or callback hook is handed the invocation context exactly
            # so it can set state before the run continues; ADK's own plugins do
            # this, so it is the documented shape rather than a mistake.
            continue
        add("AGT013", "warn",
            f"writes session state directly ({write.text})",
            path=write.path, locator=f"line {write.lineno}",
            fix="assign through ctx.state / tool_context.state so the change lands in an event; "
                "a direct write is not persisted and is not replayed",
            source=_SRC_STATE_DOC)

    if facts.agent_unresolved:
        root = facts.root_agent()
        add("AGT055", "info",
            "agent construction is not statically readable "
            f"({facts.agent_unresolved_reason}); model, instruction and tool checks were "
            "skipped, use load_agent.py for the loaded facts",
            path=root.file if root else None,
            locator=f"line {root.lineno}" if root else None,
            fix="nothing is necessarily wrong: this is a note that the review is partial. "
                "`load_agent.py <project>` imports the agent through ADK's own loader and "
                "reports the resolved name, model, instruction size and tools; an expected "
                "tool call is also treated as unverifiable rather than unknown while this "
                "holds",
            source="adk-python: cli/utils/agent_loader.py")

    if facts.agents and facts.app is None:
        add("AGT014", "info", "no `App` is exported",
            fix="the loader prefers `app = App(name=..., root_agent=root_agent)`, which is also "
                "how plugins, context caching and resumability reach an eval run",
            source="adk-python: apps/app.py")

    facts.findings.extend(findings)
    return findings


def cache_config_problems(config: dict[str, Any] | None) -> list[str]:
    """What `ContextCacheConfig(**config)` would reject, one phrase each.

    Only literal values are judged; an expression is left alone.
    """
    if not config:
        return []
    problems: list[str] = []
    unknown = sorted(set(config) - set(CACHE_CONFIG_BOUNDS))
    if unknown:
        problems.append(f"has no field(s) {', '.join(unknown)} (extra fields are forbidden)")
    for name, bound in CACHE_CONFIG_BOUNDS.items():
        value = config.get(name)
        if bound is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if bound[0] == "range" and not bound[1] <= value <= bound[2]:
            problems.append(f"{name}={value} is outside {bound[1]}..{bound[2]}")
        elif bound[0] == "gt" and not value > bound[1]:
            problems.append(f"{name}={value} must be greater than {bound[1]}")
        elif bound[0] == "ge" and not value >= bound[1]:
            problems.append(f"{name}={value} must be at least {bound[1]}")
    return problems


def compaction_config_problems(config: dict[str, Any] | None) -> list[str]:
    """What `EventsCompactionConfig(**config)` would reject, one phrase each.

    Only literal values are judged for bounds; the pairing rule (each trigger
    pair set together, at least one trigger) is judged on which keys are given
    a non-None value, since an expression is still a value.
    """
    if config is None:
        return []
    problems: list[str] = []
    unknown = sorted(set(config) - set(COMPACTION_CONFIG_BOUNDS))
    if unknown:
        problems.append(f"has no field(s) {', '.join(unknown)} (extra fields are forbidden)")
    for name, bound in COMPACTION_CONFIG_BOUNDS.items():
        value = config.get(name)
        if bound is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if bound[0] == "gt" and not value > bound[1]:
            problems.append(f"{name}={value} must be greater than {bound[1]}")
        elif bound[0] == "ge" and not value >= bound[1]:
            problems.append(f"{name}={value} must be at least {bound[1]}")
    given = {k for k, v in config.items() if v is not None}
    for first, second in COMPACTION_PAIRS:
        if (first in given) != (second in given):
            problems.append(f"sets {first if first in given else second} without "
                            f"{second if first in given else first} (they must be set together)")
    if not any(first in given or second in given for first, second in COMPACTION_PAIRS):
        problems.append("configures no trigger (neither compaction_interval/overlap_size nor "
                        "token_threshold/event_retention_size)")
    return problems


def _agents_for_toolset(facts: ModuleFacts, toolset: SkillToolsetSpec) -> list[AgentSpec]:
    bound = [a for a in facts.agents if a.call in LLM_AGENT_CALLS and toolset.label in a.opaque_tools]
    return bound or [a for a in facts.agents if a.call in LLM_AGENT_CALLS]


def _skill_findings(facts: ModuleFacts) -> list[Finding]:
    """AGT070-AGT073 and AGT075: the skills a SkillToolset registers."""
    found: list[Finding] = []

    def add(fid: str, severity: str, message: str, *, path: str | None, locator: str | None,
            fix: str, source: str) -> None:
        found.append(Finding(id=fid, severity=severity, area="skill", message=message,
                             path=path, locator=locator, fix=fix, source=source))

    for toolset in facts.skill_toolsets:
        locator = f"line {toolset.lineno}"
        for skill in toolset.skills:
            if skill.source in ("registry", "gcs"):
                continue
            label = skill.name or (Path(skill.path).name if skill.path else "inline skill")
            where = skill.path if skill.source == "dir" and skill.path else skill.file
            where_locator = None if skill.source == "dir" else f"line {skill.lineno}"
            if skill.problems:
                add("AGT070", "error",
                    f"skill {label!r} ({skill.source}): {'; '.join(skill.problems)}, so loading "
                    "it raises and the agent never starts",
                    path=where, locator=where_locator,
                    fix="frontmatter needs `name` (lowercase kebab-case, at most 64 "
                        "characters, equal to the skill's directory name) and `description` "
                        "(non-empty, at most 1,024 characters). snake_case names are accepted "
                        "only with the experimental SNAKE_CASE_SKILL_NAME feature enabled",
                    source=f"{_SRC_SKILL_MODELS} (Frontmatter); adk-python: skills/_utils.py")
            lines = skill.instruction_lines
            tokens = skill.instruction_tokens_estimate
            if lines > MAX_INSTRUCTION_LINES or tokens > MAX_INSTRUCTION_TOKENS:
                add("AGT071", "warn",
                    f"skill {label!r}: the instructions are {lines:,} lines (about {tokens:,} "
                    f"tokens), past the ~{MAX_INSTRUCTION_LINES} lines / ~{MAX_INSTRUCTION_TOKENS:,} "
                    "tokens a skill body should stay under",
                    path=where, locator=where_locator,
                    fix="keep SKILL.md to the procedure and move detail into references/ files; "
                        "the body is sent whole on every `load_skill`, while a reference is "
                        "fetched only when the model asks for it with `load_skill_resource`",
                    source=f"{_SRC_SKILL_TOOLSET}; agentskills.io specification")
            if skill.description and not skill.problems and skill.description_chars < MIN_DESCRIPTION_CHARS:
                add("AGT073", "info",
                    f"skill {label!r}: the description is {skill.description_chars} characters, "
                    "which rarely says both what the skill does and when to use it",
                    path=where, locator=where_locator,
                    fix="the description is the only part of a skill the model sees before "
                        "activating it; name the task, the trigger phrases and what it is not "
                        "for, so activation cases can pass",
                    source=f"{_SRC_SKILL_MODELS} (L1 metadata)")
            if skill.instructions:
                for agent in _agents_for_toolset(facts, toolset):
                    share, repeated, total = repeated_share(skill.instructions, agent.spec_text)
                    if repeated and share >= 0.4:
                        add("AGT072", "info",
                            f"the instruction of agent {agent.name or agent.var!r} already carries "
                            f"{repeated} of skill {label!r}'s {total} distinct line(s) "
                            f"({share:.0%}) verbatim",
                            path=agent.file, locator=f"line {agent.lineno}",
                            fix="pick one home for the procedure: in the skill it loads only "
                                "when needed (progressive disclosure), in the instruction it is "
                                "paid for on every request. Leave the instruction a pointer to "
                                "the skill",
                            source=_SRC_SKILL_TOOLSET)
        config = toolset.lifecycle_config or {}
        cap = config.get("max_active_skills")
        if isinstance(cap, int) and not isinstance(cap, bool) and cap < 1:
            add("AGT075", "error",
                f"SkillLifecycleConfig(max_active_skills={cap}) raises ValueError "
                "(`max_active_skills` must be at least 1)",
                path=toolset.file, locator=locator,
                fix="use 1 or more (default 5); the cap counts only BOUNDED skills, so to stop "
                    "skills piling up use BOUNDED with a small cap, or EPHEMERAL",
                source=f"{_SRC_SKILL_TOOLSET} (SkillLifecycleConfig.__post_init__)")
    return found


def skill_lifecycle_findings(
    facts: ModuleFacts, *, env_enabled: bool | None, adk_version: str | None = None,
    path: str | None = None,
) -> list[Finding]:
    """AGT074: a lifecycle config that leans on the experimental flag.

    `SkillLifecycleConfig` itself works without `ADK_ENABLE_SKILL_LIFECYCLE`:
    modes, the BOUNDED cap and the overrides all apply. The flag adds the
    `unload_skill` tool, its paragraph in the system instruction and the pruning
    of released skills' instructions from the request. `env_enabled` is what
    the agent's `.env` says (None when no .env was read).
    """
    from .env import version_at_least  # local import: env imports nothing from here

    found: list[Finding] = []
    for toolset in facts.skill_toolsets:
        config = toolset.lifecycle_config
        if config is None or config.get("enabled") is False:
            continue
        modes = {config.get("default_mode")}
        overrides = config.get("skill_overrides")
        if isinstance(overrides, dict):
            modes |= set(overrides.values())
        uses_release = bool(modes & {"BOUNDED", "EPHEMERAL"}) or bool(overrides)
        if not uses_release or env_enabled:
            continue
        version_note = ""
        if adk_version and not version_at_least(adk_version, SKILL_LIFECYCLE_SINCE):
            version_note = (f" The runner is ADK {adk_version}: SkillLifecycleConfig and "
                            f"SkillLifecycleMode only exist from {SKILL_LIFECYCLE_SINCE}, so "
                            "the import fails there.")
        found.append(Finding(
            id="AGT074", severity="info", area="skill",
            message=f"SkillToolset {toolset.label!r} releases skills "
                    f"({', '.join(sorted(m for m in modes if m)) or 'skill_overrides'}) but "
                    "ADK_ENABLE_SKILL_LIFECYCLE is not set in the agent's .env, so there is no "
                    "`unload_skill` tool and released skills' instructions are not pruned from "
                    "the request." + version_note,
            path=path or toolset.file, locator=f"line {toolset.lineno}",
            fix="the modes, the max_active_skills cap and skill_overrides work without the "
                "flag; set ADK_ENABLE_SKILL_LIFECYCLE=1 if the model should be able to unload "
                "a skill itself and released instructions should leave the prompt. It is "
                "experimental (ADK 2.10+)",
            source=f"{_SRC_SKILL_TOOLSET}; adk-python: features/_feature_registry.py "
                   "(SKILL_LIFECYCLE)",
        ))
    return found


def _sub_agent_scope_findings(facts: ModuleFacts) -> list[Finding]:
    """AGT078: a transfer target that reads the whole shared history.

    Measured on 2.12.0 (`tools/agent_tool.py`): an `AgentTool` runs its agent in
    a fresh in-memory session holding only the request (state is copied), so it
    is already scoped. A sub-agent reached by transfer runs in the caller's
    session, and with `include_contents='default'` its requests carry the
    conversation so far; 'none' sends only its instruction and the turn's input.
    """
    found: list[Finding] = []
    by_var = {a.var: a for a in facts.agents if a.var}
    reported: set[str] = set()
    for parent in facts.agents:
        # Only an LlmAgent transfers: a Sequential/Parallel/Loop agent runs its
        # children itself, and an AgentTool already gets a fresh session.
        if parent.call not in LLM_AGENT_CALLS:
            continue
        for child_var in parent.sub_agents:
            child = by_var.get(child_var)
            if child is None or child.call not in LLM_AGENT_CALLS or child.kwargs_unresolved:
                continue
            if child.include_contents not in (None, "default") or child_var in reported:
                continue
            reported.add(child_var)
            found.append(Finding(
                id="AGT078", severity="info", area="agent",
                message=f"sub-agent {child.name or child_var!r} (a transfer target of "
                        f"{parent.name or parent.var!r}) keeps include_contents='default', so "
                        "each of its requests carries the whole conversation history",
                path=child.file, locator=f"line {child.lineno}",
                fix="if it only needs the handed-over request and state, set "
                    "include_contents='none' (it then sees its instruction and the current "
                    "turn); or call it as an AgentTool, which runs it in a fresh session with "
                    "only the request. Keep 'default' where it genuinely needs the history",
                source=f"{_SRC_LLM_AGENT} (include_contents); adk-python: tools/agent_tool.py",
            ))
    return found


DUPLICATE_NAME_FIX = ("ADK never raises, but ADK 2.13 warns for every clash; `find_agent()` "
                      "returns the first match in a depth-first walk, so transfers and "
                      "lookups by name reach whichever agent comes first; give every agent "
                      "in the tree a unique name")


def agent_trees(facts: ModuleFacts) -> list[list[tuple[AgentSpec, tuple[str, ...]]]]:
    """Each agent tree reachable through `sub_agents=` by variable name, as
    (agent, ancestor-variable path) pairs in depth-first order. A root is an
    agent no other agent lists as a sub-agent."""
    by_var: dict[str, AgentSpec] = {}
    for agent in facts.agents:
        if agent.var and agent.var not in by_var:
            by_var[agent.var] = agent
    children = {var for agent in facts.agents for var in agent.sub_agents}
    roots = [a for a in facts.agents if a.var and a.var not in children and a.sub_agents]
    trees: list[list[tuple[AgentSpec, tuple[str, ...]]]] = []
    for root in roots:
        nodes: list[tuple[AgentSpec, tuple[str, ...]]] = []

        def walk(agent: AgentSpec, path: tuple[str, ...]) -> None:
            nodes.append((agent, path))
            for var in agent.sub_agents:
                child = by_var.get(var)
                if child is None or var in path or var == agent.var or len(path) > 32:
                    continue
                walk(child, path + (agent.var or "?",))

        walk(root, ())
        trees.append(nodes)
    return trees


def _duplicate_name_findings(facts: ModuleFacts) -> list[Finding]:
    """AGT015 across the whole tree: cousins and ancestor/descendant clashes.

    Measured on 2.13.0 (6031bb1d7): ADK logs "Found duplicate agent names: `x`.
    All agents in the tree must have unique names. find_agent() returns the
    first match in a depth-first walk." for siblings, cousins and an agent
    whose name reappears below it; 2.12.0 warned for siblings only. Sibling
    clashes by the same variable are reported per agent above; this reports
    distinct agents that share a `name`.
    """
    found: list[Finding] = []
    for nodes in agent_trees(facts):
        seen: dict[str, list[tuple[AgentSpec, tuple[str, ...]]]] = {}
        for agent, path in nodes:
            if agent.name:
                seen.setdefault(agent.name, []).append((agent, path))
        for name, entries in sorted(seen.items()):
            distinct = {id(agent) for agent, _ in entries}
            if len(distinct) < 2:
                continue
            (first, first_path), (second, second_path) = entries[0], entries[1]
            if first.var in second_path or second.var in first_path:
                relation = "an ancestor and its descendant"
            elif first_path and second_path and first_path[-1] == second_path[-1]:
                relation = "siblings"
            else:
                relation = "cousins"
            root = nodes[0][0]
            found.append(Finding(
                id="AGT015", severity="warn", area="agent",
                message=f"agent name {name!r} is used by {len(distinct)} agents in the tree "
                        f"under {root.name or root.var!r} ({relation}: "
                        + ", ".join(sorted({a.var or '?' for a, _ in entries})) + ")",
                path=second.file, locator=f"line {second.lineno}",
                fix=DUPLICATE_NAME_FIX,
                source=f"{_SRC_BASE_AGENT} (validate_sub_agents_unique_names)",
            ))
    return found


def agent_model_ids(facts: ModuleFacts, env_model: str | None = None) -> list[str] | None:
    """The model ids the agent's LLM agents run on, or None when any of them is
    not statically readable. A model read from the environment resolves to
    `env_model` (MODEL_NAME from the agent's .env) when given, else to the
    code's fallback."""
    ids: list[str] = []
    for agent in facts.agents:
        if agent.call not in LLM_AGENT_CALLS:
            continue
        if agent.model_from_env and env_model:
            model = env_model
        else:
            model = agent.model
        if model is None:
            if "model" in agent.kwargs or agent.kwargs_unresolved:
                return None
            continue                       # inherits its parent's model
        if model not in ids:
            ids.append(model)
    return ids


#: Tools Gemini runs itself. Each reaches the request as a `types.Tool` with
#: no `function_declarations` (google_search, retrieval, code_execution,
#: url_context ...), which is what the 2.13.0 multi-turn trajectory judge
#: cannot iterate (CFG076). `exit_loop`, `load_memory` and the other ready-made
#: objects in OPAQUE_TOOL_NAMES are ordinary function tools and not listed.
MODEL_BUILTIN_TOOL_NAMES = {
    "google_search", "built_in_code_execution", "url_context", "enterprise_web_search",
    "google_maps_grounding",
}
MODEL_BUILTIN_TOOL_CALLS = {
    "GoogleSearchTool", "VertexAiSearchTool", "UrlContextTool", "BuiltInCodeExecutionTool",
    "EnterpriseWebSearchTool", "GoogleMapsGroundingTool",
}


def builtin_model_tools(facts: ModuleFacts) -> list[str]:
    """Built-in (model-executed) tools bound to any agent in the project, plus
    `code_execution` for an agent with `code_executor=BuiltInCodeExecutor()`."""
    found: set[str] = set()
    for agent in facts.agents:
        for name in agent.opaque_tools:
            if name in MODEL_BUILTIN_TOOL_NAMES or name in MODEL_BUILTIN_TOOL_CALLS:
                found.add(name)
    uses_executor = any(name.rsplit(".", 1)[-1] == "BuiltInCodeExecutor" for name in facts.imports)
    if uses_executor and any("code_executor" in agent.kwargs for agent in facts.agents):
        found.add("code_execution")
    return sorted(found)


# ---------------------------------------------------------------------------
# session state keys: who writes them, who reads them (AGT080, EVS075)
# ---------------------------------------------------------------------------

#: State-key prefixes with their own scope; `{user:name}` reads key "user:name".
STATE_PREFIXES = ("app:", "user:", "temp:")
#: The file `adk web` (2.13+) seeds every new session from.
INITIAL_STATE_FILE = "initial_state.json"
_STATE_OWNERS = ("state", "state_delta")


def bare_state_key(key: str) -> str:
    """`user:claimant` -> `claimant`; other keys unchanged."""
    for prefix in STATE_PREFIXES:
        if key.startswith(prefix):
            return key[len(prefix):]
    return key


def instruction_placeholders(text: str | None) -> list[tuple[str, bool]]:
    """(key, optional) for each state placeholder ADK would fill in `text`.

    Mirrors `utils/instructions_utils.inject_session_state` (2.9.2 and 2.13.0
    alike): `{artifact.x}` is an artifact; a name that is not an identifier,
    optionally behind one `app:`/`user:`/`temp:` prefix (`{a.b}`), is left in
    the text untouched; `{key?}` becomes "" when the key is missing; `{key}`
    raises KeyError("Context variable not found: `key` ...").
    """
    found: list[tuple[str, bool]] = []
    for match in PLACEHOLDER_RE.finditer(text or ""):
        inner = match.group()[1:-1]
        optional = inner.endswith("?")
        key = inner.rstrip("?")
        if key.startswith("artifact."):
            continue
        parts = key.split(":")
        if len(parts) == 1:
            valid = key.isidentifier()
        else:
            valid = len(parts) == 2 and f"{parts[0]}:" in STATE_PREFIXES and parts[1].isidentifier()
        if valid and (key, optional) not in found:
            found.append((key, optional))
    return found


@dataclass
class StateKeys:
    """Literal state keys the agent's source writes and reads."""

    written: dict[str, str] = field(default_factory=dict)   # key -> "file:line how"
    read: set[str] = field(default_factory=set)
    #: A write whose key is not a literal (`state[key] = ...`, `state.update(d)`):
    #: any key may be written, so "never written" cannot be claimed.
    dynamic_writes: list[str] = field(default_factory=list)
    #: The same for reads (`state[key]`, `state.get(name)`, `state.to_dict()`).
    dynamic_reads: list[str] = field(default_factory=list)

    def writes(self, key: str) -> bool:
        return key in self.written or bare_state_key(key) in {
            bare_state_key(k) for k in self.written}

    def reads(self, key: str) -> bool:
        bare = bare_state_key(key)
        return key in self.read or bare in {bare_state_key(k) for k in self.read}


def _owner_is_state(node: ast.AST | None) -> bool:
    text = _unparse(node) or ""
    return any(text == owner or text.endswith(f".{owner}") for owner in _STATE_OWNERS)


def _key_literal(node: ast.AST | None, constants: dict[str, str]) -> str | None:
    value = _literal(node)
    if isinstance(value, str):
        return value
    if isinstance(node, ast.Name) and node.id in constants:
        return constants[node.id]
    return None


def collect_state_keys(files: Iterable[Path | str],
                       constants: dict[str, str] | None = None) -> StateKeys:
    """Every literal state key written or read in these Python files.

    Writes: `x.state["k"] = ...` (also `+=`, annotated), `x.state.update({"k":
    ...})` / `update(k=...)`, `x.state.setdefault("k", ...)`, `state_delta=` /
    `state=` keyword dicts (`EventActions`, `create_session`), and
    `output_key="k"` on any call. Reads: `x.state["k"]` loads, `x.state.get("k")`
    / `.pop`, `"k" in x.state`, and any string constant holding a `{k}` /
    `{k?}` placeholder (an instruction, wherever it is defined).
    """
    keys = StateKeys()
    constants = constants or {}
    for path in files:
        try:
            tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
            continue
        name = Path(path).name

        def wrote(key: str | None, node: ast.AST, how: str) -> None:
            if key is None:
                keys.dynamic_writes.append(f"{name}:{getattr(node, 'lineno', 0)}")
            else:
                keys.written.setdefault(key, f"{name}:{getattr(node, 'lineno', 0)} {how}")

        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Subscript) and _owner_is_state(target.value):
                        wrote(_key_literal(target.slice, constants), node, "assignment")
            elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) \
                    and _owner_is_state(node.value):
                key = _key_literal(node.slice, constants)
                if key is None:
                    keys.dynamic_reads.append(f"{name}:{node.lineno}")
                else:
                    keys.read.add(key)
            elif isinstance(node, ast.Compare) and _owner_is_state(node.comparators[0]) \
                    and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
                key = _key_literal(node.left, constants)
                if key is not None:
                    keys.read.add(key)
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and _owner_is_state(func.value):
                    first = node.args[0] if node.args else None
                    if func.attr == "update":
                        if isinstance(first, ast.Dict) and all(k is not None for k in first.keys):
                            for k in first.keys:
                                wrote(_key_literal(k, constants), node, "state.update")
                        elif first is not None:
                            wrote(None, node, "state.update")
                        for kw in node.keywords:
                            wrote(kw.arg, node, "state.update")
                    elif func.attr in ("setdefault", "__setitem__"):
                        wrote(_key_literal(first, constants), node, f"state.{func.attr}")
                    elif func.attr in ("get", "pop", "__getitem__", "__contains__"):
                        key = _key_literal(first, constants)
                        if key is None:
                            keys.dynamic_reads.append(f"{name}:{node.lineno}")
                        else:
                            keys.read.add(key)
                    elif func.attr in ("to_dict", "items", "keys", "values"):
                        keys.dynamic_reads.append(f"{name}:{node.lineno}")
                for kw in node.keywords:
                    if kw.arg == "output_key":
                        key = _key_literal(kw.value, constants)
                        if key:
                            keys.written.setdefault(
                                key, f"{name}:{node.lineno} output_key")
                    elif kw.arg in ("state_delta", "state") and isinstance(kw.value, ast.Dict):
                        for k in kw.value.keys:
                            wrote(_key_literal(k, constants) if k is not None else None,
                                  node, kw.arg)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                for key, _ in instruction_placeholders(node.value):
                    keys.read.add(key)
    return keys


def seeded_state_keys(docs: Iterable[Any], agent_dir: Path | str | None = None) -> set[str]:
    """Keys an evalset's `session_input.state` seeds, plus the keys of the
    agent's `initial_state.json` (read by `adk web` from 2.13)."""
    seeded: set[str] = set()
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        for case in doc.get("eval_cases") or []:
            if not isinstance(case, dict):
                continue
            session_input = case.get("session_input")
            state = session_input.get("state") if isinstance(session_input, dict) else None
            if isinstance(state, dict):
                seeded.update(str(k) for k in state)
    if agent_dir:
        initial = Path(agent_dir) / INITIAL_STATE_FILE
        try:
            data = json.loads(initial.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            data = None
        if isinstance(data, dict):
            seeded.update(str(k) for k in data)
    return seeded


def state_placeholder_findings(facts: ModuleFacts, docs: Iterable[Any],
                               agent_dir: Path | str | None = None) -> list[Finding]:
    """AGT080: an instruction placeholder `{key}` nothing ever puts in state.

    Measured on 2.9.2 and 2.13.0: building the instruction raises
    `KeyError: "Context variable not found: <key> in agent '<name>'."` when a
    required placeholder's key is not in session state. Skipped when any write
    in the agent is not a literal key (it might write anything) and for
    instructions built at runtime.
    """
    keys = collect_state_keys(facts.files, facts.str_constants)
    if keys.dynamic_writes:
        return []
    seeded = seeded_state_keys(list(docs), agent_dir)
    seeded_bare = {bare_state_key(k) for k in seeded}
    found: list[Finding] = []
    reported: set[str] = set()
    for agent in facts.agents:
        if agent.call not in LLM_AGENT_CALLS or agent.instruction_is_dynamic:
            continue
        for key, optional in instruction_placeholders(agent.instruction_text):
            if optional or key in reported or keys.writes(key):
                continue
            if key in seeded or bare_state_key(key) in seeded_bare:
                continue
            reported.add(key)
            found.append(Finding(
                id="AGT080", severity="warn", area="agent",
                message=f"placeholder `{{{key}}}` is never written to state: ADK raises "
                        f"KeyError (\"Context variable not found: `{key}`\") when it builds "
                        f"the instruction of {agent.name or agent.var!r} unless the key is in "
                        f"session state; write it from a tool/callback, seed it, or use "
                        f"`{{{key}?}}`",
                path=agent.file, locator=f"line {agent.lineno}",
                fix=f"set it in a before_agent_callback (`callback_context.state[{key!r}] = "
                    "...`), from a tool (`tool_context.state[...]`), with another agent's "
                    "`output_key`, or seed it in every evalset's `session_input.state`; "
                    f"`{{{key}?}}` renders an empty string when it is missing",
                source="adk-python: utils/instructions_utils.py (inject_session_state)",
            ))
    return found


def _invocation_count(case: dict[str, Any]) -> int:
    conversation = case.get("conversation")
    return len(conversation) if isinstance(conversation, list) else 0


def compaction_suite_findings(
    facts: ModuleFacts, evalset_docs: Iterable[Any], *, path: str | None = None,
) -> list[Finding]:
    """AGT077: long conversations in the suite and no events compaction.

    Needs the evalsets, so discovery and the linter call it where they have both.
    """
    if not facts.agents or any(app.events_compaction_set for app in facts.apps):
        return []
    long_cases: list[str] = []
    for doc in evalset_docs:
        if not isinstance(doc, dict):
            continue
        for case in doc.get("eval_cases") or []:
            if not isinstance(case, dict):
                continue
            if case.get("conversation_scenario") or \
                    _invocation_count(case) >= LONG_CONVERSATION_INVOCATIONS:
                long_cases.append(str(case.get("eval_id") or "?"))
    if not long_cases:
        return []
    app = facts.app
    shown = ", ".join(long_cases[:4]) + (" ..." if len(long_cases) > 4 else "")
    return [Finding(
        id="AGT077", severity="info", area="agent",
        message=f"{len(long_cases)} eval case(s) run long conversations ({shown}: "
                f"{LONG_CONVERSATION_INVOCATIONS}+ invocations or a user-simulation scenario) "
                "and no App sets events_compaction_config, so every request re-sends the "
                "full history",
        path=path or (app.file if app else None),
        locator=f"line {app.lineno}" if app else None,
        fix="set `App(events_compaction_config=EventsCompactionConfig(compaction_interval=N, "
            "overlap_size=M))` (a sliding-window summary every N invocations), or the token "
            "trigger pair; then compare prompt tokens per invocation before and after with "
            "compare_runs.py",
        source=f"{_SRC_COMPACTION}; adk-docs: docs/context/compaction.md",
    )]


def _case_requests_more_than_once(case: dict[str, Any]) -> bool:
    """Can this eval case make a second model request in its session?

    Two invocations do; so does a user-simulation scenario; so does one
    invocation that expects a tool call, because the tool result goes back to
    the model in a second request. Only a single turn answered straight away
    is a session with exactly one request.
    """
    if case.get("conversation_scenario"):
        return True
    conversation = case.get("conversation")
    if not isinstance(conversation, list):
        return False
    if len(conversation) > 1:
        return True
    for invocation in conversation:
        data = invocation.get("intermediate_data") if isinstance(invocation, dict) else None
        if isinstance(data, dict) and data.get("tool_uses"):
            return True
    return False


_SRC_STREAMING = ("adk-python: flows/llm_flows/tools/_agent_tools.py "
                  "(mark_live_async_tools_non_blocking, run_live only); tools/function_tool.py "
                  "(_run_generator_as_node, 2.12+); adk-docs: docs/live/tools.md")


def _agent_is_live(agent: AgentSpec, facts: ModuleFacts, env_model: str | None,
                   _seen: frozenset[int] = frozenset()) -> bool | None:
    """True for a live agent, False for a text one, None when unknowable.

    An agent with no `model=` inherits its parent's, so the parent decides.
    """
    if agent.model_is_cascade:
        return True
    model = env_model if (agent.model_from_env and env_model) else (agent.model or agent.model_constant)
    if model:
        return is_live_model_id(model)
    if "model" in agent.kwargs or agent.kwargs_unresolved or id(agent) in _seen:
        return None
    for parent in facts.agents:
        if agent.var and agent.var in parent.sub_agents:
            return _agent_is_live(parent, facts, env_model, _seen | {id(agent)})
    return None


def streaming_tool_findings(facts: ModuleFacts, configs: Iterable[Any] = (), *,
                            env_model: str | None = None) -> list[Finding]:
    """AGT081: a streaming tool (async generator) reached outside `run_live`.

    ADK marks an async-generator tool NON_BLOCKING, and streams each `yield` as
    a partial response, only when the invocation has a live request queue,
    i.e. under `run_live`. `adk run`, `adk eval` without `live_model_config`
    and text `adk web` all use `run_async`, where 2.12+ runs the generator as a
    workflow node (the second `yield` raises `Output already set`) and 2.9.2
    never iterates it (the model receives the generator object). A text agent
    gets a warning; a live agent an info, unless an eval config next to it
    already sets `live_model_config`.
    """
    findings: list[Finding] = []
    live_eval = any(isinstance(doc, dict) and doc.get("live_model_config") is not None
                    for doc in configs)
    bound = facts.bound_tool_names
    for agent in facts.agents:
        if agent.call not in LLM_AGENT_CALLS:
            continue
        streaming = []
        for name in agent.tools:
            binding = resolve_tool(facts, name)
            if binding.status == "defined" and binding.spec is not None and binding.spec.is_streaming:
                streaming.append(binding.spec)
        if not streaming:
            continue
        live = _agent_is_live(agent, facts, env_model)
        if live is None:
            continue
        who = f"{agent.call} {agent.name!r}" if agent.name else f"{agent.call}({agent.var or 'inline'})"
        model = ("CascadeLive(...)" if agent.model_is_cascade
                 else agent.model or agent.model_constant or "inherited")
        for tool in streaming:
            yields = f"{tool.yield_count} `yield`" + ("s" if tool.yield_count != 1 else "")
            if not live:
                findings.append(Finding(
                    id="AGT081", severity="warn", area="agent",
                    message=(
                        f"{who}: tool {tool.name!r} is a streaming tool (an async generator, "
                        f"{yields}) on a text model ({model}), so it is only ever reached "
                        "through `run_async` (`adk run`, `adk eval` without "
                        "`live_model_config`, text `adk web`); ADK streams an async-generator "
                        "tool only under `run_live`. On ADK 2.12+ a second `yield` aborts the "
                        "run (`ValueError: Output already set`); on 2.9.2 the body never runs "
                        "and the model receives the generator object"),
                    path=tool.file or agent.file, locator=f"line {tool.lineno}",
                    fix="for a text agent make the tool a plain `async def` that returns one "
                        "result; to stream progress, give the agent a Live API model "
                        "(`gemini-3.8-live`, or `CascadeLive(...)`) and run it with `run_live`",
                    source=_SRC_STREAMING))
            elif not live_eval:
                stop = "" if "stop_streaming" in bound else (
                    "; also bind a `stop_streaming(function_name: str)` tool so the model "
                    "can cancel the stream")
                findings.append(Finding(
                    id="AGT081", severity="info", area="agent",
                    message=(
                        f"{who}: tool {tool.name!r} is a streaming tool on a live model "
                        f"({model}); it streams only under `run_live`, so evals of this agent "
                        "need `live_model_config` in the eval config, otherwise `adk eval` "
                        "runs it through `run_async`, where a second `yield` aborts the run "
                        "on ADK 2.12+ (`ValueError: Output already set`) and on 2.9.2 the "
                        "body never runs"),
                    path=tool.file or agent.file, locator=f"line {tool.lineno}",
                    fix="set `\"live_model_config\": {}` in the eval config that grades this "
                        "agent" + stop,
                    source=_SRC_STREAMING))
    return findings


def cache_suite_findings(
    facts: ModuleFacts, evalset_docs: Iterable[Any], *, path: str | None = None,
) -> list[Finding]:
    """AGT063: context caching is on, and no eval case can show it working.

    ADK creates no cache on a session's first model request: the first request
    only records a fingerprint, and the cache is built when a later request in
    the same session repeats the prefix (`flows/llm_flows/context/_cache.py`,
    `models/gemini_context_cache_manager.py`). A suite whose every case is one
    turn with no tool call makes one request per session, so `token_usage_v1`
    can never report `cached_tokens` and the efficiency numbers say nothing
    about the cache. Needs both the agent facts and the evalsets, so discovery
    and the linter call it where they have both.
    """
    app = next((a for a in facts.apps if a.context_cache_set), None)
    if app is None:
        return []
    cases = [
        case for doc in evalset_docs if isinstance(doc, dict)
        for case in (doc.get("eval_cases") or []) if isinstance(case, dict)
    ]
    if not cases or any(_case_requests_more_than_once(case) for case in cases):
        return []
    return [Finding(
        id="AGT063", severity="info", area="agent",
        message=f"context caching is enabled on App {app.name or app.var!r}, but all "
                f"{len(cases)} eval case(s) are a single turn with no expected tool call, "
                "so each session makes one model request and the suite cannot show a "
                "cache hit",
        path=path or app.file, locator=f"line {app.lineno}",
        fix="add a multi-turn case (two or more invocations in `conversation`, or a "
            "`conversation_scenario`): ADK only records a fingerprint on a session's first "
            "request and builds the cache on a later one, and `token_usage_v1` reports "
            "`cached_tokens` once it is reused",
        source=f"{_SRC_CACHE_CONFIG}; adk-python: flows/llm_flows/context/_cache.py",
    )]


def _tool_findings(tool: ToolSpec, models: dict[str, ModelSpec] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    locator = f"line {tool.lineno}"

    def add(fid: str, severity: str, message: str, fix: str, source: str) -> None:
        findings.append(Finding(id=fid, severity=severity, area="tool",
                                message=f"tool {tool.name!r}: {message}",
                                path=tool.file, locator=locator, fix=fix, source=source))

    if not tool.docstring:
        add("AGT040", "error", "no docstring",
            "the docstring is the entire description the model gets; say what the tool does, "
            "when to call it, what each argument means and what comes back",
            _SRC_FUNCTION_TOOL)
    else:
        if len(tool.docstring.strip()) < 40:
            add("AGT041", "warn", "docstring is too short to disambiguate",
                "an ambiguous description is the usual cause of the wrong tool being called",
                _SRC_FUNCTION_DOC)
        if tool.schema_params and not any(
            marker in tool.docstring for marker in ("Args:", "Arguments:", "Parameters:", ":param")
        ):
            add("AGT042", "warn", "docstring does not document its arguments",
                "add an Args: section; the model fills arguments from this text",
                _SRC_FUNCTION_DOC)

    for param in tool.schema_params:
        if not param.annotation:
            add("AGT043", "error", f"parameter {param.name!r} has no type annotation",
                "ADK builds the function declaration from the type hints; an unannotated "
                "parameter gives the model nothing to validate against",
                _SRC_FUNCTION_TOOL)

    if tool.raises:
        add("AGT044", "warn", f"raises {', '.join(sorted(set(tool.raises)))} instead of returning an error",
            "return a dict such as {\"status\": \"error\", \"error\": \"...\"}; a returned error "
            "becomes an observation the model can react to and correct, while an exception "
            "ends the invocation",
            _SRC_FUNCTION_DOC)

    returns_model = _annotation_is_model(tool.returns, models or {})
    # A streaming tool's annotation is the generator type; its yields are the
    # partial responses, so "not a dict" says nothing useful about it.
    if not tool.returns_dict and not returns_model and tool.returns \
            and not tool.is_streaming and not tool.returns.startswith(("str", "Str")):
        add("AGT045", "info", f"returns {tool.returns}, not a dict",
            "a dict with a `status` key gives judges and groundedness checks something to read",
            _SRC_FUNCTION_DOC)
    elif tool.returns_dict and tool.dict_keys and "status" not in tool.dict_keys:
        add("AGT046", "info", "returned dict has no `status` key",
            "a status field makes success and failure explicit for the model and for rubric "
            "and groundedness metrics",
            _SRC_FUNCTION_DOC)

    if tool.large_return_chars > LARGE_TOOL_RETURN_CHARS:
        add("AGT079", "warn",
            f"returns a {tool.large_return_chars:,}-character string literal, which lands in "
            "the chat history and is re-sent with every later request",
            "return a short summary plus a reference instead: save the bulk as an artifact "
            "(`tool_context.save_artifact(...)`) and let the agent fetch it with "
            "`load_artifacts` when it needs it, or keep reference text in a skill resource",
            "adk-docs: docs/artifacts/index.md; adk-python: tools/load_artifacts_tool.py")

    if tool.blocking_calls and not tool.is_async:
        add("AGT047", "warn",
            f"synchronous blocking call(s): {', '.join(sorted(set(tool.blocking_calls)))}",
            "a sync tool is awaited inline, so a blocking call freezes the whole run; make the "
            "tool async, or move the work off the event loop",
            _SRC_FUNCTION_DOC)

    return findings


def _annotation_is_model(annotation: str | None, models: dict[str, ModelSpec]) -> bool:
    """Is this return annotation a Pydantic model the project defines?

    A model serializes to a JSON object with named fields, which is exactly what
    a judge or a groundedness check needs; asking for a bare dict instead would
    be a downgrade.
    """
    if not annotation:
        return False
    base = annotation.replace("Optional[", "").replace("list[", "").replace("List[", "")
    base = base.rstrip("]").split("|")[0].strip().split(".")[-1]
    return base in models


def tool_param_constraints(tool: ToolSpec, models: dict[str, ModelSpec]) -> dict[str, FieldSpec]:
    """Flatten a tool's argument constraints into the shape an eval case writes.

    A tool that takes a single Pydantic model, ``def submit(req: ClaimRequest)``,
    is called with arguments nested under that parameter name, so the keys here
    are dotted: ``req.claim_id``.
    """
    flat: dict[str, FieldSpec] = {}
    for param in tool.schema_params:
        if param.model and param.model in models:
            for spec in models[param.model].fields:
                flat[f"{param.name}.{spec.name}"] = spec
        else:
            flat[param.name] = FieldSpec(
                name=param.name, annotation=param.annotation, required=not param.has_default
            )
    return flat
