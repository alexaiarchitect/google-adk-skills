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
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

from . import DEPRECATED_MODELS
from .env import classify_model_id
from .report import Finding

#: Constructors that build an LLM agent.
LLM_AGENT_CALLS = {"Agent", "LlmAgent"}
#: Workflow shells superseded by `google.adk.workflow.Workflow` in ADK 2.x.
DEPRECATED_AGENT_CALLS = {"SequentialAgent", "ParallelAgent", "LoopAgent"}
AGENT_CALLS = LLM_AGENT_CALLS | DEPRECATED_AGENT_CALLS | {"Workflow"}

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

_SRC_LLM_AGENT = "adk-python: agents/llm_agent.py"
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
    lineno: int = 0
    file: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("instruction_text", None)
        return data


@dataclass
class AppSpec:
    var: str
    name: str | None = None
    plugins: list[str] = field(default_factory=list)
    root_agent: str | None = None
    kwargs: list[str] = field(default_factory=list)
    lineno: int = 0
    file: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    def tools_unresolved(self) -> bool:
        """Did any agent bind tools through an expression we could not read?"""
        return any(agent.tools_unresolved for agent in self.agents)

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
            "tool_collisions": self.tool_collisions,
            "state_writes": [w.to_dict() for w in self.state_writes],
            "syntax_errors": self.syntax_errors,
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
    return spec


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

    instruction_node = _kwarg(call, "instruction")
    if instruction_node is not None:
        spec.has_instruction = True
        text = _string_value(instruction_node, constants)
        if text:
            spec.instruction_text = text
            spec.instruction_len = len(text)
        elif _is_instruction_provider(instruction_node, facts):
            spec.instruction_is_dynamic = True

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
    return spec


def _collect_app(var: str, call: ast.Call) -> AppSpec:
    spec = AppSpec(var=var, lineno=call.lineno)
    spec.name = _literal(_kwarg(call, "name"))
    spec.plugins = _list_names(_kwarg(call, "plugins"))
    root = _kwarg(call, "root_agent")
    spec.root_agent = _name_of(root) or _unparse(root)
    spec.kwargs = [kw.arg for kw in call.keywords if kw.arg]
    return spec


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
                app = _collect_app(var, value)
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
                app = _collect_app("app", node.value)
                app.file = file_key
                facts.apps.append(app)

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

        misplaced = sorted(set(agent.kwargs) & MISPLACED_MODEL_KWARGS)
        if misplaced:
            add("AGT016", "error",
                f"{', '.join(misplaced)} passed straight to {agent.call}, which raises at construction",
                path=agent.file, locator=locator,
                fix="move them into generate_content_config=types.GenerateContentConfig(...)",
                source=_SRC_LLM_AGENT)

        if agent.call in LLM_AGENT_CALLS:
            if not agent.has_instruction and not agent.kwargs_unresolved:
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
                fix="ADK only warns, but routing by name becomes ambiguous",
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

    if facts.agents and facts.app is None:
        add("AGT014", "info", "no `App` is exported",
            fix="the loader prefers `app = App(name=..., root_agent=root_agent)`, which is also "
                "how plugins, context caching and resumability reach an eval run",
            source="adk-python: apps/app.py")

    facts.findings.extend(findings)
    return findings


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
    if not tool.returns_dict and not returns_model and tool.returns \
            and not tool.returns.startswith(("str", "Str")):
        add("AGT045", "info", f"returns {tool.returns}, not a dict",
            "a dict with a `status` key gives judges and groundedness checks something to read",
            _SRC_FUNCTION_DOC)
    elif tool.returns_dict and tool.dict_keys and "status" not in tool.dict_keys:
        add("AGT046", "info", "returned dict has no `status` key",
            "a status field makes success and failure explicit for the model and for rubric "
            "and groundedness metrics",
            _SRC_FUNCTION_DOC)

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
