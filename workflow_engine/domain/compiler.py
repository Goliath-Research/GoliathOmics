"""
Lower DomainProgram IR to WorkflowDefinitionSpec + context_json.

Supports program v1 (phases/steps) and v2 (statement body with for/parallel/do).
Emits collection_bindings for project.* references resolved by the engine at instance start.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from methyl_domain.program import (
    ActionStep,
    AssignStep,
    CollectionInSpec,
    DomainProgram,
    ForeachSpec,
    ForeachStep,
    IfStep,
    ParallelStep,
    RepeatStep,
    SwitchStep,
    WaitStep,
    WhileStep,
)

_CONTRACT = Path(__file__).resolve().parents[1] / "contract"
if str(_CONTRACT) not in sys.path:
    sys.path.insert(0, str(_CONTRACT))

from workflow_definition_spec import (  # noqa: E402
    CollectionBindingSpec,
    EventTriggerSpec as CompiledEventTriggerSpec,
    WorkflowDefinitionSpec,
    WorkflowEdgeSpec,
    WorkflowInputBindingSpec,
    WorkflowNodeSpec,
    WorkflowOutputBindingSpec,
    WorkflowScopeDefaultSpec,
)

_WORKERS = Path(__file__).resolve().parents[2] / "workers"
if str(_WORKERS) not in sys.path:
    sys.path.insert(0, str(_WORKERS))

from methyl_worker.action_catalog import find_catalog_entry  # noqa: E402

from workflow_context import enrich_instance_context  # noqa: E402


@dataclass
class CompileResult:
    workflow: WorkflowDefinitionSpec
    context_json: Dict[str, Any]


# Exact schemaRef strings for typed workflow.compute assign actions (MVP plug check).
_ASSIGN_ACTION_SCHEMA_REF: Dict[str, str] = {
    "workflow.const_bool": "schemas/vars/bool.schema.json",
    "workflow.const_int": "schemas/vars/int.schema.json",
    "workflow.const_string": "schemas/vars/string.schema.json",
    "workflow.const_path": "schemas/vars/path.schema.json",
    "workflow.json_path_bool": "schemas/vars/bool.schema.json",
    "workflow.json_path_int": "schemas/vars/int.schema.json",
    "workflow.json_path_string": "schemas/vars/string.schema.json",
    "workflow.fs_stat": "schemas/vars/bool.schema.json",
}


@dataclass
class _CompileCtx:
    nodes: List[WorkflowNodeSpec] = field(default_factory=list)
    edges: List[WorkflowEdgeSpec] = field(default_factory=list)
    output_bindings: List[WorkflowOutputBindingSpec] = field(default_factory=list)
    scope_defaults: List[WorkflowScopeDefaultSpec] = field(default_factory=list)
    input_bindings: List[WorkflowInputBindingSpec] = field(default_factory=list)
    collection_bindings: List[CollectionBindingSpec] = field(default_factory=list)
    declared_vars: Set[str] = field(default_factory=set)
    variable_schema_refs: Dict[str, str] = field(default_factory=dict)
    assign_targets_in_parallel: Set[str] = field(default_factory=set)
    foreach_bound_names: Set[str] = field(default_factory=set)
    _counter: int = 0

    def fresh_key(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}_{self._counter}"

def _parse_step(
    raw: Any,
) -> Union[
    ActionStep, AssignStep, IfStep, SwitchStep, WhileStep, RepeatStep, ForeachStep, ParallelStep, WaitStep
]:
    if isinstance(
        raw,
        (ActionStep, AssignStep, IfStep, SwitchStep, WhileStep, RepeatStep, ForeachStep, ParallelStep, WaitStep),
    ):
        return raw
    if not isinstance(raw, dict):
        raise ValueError(f"unsupported program step: {raw!r}")
    if "assign" in raw:
        return AssignStep.model_validate(raw)
    if "parallel" in raw and isinstance(raw.get("parallel"), list):
        return ParallelStep.model_validate(raw)
    if "for" in raw or "foreach" in raw:
        return ForeachStep.model_validate(raw)
    if "switch" in raw:
        return SwitchStep.model_validate(raw)
    if "while" in raw:
        return WhileStep.model_validate(raw)
    if "repeat" in raw:
        return RepeatStep.model_validate(raw)
    if "if" in raw:
        return IfStep.model_validate(raw)
    if "wait" in raw:
        return WaitStep.model_validate(raw)
    if "do" in raw or "action" in raw:
        return ActionStep.model_validate(raw)
    raise ValueError(f"unsupported program step keys: {list(raw)}")


def _condition_var_from_expr(expr: str) -> str:
    expr = expr.strip()
    m = re.match(r"^\$\{(.+)\}$", expr)
    inner = m.group(1) if m else expr
    if "==" in inner:
        return inner.split("==", 1)[0].strip()
    return inner.strip()


def _ref_to_var(ref: str) -> str:
    """Map a program ref to a scope variable name for ``${var.<name>}`` templates."""
    if ref.startswith("${") and ref.endswith("}"):
        return ref[2:-1].replace("var.", "")
    # FOREACH item refs (iteration.runDir) resolve via nested scope: ${var.iteration.runDir}
    if ref.startswith("iteration."):
        return ref
    parts = ref.split(".")
    return parts[-1]


def _placeholder_for_value(value: Any) -> Any:
    if isinstance(value, dict):
        if "ref" in value:
            return f"${{var.{_ref_to_var(str(value['ref']))}}}"
        return {k: _placeholder_for_value(v) for k, v in value.items()}
    if isinstance(value, str):
        if value.startswith("${"):
            return value
        return value
    return value


def _collect_project_binding(ref: str, bindings: Dict[str, CollectionBindingSpec]) -> None:
    if not ref.startswith("project."):
        return
    field_name = ref.split(".", 1)[1]
    if field_name == "project":
        return
    bindings.setdefault(
        "project",
        CollectionBindingSpec(
            scope_var="project",
            kind="jsonFile",
            path_var="projectPath",
            bind_order=0,
        ),
    )
    bindings[field_name] = CollectionBindingSpec(
        scope_var=field_name,
        kind="jsonPath",
        base_var="project",
        json_path=f"$.{field_name}",
        bind_order=len(bindings),
    )


def _collect_bindings_from_spec(spec: ForeachSpec, bindings: Dict[str, CollectionBindingSpec]) -> None:
    if isinstance(spec.in_, CollectionInSpec):
        if spec.in_.ref:
            _collect_project_binding(spec.in_.ref, bindings)
        return
    if isinstance(spec.in_, str) and spec.in_.startswith("project."):
        _collect_project_binding(spec.in_, bindings)


def _collect_bindings_from_steps(steps: List[Any], bindings: Dict[str, CollectionBindingSpec]) -> None:
    for raw in steps:
        step = _parse_step(raw)
        if isinstance(step, ForeachStep):
            _collect_bindings_from_spec(step.spec(), bindings)
            _collect_bindings_from_steps(step.body(), bindings)
        elif isinstance(step, IfStep):
            _collect_bindings_from_steps(step.then, bindings)
            _collect_bindings_from_steps(step.else_, bindings)
        elif isinstance(step, SwitchStep):
            for case_steps in step.cases.values():
                _collect_bindings_from_steps(case_steps, bindings)
            _collect_bindings_from_steps(step.default, bindings)
        elif isinstance(step, (WhileStep, RepeatStep)):
            _collect_bindings_from_steps(step.steps(), bindings)
        elif isinstance(step, ParallelStep):
            _collect_bindings_from_steps(step.parallel, bindings)


def _build_collection_bindings(program: DomainProgram) -> List[CollectionBindingSpec]:
    bindings: Dict[str, CollectionBindingSpec] = {}
    bindings["project"] = CollectionBindingSpec(
        scope_var="project",
        kind="jsonFile",
        path_var="projectPath",
        bind_order=0,
    )
    if program.body:
        _collect_bindings_from_steps(program.body, bindings)
    for phase in program.phases:
        if phase.foreach:
            _collect_bindings_from_spec(phase.foreach, bindings)
        _collect_bindings_from_steps(phase.steps, bindings)
    ordered = sorted(bindings.values(), key=lambda b: (b.bind_order, b.scope_var))
    for i, b in enumerate(ordered):
        b.bind_order = i
    return ordered


def _group_ref_side(ref: Any) -> Optional[str]:
    """Return 'control' or 'disease' when step.with group references comparison sides."""
    if isinstance(ref, dict) and "ref" in ref:
        ref_s = str(ref["ref"])
    elif isinstance(ref, str):
        ref_s = ref
    else:
        return None
    if "control_group" in ref_s:
        return "control"
    if "disease_group" in ref_s:
        return "disease"
    return None


def _apply_catalog_template_rules(
    template: Dict[str, Any],
    entry: Any,
    params: Dict[str, Any],
) -> None:
    """Apply process-pack template metadata from catalog ``domain_effects`` (no action_name switch)."""
    effects = getattr(entry, "domain_effects", None)
    if effects is None:
        return
    for rule in getattr(effects, "template_defaults", ()) or ():
        if rule.only_if_absent:
            if rule.field in template:
                continue
            absent_also = getattr(rule, "absent_also", ()) or ()
            if any(key in template for key in absent_also):
                continue
        template[rule.field] = f"${{var.{rule.scope_var}}}"
    side = _group_ref_side(params.get("group"))
    if side is None:
        return
    for rule in getattr(effects, "template_group_side_defaults", ()) or ():
        if rule.side == side:
            template[rule.field] = f"${{var.{rule.scope_var}}}"


# Catalog context_vars that are optional on the success path. Missing `with`
# must compile to JSON null, not `${var.X}` (SQL bind fails closed on missing
# scope vars). QC-fail archives set rejectReason in `with` and override this.
_OPTIONAL_NULL_CONTEXT_VARS = frozenset({"rejectReason"})


def _action_template(entry, step: ActionStep) -> Dict[str, Any]:
    tool = entry.tool or entry.action_name
    template: Dict[str, Any] = {"tool": tool}
    template["projectPath"] = "${var.projectPath}"
    template["executionScopeId"] = "${var.executionScopeId}"
    if entry.action_config_key:
        template["project"] = "${var.projectPath}"
        template["resolvedConfig"] = f"${{var.resolvedConfig__{entry.action_config_key}}}"
    for ctx_var in entry.context_vars:
        template[ctx_var] = f"${{var.{ctx_var}}}"

    params = step.with_ or step.in_ or {}
    for key, val in params.items():
        template[key] = _placeholder_for_value(val)

    for key in _OPTIONAL_NULL_CONTEXT_VARS:
        if template.get(key) == f"${{var.{key}}}":
            template[key] = None

    _apply_catalog_template_rules(template, entry, params)
    return template


def _emit_root_scope_defaults(ctx: _CompileCtx, root_key: str) -> None:
    ctx.scope_defaults.extend(
        [
            WorkflowScopeDefaultSpec(
                node_key=root_key,
                var_name="projectPath",
                default_expr="${var.projectPath}",
            ),
            WorkflowScopeDefaultSpec(
                node_key=root_key,
                var_name="centroid1Dir",
                default_expr="${var.centroid1Dir}",
            ),
        ]
    )


def _link(parent: str, child: str, order: int, branch: str, ctx: _CompileCtx) -> None:
    ctx.edges.append(
        WorkflowEdgeSpec(
            parent_node_key=parent,
            child_node_key=child,
            child_order=order,
            branch_kind=branch,  # type: ignore[arg-type]
        )
    )


def _compile_action(
    ctx: _CompileCtx,
    step: ActionStep,
    parent_key: str,
    order: int,
    *,
    branch: str = "SEQUENCE",
    in_parallel: bool = False,
) -> str:
    action_name = step.action_name()
    entry = find_catalog_entry(action_name)
    if entry is None:
        raise ValueError(f"unknown action in program: {action_name!r}")
    node_key = step.node_key or ctx.fresh_key(action_name.replace(".", "_"))
    ctx.nodes.append(
        WorkflowNodeSpec(
            node_key=node_key,
            node_type="ACTION",
            action_name=entry.action_name,
            input_template=_action_template(entry, step),
        )
    )
    _link(parent_key, node_key, order, branch, ctx)
    if entry.domain_effects and entry.domain_effects.scope_bindings:
        for var_name, json_path in entry.domain_effects.scope_bindings:
            ctx.output_bindings.append(
                WorkflowOutputBindingSpec(
                    node_key=node_key,
                    var_name=var_name,
                    source_kind="output_path",
                    source_json_path=json_path,
                )
            )
    if entry.domain_effects and entry.domain_effects.output_bindings:
        for binding in entry.domain_effects.output_bindings:
            ctx.output_bindings.append(
                WorkflowOutputBindingSpec(
                    node_key=node_key,
                    var_name=binding.scope_field,
                    source_kind="output_path",
                    source_json_path=binding.output_json_path,
                )
            )
    if step.out:
        for var_name, json_path in step.out.items():
            if in_parallel:
                if var_name in ctx.assign_targets_in_parallel:
                    raise ValueError(
                        f"assign/out target {var_name!r} cannot be written under a parallel "
                        f"ancestor (shared mutable assign is forbidden)"
                    )
                ctx.assign_targets_in_parallel.add(var_name)
            ctx.output_bindings.append(
                WorkflowOutputBindingSpec(
                    node_key=node_key,
                    var_name=var_name,
                    source_kind="output_path",
                    source_json_path=json_path,
                )
            )
    return node_key


def _compile_assign(
    ctx: _CompileCtx,
    step: AssignStep,
    parent_key: str,
    order: int,
    *,
    branch: str = "SEQUENCE",
    in_parallel: bool = False,
) -> str:
    target = step.assign
    if target not in ctx.declared_vars:
        raise ValueError(
            f"assign target {target!r} is not declared in program variables "
            f"(need schemaRef/schema declaration)"
        )
    if target in ctx.foreach_bound_names:
        raise ValueError(
            f"assign target {target!r} collides with FOREACH as/index name in the same scope"
        )
    action_name = step.action_name()
    expected = _ASSIGN_ACTION_SCHEMA_REF.get(action_name)
    declared = ctx.variable_schema_refs.get(target)
    if expected and declared and declared != expected:
        raise ValueError(
            f"assign target {target!r} schemaRef {declared!r} is incompatible with "
            f"action {action_name!r} (expected exact match {expected!r})"
        )
    return _compile_action(
        ctx, step.as_action_step(), parent_key, order, branch=branch, in_parallel=in_parallel
    )


def _compile_if(
    ctx: _CompileCtx,
    step: IfStep,
    parent_key: str,
    order: int,
    *,
    branch: str = "SEQUENCE",
    in_parallel: bool = False,
) -> str:
    if_key = ctx.fresh_key("if")
    cond_var = _condition_var_from_expr(step.if_)
    ctx.nodes.append(
        WorkflowNodeSpec(node_key=if_key, node_type="IF", condition_var=cond_var)
    )
    _link(parent_key, if_key, order, branch, ctx)
    _compile_steps(ctx, step.then, if_key, branch="THEN", in_parallel=in_parallel)
    # IF picks THEN/ELSE from the integer Result (condition_var / result_code), same
    # shape as SWITCH CASE/DEFAULT. Typed action outputs explain that Result; they do
    # not select the branch. Always emit ELSE so Result=0 has a branch (empty SEQUENCE
    # when the program omits else — activate completes empty SEQUENCE immediately).
    else_steps = list(step.else_ or [])
    if else_steps:
        _compile_steps(ctx, else_steps, if_key, branch="ELSE", in_parallel=in_parallel)
    else:
        else_key = ctx.fresh_key("seq")
        ctx.nodes.append(WorkflowNodeSpec(node_key=else_key, node_type="SEQUENCE"))
        _link(if_key, else_key, 0, "ELSE", ctx)
    return if_key


def _compile_switch(
    ctx: _CompileCtx,
    step: SwitchStep,
    parent_key: str,
    order: int,
    *,
    branch: str = "SEQUENCE",
    in_parallel: bool = False,
) -> str:
    sw_key = ctx.fresh_key("switch")
    switch_var: Optional[str] = None
    switch_ref: Optional[str] = None
    if isinstance(step.switch, dict) and "ref" in step.switch:
        switch_ref = str(step.switch["ref"])
    else:
        switch_var = _condition_var_from_expr(str(step.switch))
    ctx.nodes.append(
        WorkflowNodeSpec(
            node_key=sw_key,
            node_type="SWITCH",
            switch_var=switch_var,
            switch_ref_node_key=switch_ref,
        )
    )
    _link(parent_key, sw_key, order, branch, ctx)
    case_order = 0
    for case_key, case_steps in step.cases.items():
        case_val = int(case_key)
        case_seq = _compile_steps(ctx, case_steps, sw_key, branch="CASE", in_parallel=in_parallel)
        # Retarget last link to include switch_case_value
        for edge in reversed(ctx.edges):
            if edge.parent_node_key == sw_key and edge.child_node_key == case_seq:
                edge.switch_case_value = case_val
                edge.child_order = case_order
                break
        case_order += 1
    if step.default:
        def_seq = _compile_steps(ctx, step.default, sw_key, branch="DEFAULT", in_parallel=in_parallel)
        for edge in reversed(ctx.edges):
            if edge.parent_node_key == sw_key and edge.child_node_key == def_seq:
                edge.is_default = True
                edge.child_order = case_order
                break
    return sw_key


def _compile_while(
    ctx: _CompileCtx,
    step: WhileStep,
    parent_key: str,
    order: int,
    *,
    branch: str = "SEQUENCE",
    in_parallel: bool = False,
) -> str:
    wh_key = ctx.fresh_key("while")
    cond_var = _condition_var_from_expr(step.while_)
    ctx.nodes.append(
        WorkflowNodeSpec(node_key=wh_key, node_type="WHILE", condition_var=cond_var)
    )
    _link(parent_key, wh_key, order, branch, ctx)
    _compile_steps(ctx, step.steps(), wh_key, branch="BODY", in_parallel=in_parallel)
    return wh_key


def _compile_repeat(
    ctx: _CompileCtx,
    step: RepeatStep,
    parent_key: str,
    order: int,
    *,
    branch: str = "SEQUENCE",
    in_parallel: bool = False,
) -> str:
    rp_key = ctx.fresh_key("repeat")
    count = step.count()
    if count < 0:
        raise ValueError(f"repeat count must be >= 0, got {count}")
    ctx.nodes.append(
        WorkflowNodeSpec(node_key=rp_key, node_type="REPEAT", repeat_count=count)
    )
    _link(parent_key, rp_key, order, branch, ctx)
    _compile_steps(ctx, step.steps(), rp_key, branch="BODY", in_parallel=in_parallel)
    return rp_key


def _compile_foreach(
    ctx: _CompileCtx,
    step: ForeachStep,
    parent_key: str,
    order: int,
    *,
    branch: str = "SEQUENCE",
    in_parallel: bool = False,
) -> str:
    fe_key = ctx.fresh_key("foreach")
    spec = step.spec()
    item = spec.resolved_item()
    index_var = spec.index or f"{item}Index"
    child_parallel = in_parallel or bool(spec.parallel)
    ctx.nodes.append(
        WorkflowNodeSpec(
            node_key=fe_key,
            node_type="FOREACH",
            foreach_collection_var=spec.resolved_collection_var(),
            foreach_item_var=item,
            foreach_index_var=index_var,
            foreach_parallel=spec.parallel,
        )
    )
    _link(parent_key, fe_key, order, branch, ctx)
    prev_bound = set(ctx.foreach_bound_names)
    ctx.foreach_bound_names = prev_bound | {item, index_var}
    try:
        _compile_steps(ctx, step.body(), fe_key, branch="BODY", in_parallel=child_parallel)
    finally:
        ctx.foreach_bound_names = prev_bound
    return fe_key


def _compile_parallel(
    ctx: _CompileCtx,
    step: ParallelStep,
    parent_key: str,
    order: int,
    *,
    branch: str = "SEQUENCE",
    in_parallel: bool = False,
) -> str:
    par_key = ctx.fresh_key("parallel")
    ctx.nodes.append(WorkflowNodeSpec(node_key=par_key, node_type="PARALLEL"))
    _link(parent_key, par_key, order, branch, ctx)
    # Scope sibling-write tracking to this PARALLEL block.
    # - Top-level parallel: start empty so sequential parallel blocks do not
    #   poison each other across time.
    # - Nested under a parallel ancestor: keep inherited sibling writes visible
    #   so an earlier outer assign still races with writes inside this block.
    prev_targets = set(ctx.assign_targets_in_parallel)
    ctx.assign_targets_in_parallel = set(prev_targets) if in_parallel else set()
    try:
        for idx, raw in enumerate(step.parallel):
            _compile_one(ctx, _parse_step(raw), par_key, idx, branch="PARALLEL", in_parallel=True)
    finally:
        if in_parallel:
            ctx.assign_targets_in_parallel = prev_targets | ctx.assign_targets_in_parallel
        else:
            ctx.assign_targets_in_parallel = prev_targets
    return par_key


def _wait_correlation_var(expr: Optional[str]) -> Optional[str]:
    if not expr:
        return None
    text = expr.strip()
    if text.startswith("${") and text.endswith("}"):
        text = text[2:-1]
    for prefix in ("var.", "ctx."):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    return text or None


def _compile_wait(
    ctx: _CompileCtx,
    step: WaitStep,
    parent_key: str,
    order: int,
    *,
    branch: str = "SEQUENCE",
) -> str:
    node_key = step.node_key or ctx.fresh_key("wait")
    ctx.nodes.append(
        WorkflowNodeSpec(
            node_key=node_key,
            node_type="WAIT_EVENT",
            wait_event_type=step.wait.event,
            wait_correlation_var=_wait_correlation_var(step.wait.correlation),
        )
    )
    _link(parent_key, node_key, order, branch, ctx)
    return node_key


def _compile_one(
    ctx: _CompileCtx,
    step: Any,
    parent_key: str,
    order: int,
    *,
    branch: str = "SEQUENCE",
    in_parallel: bool = False,
) -> str:
    if isinstance(step, AssignStep):
        return _compile_assign(ctx, step, parent_key, order, branch=branch, in_parallel=in_parallel)
    if isinstance(step, ActionStep):
        return _compile_action(ctx, step, parent_key, order, branch=branch, in_parallel=in_parallel)
    if isinstance(step, IfStep):
        return _compile_if(ctx, step, parent_key, order, branch=branch, in_parallel=in_parallel)
    if isinstance(step, SwitchStep):
        return _compile_switch(ctx, step, parent_key, order, branch=branch, in_parallel=in_parallel)
    if isinstance(step, WhileStep):
        return _compile_while(ctx, step, parent_key, order, branch=branch, in_parallel=in_parallel)
    if isinstance(step, RepeatStep):
        return _compile_repeat(ctx, step, parent_key, order, branch=branch, in_parallel=in_parallel)
    if isinstance(step, ForeachStep):
        return _compile_foreach(ctx, step, parent_key, order, branch=branch, in_parallel=in_parallel)
    if isinstance(step, ParallelStep):
        return _compile_parallel(ctx, step, parent_key, order, branch=branch, in_parallel=in_parallel)
    if isinstance(step, WaitStep):
        return _compile_wait(ctx, step, parent_key, order, branch=branch)
    raise ValueError(f"unsupported step type: {type(step)!r}")


def _compile_steps(
    ctx: _CompileCtx,
    steps: List[Any],
    parent_key: str,
    *,
    branch: str = "SEQUENCE",
    in_parallel: bool = False,
) -> str:
    seq_key = ctx.fresh_key("seq")
    ctx.nodes.append(WorkflowNodeSpec(node_key=seq_key, node_type="SEQUENCE"))
    if branch != "SEQUENCE":
        _link(parent_key, seq_key, 0, branch, ctx)
    for order, raw in enumerate(steps):
        _compile_one(ctx, _parse_step(raw), seq_key, order, in_parallel=in_parallel)
    return seq_key


def compile_domain_program(program: DomainProgram, *, enrich_context: bool = False) -> CompileResult:
    ctx = _CompileCtx()
    context_seeds, decls = program.split_variables()
    ctx.declared_vars = set(decls.keys())
    variable_schemas: Dict[str, Any] = {}
    for name, decl in decls.items():
        ref = decl.resolved_schema_ref()
        body = decl.resolved_schema_body()
        variable_schemas[name] = body if body is not None else ref
        if ref:
            ctx.variable_schema_refs[name] = ref

    root_key = "root"
    ctx.nodes.append(WorkflowNodeSpec(node_key=root_key, node_type="SEQUENCE"))

    if program.body:
        body_seq = _compile_steps(ctx, program.body, root_key)
        _link(root_key, body_seq, 0, "SEQUENCE", ctx)
    else:
        for order, phase in enumerate(program.phases):
            if phase.foreach:
                fe_key = ctx.fresh_key(f"phase_{phase.name}")
                spec = phase.foreach
                child_parallel = bool(spec.parallel)
                ctx.nodes.append(
                    WorkflowNodeSpec(
                        node_key=fe_key,
                        node_type="FOREACH",
                        foreach_collection_var=spec.resolved_collection_var(),
                        foreach_item_var=spec.resolved_item(),
                        foreach_index_var=spec.index or f"{spec.resolved_item()}Index",
                        foreach_parallel=spec.parallel,
                    )
                )
                _link(root_key, fe_key, order, "SEQUENCE", ctx)
                _compile_steps(ctx, phase.steps, fe_key, branch="BODY", in_parallel=child_parallel)
            else:
                phase_seq = _compile_steps(ctx, phase.steps, root_key)
                _link(root_key, phase_seq, order, "SEQUENCE", ctx)

    collection_bindings = _build_collection_bindings(program)
    context_json: Dict[str, Any] = {"projectPath": program.projectPath, **context_seeds}
    if enrich_context:
        try:
            context_json = enrich_instance_context(context_json)
        except (FileNotFoundError, ValueError):
            pass

    _emit_root_scope_defaults(ctx, root_key)

    event_triggers = [
        CompiledEventTriggerSpec(
            event=trig.event,
            mode=trig.mode,  # type: ignore[arg-type]
            filter_json=trig.filter,
        )
        for trig in program.on
    ]

    workflow = WorkflowDefinitionSpec(
        name=program.name,
        description=program.description,
        root_node_key=root_key,
        nodes=ctx.nodes,
        edges=ctx.edges,
        output_bindings=ctx.output_bindings,
        scope_defaults=ctx.scope_defaults,
        input_bindings=ctx.input_bindings,
        collection_bindings=collection_bindings,
        event_triggers=event_triggers,
        variable_schemas=variable_schemas,
    )
    return CompileResult(workflow=workflow, context_json=context_json)


def compile_domain_program_file(path: Path, *, enrich_context: bool = False) -> CompileResult:
    data = json.loads(path.read_text(encoding="utf-8"))
    return compile_domain_program(DomainProgram.model_validate(data), enrich_context=enrich_context)
