"""
Unified catalog of all workflow ACTION definitions.

Single source of truth for action_name, capability, handler dispatch, CLI/tool
mapping, and task I/O schema models. Tool parameters resolve via profile/site
``actionConfig`` into task ``resolvedConfig`` (not study manifest step_config).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, FrozenSet, List, Literal, Mapping, Optional, Sequence, Tuple, TypedDict

if TYPE_CHECKING:
    from .actions.base import ActionBase

ExecutionMode = Literal["cli", "in_process"]
ActionCategory = Literal["sample_prep", "modeling", "validation"]
ActionConfigKey = Literal[
    "centroid",
    "detection",
    "dmp_selection",
    "mapper",
    "enricher",
    "classifier",
    "gene_selection",
    "predictor",
    "alignment_qc",
    "extraction_qc",
    "fragmentomics",
    "methyl_extract",
    "validation",
    "derived_measures",
    "cell_deconvolution",
    "residualize",
    "methylation_confounder_scores",
    "info_measures",
    "mhb_mhl",
    "progression",
    "parabricks",
    "docker_align",
    "demultiplex",
    "methylgrapher_wgbs",
    "rna_align",
    "rna_qc",
    "rna_de_select",
    "proteomics_quant",
    "proteomics_qc",
    "protein_de_select",
]
ArgvMap = Tuple[Tuple[str, str], ...]
ContextVars = Tuple[str, ...]
SchemaRef = Tuple[str, str]  # (module, class)

DEFAULT_PIPELINE_ARGV_MAP: ArgvMap = (
    ("project", "--project"),
    ("projectPath", "--project"),
    ("group", "--group"),
    ("chromosome", "--chromosome"),
    ("context", "--context"),
    ("comparison", "--comparison"),
    ("outputDir", "--output-dir"),
    ("centroid1Dir", "--centroid1-dir"),
    ("centroid2Dir", "--centroid2-dir"),
    ("stepOverride", "--step-override"),
    ("resolvedConfigPath", "--resolved-config"),
)
CENTROID_ARGV_MAP: ArgvMap = DEFAULT_PIPELINE_ARGV_MAP + (
    ("residualizeCoefDir", "--residualize-coef-dir"),
)
CLASSIFIER_ARGV_MAP: ArgvMap = DEFAULT_PIPELINE_ARGV_MAP + (
    ("residualizeCoefDir", "--residualize-coef-dir"),
)
NodeType = Literal[
    "ACTION",
    "SEQUENCE",
    "PARALLEL",
    "IF",
    "SWITCH",
    "REPEAT",
    "WHILE",
    "FOREACH",
]

# Recognized action catalog keys resolved via action_config_resolver.
PROJECT_ACTION_CONFIG_KEYS: FrozenSet[ActionConfigKey] = frozenset(
    {
        "centroid",
        "detection",
        "dmp_selection",
        "mapper",
        "enricher",
        "classifier",
        "gene_selection",
        "predictor",
        "alignment_qc",
        "extraction_qc",
        "fragmentomics",
        "methyl_extract",
        "validation",
        "derived_measures",
        "cell_deconvolution",
        "residualize",
        "methylation_confounder_scores",
        "info_measures",
        "mhb_mhl",
        "progression",
        "parabricks",
        "docker_align",
        "demultiplex",
        "methylgrapher_wgbs",
        "rna_align",
        "rna_qc",
        "rna_de_select",
        "proteomics_quant",
        "proteomics_qc",
        "protein_de_select",
    }
)


@dataclass(frozen=True)
class DomainOutputBinding:
    """Maps worker output_json path to a field on a domain type in scope."""

    domain_type: str
    scope_field: str
    output_json_path: str


@dataclass(frozen=True)
class TemplateDefault:
    """Compiler template field bound to a scope variable: ``template[field] = ${var.scope_var}``."""

    field: str
    scope_var: str
    only_if_absent: bool = False
    # Extra template keys that must also be absent when ``only_if_absent`` is set
    # (e.g. skip ``comparison`` when ``label`` is already present).
    absent_also: Tuple[str, ...] = ()


@dataclass(frozen=True)
class TemplateGroupSideDefault:
    """When ``with.group`` references control/disease side, bind ``field`` to ``scope_var``."""

    side: Literal["control", "disease"]
    field: str
    scope_var: str


@dataclass(frozen=True)
class DomainEffects:
    reads_types: Tuple[str, ...] = ()
    writes_types: Tuple[str, ...] = ()
    scope_bindings: Tuple[Tuple[str, str], ...] = ()  # (var_name, output_json_path)
    output_bindings: Tuple[DomainOutputBinding, ...] = ()
    # Compiler input_template rules (process-pack metadata; engine stays action-agnostic).
    template_defaults: Tuple[TemplateDefault, ...] = ()
    template_group_side_defaults: Tuple[TemplateGroupSideDefault, ...] = ()


@dataclass(frozen=True)
class ActionControl:
    """In-flight task control contract for agnostic WorkerRunner / portal UX.

    Worker-level drain/continue is always available via ``wf.worker.desired_state``.
    These flags describe what the runner may do to the **current** task.
    """

    can_pause: bool = False
    can_continue: bool = False
    can_stop: bool = True

    def to_dict(self) -> Dict[str, bool]:
        return {
            "can_pause": self.can_pause,
            "can_continue": self.can_continue,
            "can_stop": self.can_stop,
        }


# Default: stoppable abort; no cooperative pause/continue until handlers checkpoint.
DEFAULT_ACTION_CONTROL = ActionControl()
# Long GPU / Align / extract work — explicit stoppable (same as default; documented).
CONTROL_STOPPABLE = ActionControl(can_pause=False, can_continue=False, can_stop=True)
# Abort unsafe (e.g. destructive finalize) — drain only.
CONTROL_DRAIN_ONLY = ActionControl(can_pause=False, can_continue=False, can_stop=False)


@dataclass(frozen=True)
class ActionDispatch:
    """Claim-time scheduling constraints (catalog SoT → ``wf.workflow_action``).

    The workflow engine enforces these generically; it does not interpret
    science/action names. Seeded via ``dispatch`` on the action catalog.
    """

    max_per_worker: Optional[int] = None
    exclusive_worker: bool = False
    # Opaque input_json field name (e.g. "sampleId"); engine never interprets
    # the value — only matches keys within a workflow instance.
    affinity_key_field: Optional[str] = None
    prefer_previous_worker: bool = False
    prefer_continue_group: bool = False

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if self.max_per_worker is not None:
            out["max_per_worker"] = int(self.max_per_worker)
        if self.exclusive_worker:
            out["exclusive_worker"] = True
        if self.affinity_key_field:
            out["affinity_key_field"] = str(self.affinity_key_field)
        if self.prefer_previous_worker:
            out["prefer_previous_worker"] = True
        if self.prefer_continue_group:
            out["prefer_continue_group"] = True
        return out


DEFAULT_ACTION_DISPATCH = ActionDispatch()
# Soft sample stickiness for SamplePrep-style FOREACH chains: continue the same
# affinity key before starting a new one; prefer the worker that last completed
# that key (fallback to any capable worker when preferred is busy).
DISPATCH_SAMPLE_AFFINITY = ActionDispatch(
    affinity_key_field="sampleId",
    prefer_previous_worker=True,
    prefer_continue_group=True,
)
# Full-worker actions (e.g. single-GPU Align): at most one on a worker, and no
# concurrent sibling claims while leased; also sample-affinity for SamplePrep.
DISPATCH_EXCLUSIVE_ONE = ActionDispatch(
    max_per_worker=1,
    exclusive_worker=True,
    affinity_key_field="sampleId",
    prefer_previous_worker=True,
    prefer_continue_group=True,
)


class ActionCatalogExport(TypedDict, total=False):
    action_name: str
    capability: str
    execution_mode: ExecutionMode
    schema_id: str
    description: str
    category: ActionCategory
    default_node_type: NodeType
    input_schema_ref: str
    output_schema_ref: str
    action_config_key: ActionConfigKey
    context_vars: List[str]
    argv_map: Dict[str, str]
    in_process_handler: str
    cli_tool: str
    tool: str
    domain_effects: Dict[str, Any]
    control: Dict[str, bool]
    dispatch: Dict[str, Any]
    idempotency_enabled: bool
    idempotency_opt_out_reason: str


@dataclass(frozen=True)
class ActionCatalogEntry:
    action_name: str
    capability: str
    schema_id: str
    description: str
    category: ActionCategory
    input_module: str
    input_class: str
    output_module: str
    output_class: str
    execution_mode: ExecutionMode = "cli"
    default_node_type: NodeType = "ACTION"
    cli_tool: Optional[str] = None
    tool: Optional[str] = None
    action_config_key: Optional[ActionConfigKey] = None
    context_vars: ContextVars = field(default_factory=tuple)
    argv_map: ArgvMap = DEFAULT_PIPELINE_ARGV_MAP
    in_process_handler: Optional[str] = None
    handler: Optional[str] = None  # deprecated alias for in_process_handler
    idempotency_enabled: bool = False
    idempotency_opt_out_reason: Optional[str] = None
    internal: bool = False
    domain_effects: Optional[DomainEffects] = None
    control: ActionControl = DEFAULT_ACTION_CONTROL
    dispatch: ActionDispatch = DEFAULT_ACTION_DISPATCH

    def __post_init__(self) -> None:
        errors = list(_entry_invariant_errors(self))
        if errors:
            raise ValueError("; ".join(errors))

    def resolved_in_process_handler(self) -> Optional[str]:
        return self.in_process_handler or self.handler

    def build_action(self, handlers_module: Any = None) -> ActionBase:
        from .actions.base import build_action_from_catalog
        import methyl_worker.handlers as handlers_mod

        mod = handlers_module if handlers_module is not None else handlers_mod
        return build_action_from_catalog(self, mod)

    @property
    def input_schema_ref(self) -> str:
        safe = self.action_name.replace(".", "_")
        return f"schemas/tasks/{safe}.input.schema.json"

    @property
    def output_schema_ref(self) -> str:
        safe = self.action_name.replace(".", "_")
        return f"schemas/tasks/{safe}.output.schema.json"

    def to_catalog_dict(self) -> ActionCatalogExport:
        payload: ActionCatalogExport = {
            "action_name": self.action_name,
            "capability": self.capability,
            "execution_mode": self.execution_mode,
            "schema_id": self.schema_id,
            "description": self.description,
            "category": self.category,
            "default_node_type": self.default_node_type,
            "input_schema_ref": self.input_schema_ref,
            "output_schema_ref": self.output_schema_ref,
            "context_vars": list(self.context_vars),
            "argv_map": {k: v for k, v in self.argv_map},
        }
        if self.action_config_key is not None:
            payload["action_config_key"] = self.action_config_key
        handler = self.resolved_in_process_handler()
        if handler:
            payload["in_process_handler"] = handler
        if self.cli_tool:
            payload["cli_tool"] = self.cli_tool
        if self.tool:
            payload["tool"] = self.tool
        if self.domain_effects:
            de = self.domain_effects
            payload["domain_effects"] = {
                "reads_types": list(de.reads_types),
                "writes_types": list(de.writes_types),
                "scope_bindings": [
                    {"var_name": v, "output_json_path": p} for v, p in de.scope_bindings
                ],
                "output_bindings": [
                    {
                        "domain_type": b.domain_type,
                        "scope_field": b.scope_field,
                        "output_json_path": b.output_json_path,
                    }
                    for b in de.output_bindings
                ],
                "template_defaults": [
                    {
                        "field": t.field,
                        "scope_var": t.scope_var,
                        "only_if_absent": t.only_if_absent,
                        "absent_also": list(t.absent_also),
                    }
                    for t in de.template_defaults
                ],
                "template_group_side_defaults": [
                    {
                        "side": t.side,
                        "field": t.field,
                        "scope_var": t.scope_var,
                    }
                    for t in de.template_group_side_defaults
                ],
            }
        payload["control"] = self.control.to_dict()
        dispatch_payload = self.dispatch.to_dict()
        if dispatch_payload:
            payload["dispatch"] = dispatch_payload
        # Effective eligibility (default-on with explicit opt-outs).
        payload["idempotency_enabled"] = idempotency_enabled_for(self)
        reason = idempotency_opt_out_reason_for(self)
        if reason:
            payload["idempotency_opt_out_reason"] = reason
        return payload


_PIPELINE_MODULE = "methyl_worker.task_models.pipeline_models"
_SAMPLE_MODULE = "methyl_worker.task_models.sample_prep_models"
_RNA_SAMPLE_MODULE = "methyl_worker.task_models.rna_prep_models"
_PROTEOMICS_MODULE = "methyl_worker.task_models.proteomics_prep_models"
_VALIDATION_MODULE = "methyl_worker.task_models.validation_models"
# Domain effect presets (see workflow_engine/contract/domain_types.md)
_DE_METHYL_SAMPLE = DomainEffects(reads_types=("MethylSampleRef",), writes_types=("MethylSampleRef",))
_DE_DOWNLOAD = DomainEffects(
    reads_types=("MethylIngestRef",),
    writes_types=("MethylSampleRef",),
    output_bindings=(
        DomainOutputBinding("MethylSampleRef", "fastqFiles", "$.fastqFiles"),
    ),
)
_DE_PARABRICKS = DomainEffects(
    reads_types=("MethylSampleRef",),
    writes_types=("MethylSampleRef",),
    output_bindings=(
        DomainOutputBinding("MethylSampleRef", "bamPath", "$.bamPath"),
        DomainOutputBinding("MethylSampleRef", "metricsJson", "$.metricsJson"),
        DomainOutputBinding("MethylSampleRef", "qcMetricsTar", "$.qcMetricsTar"),
    ),
)
_DE_METHYL_QC = DomainEffects(
    reads_types=("MethylSampleRef",),
    writes_types=("MethylSampleRef",),
    scope_bindings=(
        ("qcPass", "$.guardrails.overall_pass"),
        ("qcDisposition", "$.screening.disposition"),
        ("trimFront1", "$.screening.trim_front1"),
        ("trimTail1", "$.screening.trim_tail1"),
        ("trimFront2", "$.screening.trim_front2"),
        ("trimTail2", "$.screening.trim_tail2"),
        ("qcAttemptReason", "$.screening.message"),
        ("remediateAlignment", "$.remediateAlignment"),
        ("remediateR2Trim", "$.remediateR2Trim"),
        # archive_sample context_vars / templates resolve var.qcPath (not only alignmentQc.qcPath).
        ("qcPath", "$.qcPath"),
    ),
    output_bindings=(
        DomainOutputBinding("MethylSampleRef", "alignmentQc", "$.alignmentQc"),
    ),
)
_DE_FRAGMENTOMICS = DomainEffects(
    reads_types=("MethylSampleRef",),
    writes_types=("MethylSampleRef",),
    output_bindings=(
        DomainOutputBinding("MethylSampleRef", "fragmentomics", "$.fragmentomics"),
    ),
)
_DE_METHYL_EXTRACT = DomainEffects(
    reads_types=("MethylSampleRef",),
    writes_types=("MethylSampleRef",),
    output_bindings=(
        DomainOutputBinding("MethylSampleRef", "methylation", "$.methylation"),
    ),
)
_DE_EXTRACTION_QC = DomainEffects(
    reads_types=("MethylSampleRef",),
    writes_types=("MethylSampleRef",),
    scope_bindings=(
        ("extractionQcPass", "$.guardrails.overall_pass"),
    ),
    output_bindings=(
        DomainOutputBinding("MethylSampleRef", "extractionQc", "$.extractionQc"),
    ),
)
_DE_ARCHIVE_SAMPLE = DomainEffects(
    reads_types=("MethylSampleRef",),
    writes_types=("MethylSampleRef",),
    scope_bindings=(("sampleArchived", "$.sampleArchived"),),
    output_bindings=(
        DomainOutputBinding("MethylSampleRef", "sampleArchive", "$.sampleArchive"),
    ),
)
_DE_QC_FAILED = DomainEffects(
    reads_types=("MethylSampleRef",),
    writes_types=("MethylSampleRef",),
    output_bindings=(
        DomainOutputBinding("MethylSampleRef", "status", "$.status"),
    ),
)
_DE_CENTROID = DomainEffects(
    reads_types=("MethylGroup",),
    writes_types=("MethylCentroidRef",),
    template_group_side_defaults=(
        TemplateGroupSideDefault("control", "outputDir", "centroid1Dir"),
        TemplateGroupSideDefault("disease", "outputDir", "centroid2Dir"),
    ),
)
_DE_DETECTION_TYPES = DomainEffects(
    reads_types=("ComparisonSpec", "MethylCentroidRef"),
    writes_types=("MethylDetectionRef",),
)
_DE_DETECTOR = DomainEffects(
    reads_types=("ComparisonSpec", "MethylCentroidRef"),
    writes_types=("MethylDetectionRef",),
    template_defaults=(
        TemplateDefault("centroid1Dir", "centroid1Dir"),
        TemplateDefault("centroid2Dir", "centroid2Dir"),
        TemplateDefault("outputDir", "detectOutDir"),
        TemplateDefault("comparison", "label", only_if_absent=True, absent_also=("label",)),
    ),
)
_DE_PLAN_ITERATIONS = DomainEffects(
    reads_types=("MethylGroup",),
    writes_types=("StratifiedCohortDraw", "CentroidSeedGroup"),
    scope_bindings=(
        ("iterations", "$.iterations"),
        ("centroidSeedGroups", "$.centroidSeedGroups"),
    ),
)
_DE_PREPARE_FREEZE = DomainEffects(
    reads_types=("StratifiedCohortDraw",),
    writes_types=("ValidationArtifactRef",),
    scope_bindings=(
        ("fixedDmpPanel", "$.fixedDmpPanel"),
        ("projectPath", "$.projectPath"),
        ("centroid1Dir", "$.centroid1Dir"),
        ("centroid2Dir", "$.centroid2Dir"),
        ("detectOutDir", "$.detectOutDir"),
    ),
)
_DE_SELECT_BEST_MODEL = DomainEffects(
    reads_types=("StratifiedCohortDraw",),
    writes_types=("ValidationArtifactRef",),
    scope_bindings=(("selectedBackend", "$.selectedBackend"),),
)
_DE_VALIDATION = DomainEffects(
    reads_types=("StratifiedCohortDraw",),
    writes_types=("ValidationArtifactRef",),
)
_DE_RESOLVE_PROJECT = DomainEffects(
    reads_types=("MethylGroup", "ComparisonSpec"),
    writes_types=("ResolvedProject",),
    scope_bindings=(("resolvedProject", "$.resolvedProject"),),
)
_DE_RNA_QC = DomainEffects(
    scope_bindings=(
        ("qcPass", "$.guardrails.overall_pass"),
        ("rnaQcPass", "$.rnaQcPass"),
    ),
)
_DE_RNA_REGISTER = DomainEffects(
    scope_bindings=(
        ("expressionRegistered", "$.expressionH5"),
    ),
)
_DE_PROTEOMICS_QC = DomainEffects(
    scope_bindings=(
        ("qcPass", "$.guardrails.overall_pass"),
        ("proteomicsQcPass", "$.proteomicsQcPass"),
    ),
)
_WORKFLOW_MODULE = "methyl_worker.task_models.workflow_compute_models"
_DE_WORKFLOW_VALUE = DomainEffects(
    scope_bindings=(),  # assign/out supplies the scope write; no fixed var name
)


def _entry_invariant_errors(entry: ActionCatalogEntry) -> List[str]:
    """Cross-field catalog rules enforced at construction and by validate_catalog()."""
    errors: List[str] = []
    if entry.execution_mode == "cli":
        if not entry.cli_tool:
            errors.append(f"{entry.action_name}: cli actions require cli_tool")
    elif entry.execution_mode == "in_process":
        if not entry.resolved_in_process_handler():
            errors.append(f"{entry.action_name}: in_process actions require in_process_handler")
    else:
        errors.append(f"{entry.action_name}: unknown execution_mode {entry.execution_mode!r}")

    if entry.action_config_key is not None:
        if entry.action_config_key not in PROJECT_ACTION_CONFIG_KEYS:
            errors.append(
                f"{entry.action_name}: unknown action_config_key {entry.action_config_key!r}"
            )
    elif not entry.context_vars:
        errors.append(
            f"{entry.action_name}: must set action_config_key or non-empty context_vars"
        )
    return errors


def _entry(
    action_name: str,
    capability: str,
    schema_id: str,
    description: str,
    category: ActionCategory,
    input_module: str,
    input_class: str,
    output_module: str,
    output_class: str,
    *,
    execution_mode: ExecutionMode = "cli",
    cli_tool: Optional[str] = None,
    tool: Optional[str] = None,
    action_config_key: Optional[ActionConfigKey] = None,
    context_vars: ContextVars = (),
    argv_map: ArgvMap = DEFAULT_PIPELINE_ARGV_MAP,
    in_process_handler: Optional[str] = None,
    idempotency_enabled: bool = False,
    idempotency_opt_out_reason: Optional[str] = None,
    domain_effects: Optional[DomainEffects] = None,
    control: ActionControl = DEFAULT_ACTION_CONTROL,
    dispatch: ActionDispatch = DEFAULT_ACTION_DISPATCH,
    internal: bool = False,
) -> ActionCatalogEntry:
    return ActionCatalogEntry(
        action_name=action_name,
        capability=capability,
        schema_id=schema_id,
        description=description,
        category=category,
        input_module=input_module,
        input_class=input_class,
        output_module=output_module,
        output_class=output_class,
        execution_mode=execution_mode,
        cli_tool=cli_tool,
        tool=tool,
        action_config_key=action_config_key,
        context_vars=context_vars,
        argv_map=argv_map,
        in_process_handler=in_process_handler,
        idempotency_enabled=idempotency_enabled,
        idempotency_opt_out_reason=idempotency_opt_out_reason,
        domain_effects=domain_effects,
        control=control,
        dispatch=dispatch,
        internal=internal,
    )


def _cli(
    action_name: str,
    capability: str,
    schema_id: str,
    description: str,
    category: ActionCategory,
    input_module: str,
    input_class: str,
    output_module: str,
    output_class: str,
    *,
    cli_tool: str,
    tool: Optional[str] = None,
    action_config_key: Optional[ActionConfigKey] = None,
    context_vars: ContextVars = (),
    argv_map: ArgvMap = DEFAULT_PIPELINE_ARGV_MAP,
    domain_effects: Optional[DomainEffects] = None,
    control: ActionControl = DEFAULT_ACTION_CONTROL,
    dispatch: ActionDispatch = DEFAULT_ACTION_DISPATCH,
    idempotency_enabled: bool = False,
    idempotency_opt_out_reason: Optional[str] = None,
    internal: bool = False,
) -> ActionCatalogEntry:
    return _entry(
        action_name,
        capability,
        schema_id,
        description,
        category,
        input_module,
        input_class,
        output_module,
        output_class,
        execution_mode="cli",
        cli_tool=cli_tool,
        tool=tool,
        action_config_key=action_config_key,
        context_vars=context_vars,
        argv_map=argv_map,
        domain_effects=domain_effects,
        control=control,
        dispatch=dispatch,
        idempotency_enabled=idempotency_enabled,
        idempotency_opt_out_reason=idempotency_opt_out_reason,
        internal=internal,
    )


def _in_process(
    action_name: str,
    capability: str,
    schema_id: str,
    description: str,
    category: ActionCategory,
    input_module: str,
    input_class: str,
    output_module: str,
    output_class: str,
    *,
    in_process_handler: str,
    tool: Optional[str] = None,
    cli_tool: Optional[str] = None,
    action_config_key: Optional[ActionConfigKey] = None,
    context_vars: ContextVars = (),
    domain_effects: Optional[DomainEffects] = None,
    control: ActionControl = DEFAULT_ACTION_CONTROL,
    dispatch: ActionDispatch = DEFAULT_ACTION_DISPATCH,
    idempotency_enabled: bool = False,
    idempotency_opt_out_reason: Optional[str] = None,
    internal: bool = False,
) -> ActionCatalogEntry:
    return _entry(
        action_name,
        capability,
        schema_id,
        description,
        category,
        input_module,
        input_class,
        output_module,
        output_class,
        execution_mode="in_process",
        in_process_handler=in_process_handler,
        tool=tool,
        cli_tool=cli_tool,
        action_config_key=action_config_key,
        context_vars=context_vars,
        argv_map=(),
        domain_effects=domain_effects,
        control=control,
        dispatch=dispatch,
        idempotency_enabled=idempotency_enabled,
        idempotency_opt_out_reason=idempotency_opt_out_reason,
        internal=internal,
    )


ACTION_CATALOG: Sequence[ActionCatalogEntry] = (
    _cli(
        "pipeline.centroid",
        "methyl-centroid",
        "pipeline.centroid",
        "Build per-group methylation centroids (chr×context HDF5 aggregates).",
        "modeling",
        _PIPELINE_MODULE,
        "CentroidTaskInput",
        _PIPELINE_MODULE,
        "CentroidTaskOutput",
        cli_tool="methyl-centroid",
        tool="MethylCentroid",
        action_config_key="centroid",
        context_vars=("group", "chromosome", "context", "outputDir", "stepOverride", "addSamples", "removeSamples", "centroidSeedDir"),
        argv_map=CENTROID_ARGV_MAP,
        domain_effects=_DE_CENTROID,
    ),
    _cli(
        "pipeline.detector",
        "methyl-detector",
        "pipeline.detector",
        "Detect differentially methylated positions between cohort pairs.",
        "modeling",
        _PIPELINE_MODULE,
        "DetectorTaskInput",
        _PIPELINE_MODULE,
        "DetectorTaskOutput",
        cli_tool="methyl-detector",
        tool="MethylDetector",
        action_config_key="detection",
        context_vars=("chromosome", "context", "comparison", "fixedDmpPanel", "stepOverride"),
        argv_map=(
            ("project", "--project"),
            ("projectPath", "--project"),
            ("group", "--group"),
            ("centroid1Dir", "--centroid1-dir"),
            ("centroid2Dir", "--centroid2-dir"),
            ("stepOverride", "--step-override"),
        ),
        domain_effects=_DE_DETECTOR,
    ),
    _cli(
        "pipeline.dmp_select",
        "methyl-dmp-select",
        "pipeline.dmp_select",
        "Select minimal discriminatory DMP panel from discovery exports (FeatureCuts / elbow).",
        "modeling",
        _PIPELINE_MODULE,
        "DmpSelectTaskInput",
        _PIPELINE_MODULE,
        "DmpSelectTaskOutput",
        cli_tool="methyl-dmp-select",
        tool="MethylDmpSelect",
        action_config_key="dmp_selection",
        context_vars=("chromosome", "context", "comparison", "discoveryCsv", "outputDir", "stepOverride"),
        argv_map=(
            ("project", "--project"),
            ("projectPath", "--project"),
            ("group", "--group"),
            ("chromosome", "--chromosome"),
            ("discoveryCsv", "--discovery-csv"),
            ("outputDir", "--output-dir"),
            ("stepOverride", "--step-override"),
        ),
        domain_effects=_DE_DETECTION_TYPES,
    ),
    _cli(
        "pipeline.mapper",
        "methyl-mapper",
        "pipeline.mapper",
        "Map DMPs to genes and genomic features.",
        "modeling",
        _PIPELINE_MODULE,
        "MapperTaskInput",
        _PIPELINE_MODULE,
        "MapperTaskOutput",
        cli_tool="methyl-mapper",
        tool="MethylMapper",
        action_config_key="mapper",
    ),
    _cli(
        "pipeline.derived_measures",
        "methyl-derived-measures",
        "pipeline.derived_measures",
        "Genome-wide per-sample derived methylation measures (sidecar CSV).",
        "modeling",
        _PIPELINE_MODULE,
        "DerivedMeasuresTaskInput",
        _PIPELINE_MODULE,
        "DerivedMeasuresTaskOutput",
        cli_tool="methyl-derived-measures",
        tool="MethylDerivedMeasures",
        action_config_key="derived_measures",
    ),
    _cli(
        "pipeline.cell_deconvolution",
        "methyl-cell-deconv",
        "pipeline.cell_deconvolution",
        "Cell-type deconvolution (Houseman or HiTIMED) from a path-configured atlas JSON; "
        "plant_tissue requires an operator-supplied seed/hierarchy basis (no blood fallback).",
        "modeling",
        _PIPELINE_MODULE,
        "CellDeconvolutionTaskInput",
        _PIPELINE_MODULE,
        "CellDeconvolutionTaskOutput",
        cli_tool="methyl-cell-deconv",
        tool="MethylCellDeconv",
        action_config_key="cell_deconvolution",
    ),
    _cli(
        "pipeline.residualize_fit",
        "methyl-residualize-fit",
        "pipeline.residualize_fit",
        "Train-only M-value residualization coefficients (no Group label; freeze for inference).",
        "modeling",
        _PIPELINE_MODULE,
        "ResidualizeFitTaskInput",
        _PIPELINE_MODULE,
        "ResidualizeFitTaskOutput",
        cli_tool="methyl-residualize-fit",
        tool="MethylResidualizeFit",
        action_config_key="residualize",
        context_vars=("outputDir", "stepOverride", "projectPath"),
        argv_map=(
            ("project", "--project"),
            ("projectPath", "--project"),
            ("outputDir", "--output-dir"),
            ("stepOverride", "--step-override"),
            ("resolvedConfigPath", "--resolved-config"),
        ),
    ),
    _cli(
        "pipeline.methylation_confounder_scores",
        "methyl-confounder-scores",
        "pipeline.methylation_confounder_scores",
        "Label-free methylation confounder scores (smoking, clock, BMI, inflammation).",
        "modeling",
        _PIPELINE_MODULE,
        "ConfounderScoresTaskInput",
        _PIPELINE_MODULE,
        "ConfounderScoresTaskOutput",
        cli_tool="methyl-confounder-scores",
        tool="MethylConfounderScores",
        action_config_key="methylation_confounder_scores",
        context_vars=("outputDir", "stepOverride", "projectPath"),
        argv_map=(
            ("project", "--project"),
            ("projectPath", "--project"),
            ("outputDir", "--output-dir"),
            ("stepOverride", "--step-override"),
            ("resolvedConfigPath", "--resolved-config"),
        ),
    ),
    _cli(
        "pipeline.info_measures",
        "methyl-infotheory",
        "pipeline.info_measures",
        "Read-level information-theoretic methylation measures (entropy, JSD, confirmation).",
        "modeling",
        _PIPELINE_MODULE,
        "InfoMeasuresTaskInput",
        _PIPELINE_MODULE,
        "InfoMeasuresTaskOutput",
        cli_tool="methyl-infotheory",
        tool="MethylInfoTheory",
        action_config_key="info_measures",
    ),
    _cli(
        "pipeline.mhb_mhl",
        "methyl-mhl",
        "pipeline.mhb_mhl",
        "Native MHB discovery and methylation haplotype load (MHL) from haplotype sidecars.",
        "modeling",
        _PIPELINE_MODULE,
        "MhbMhlTaskInput",
        _PIPELINE_MODULE,
        "MhbMhlTaskOutput",
        cli_tool="methyl-mhl",
        tool="MethylMhl",
        action_config_key="mhb_mhl",
    ),
    _cli(
        "pipeline.gene_select",
        "methyl-gene-select",
        "pipeline.gene_select",
        "Validation-driven gene panel selection (ECDF OvR FeatureCuts).",
        "modeling",
        _PIPELINE_MODULE,
        "GeneSelectTaskInput",
        _PIPELINE_MODULE,
        "GeneSelectTaskOutput",
        cli_tool="methyl-gene-select",
        tool="MethylGeneSelect",
        action_config_key="gene_selection",
        context_vars=("comparison", "runDir", "biomarkerFilter"),
        argv_map=(
            ("project", "--project"),
            ("projectPath", "--project"),
            ("runDir", "--run-dir"),
            ("biomarkerFilter", "--biomarker-filter"),
        ),
    ),
    _cli(
        "pipeline.gene_feature_select",
        "methyl-gene-feature-select",
        "pipeline.gene_feature_select",
        "Select structural gene × region features for tabular structural_scored backends.",
        "modeling",
        _PIPELINE_MODULE,
        "GeneFeatureSelectTaskInput",
        _PIPELINE_MODULE,
        "GeneFeatureSelectTaskOutput",
        cli_tool="methyl-gene-feature-select",
        tool="MethylGeneFeatureSelect",
        action_config_key="gene_selection",
        context_vars=("mapperDir", "outputDir"),
        argv_map=(
            ("mapperDir", "--mapper-dir"),
            ("outputDir", "--output-dir"),
        ),
    ),
    _cli(
        "pipeline.enricher",
        "methyl-enricher",
        "pipeline.enricher",
        "Functional enrichment on mapped gene sets.",
        "modeling",
        _PIPELINE_MODULE,
        "EnricherTaskInput",
        _PIPELINE_MODULE,
        "EnricherTaskOutput",
        cli_tool="methyl-enricher",
        tool="MethylEnricher",
        action_config_key="enricher",
    ),
    _cli(
        "pipeline.progression",
        "methyl-disease-progression",
        "pipeline.progression",
        "Ordered disease-stage progression analysis across comparisons.",
        "modeling",
        _PIPELINE_MODULE,
        "ProgressionTaskInput",
        _PIPELINE_MODULE,
        "ProgressionTaskOutput",
        cli_tool="methyl-disease-progression",
        tool="MethylDiseaseProgression",
        action_config_key="progression",
    ),
    _cli(
        "pipeline.classifier",
        "methyl-classifier",
        "pipeline.classifier",
        "Train production classifier from frozen centroids and DMP panel.",
        "modeling",
        _PIPELINE_MODULE,
        "ClassifierTaskInput",
        _PIPELINE_MODULE,
        "ClassifierTaskOutput",
        cli_tool="methyl-classifier",
        tool="MethylClassifier",
        action_config_key="classifier",
        argv_map=CLASSIFIER_ARGV_MAP,
    ),
    _cli(
        "pipeline.predictor",
        "methyl-predictor",
        "pipeline.predictor",
        "Run predictor on holdout samples using trained classifier.",
        "modeling",
        _PIPELINE_MODULE,
        "PredictorTaskInput",
        _PIPELINE_MODULE,
        "PredictorTaskOutput",
        cli_tool="methyl-predictor",
        tool="MethylPredictor",
        action_config_key="predictor",
        argv_map=CLASSIFIER_ARGV_MAP,
    ),
    _in_process(
        "context.resolve_project",
        "context.resolve-project",
        "context.resolve_project",
        "Materialize study manifest paths and cohorts into a typed ResolvedProject.",
        "validation",
        "methyl_worker.task_models.context_models",
        "ResolveProjectTaskInput",
        "methyl_worker.task_models.context_models",
        "ResolveProjectTaskOutput",
        in_process_handler="_handle_context_resolve_project",
        tool="ContextResolveProject",
        context_vars=("projectPath", "monteCarloRunsRoot", "cohortPathsList"),
        domain_effects=_DE_RESOLVE_PROJECT,
    ),
    _in_process(
        "sample.download_fastq",
        "sample.download-fastq",
        "sample.download_fastq",
        "Download sample FASTQ files from external object storage.",
        "sample_prep",
        _SAMPLE_MODULE,
        "DownloadFastqTaskInput",
        _SAMPLE_MODULE,
        "DownloadFastqTaskOutput",
        in_process_handler="_handle_download_fastq",
        tool="SampleDownloadFastq",
        context_vars=("sampleId", "sampleDir", "sampleRoot", "fastqSource"),
        domain_effects=_DE_DOWNLOAD,
        dispatch=DISPATCH_SAMPLE_AFFINITY,
    ),
    _in_process(
        "sample.demultiplex",
        "sample.demultiplex",
        "sample.demultiplex",
        "Demultiplex barcode-tagged PE FASTQs (epi-GBS / reduced-rep); barcode table via resolvedConfig.",
        "sample_prep",
        _SAMPLE_MODULE,
        "DemultiplexTaskInput",
        _SAMPLE_MODULE,
        "DemultiplexTaskOutput",
        in_process_handler="_handle_demultiplex",
        tool="SampleDemultiplex",
        context_vars=("sampleId", "sampleDir", "projectPath"),
        action_config_key="demultiplex",
        dispatch=DISPATCH_SAMPLE_AFFINITY,
    ),
    _in_process(
        "sample.docker_align",
        "align.docker",
        "sample.docker_align",
        "Align bisulfite FASTQs with a config-described Docker image (epi-GBS / alternate aligners).",
        "sample_prep",
        _SAMPLE_MODULE,
        "DockerAlignTaskInput",
        _SAMPLE_MODULE,
        "ParabricksTaskOutput",
        in_process_handler="_handle_docker_align",
        tool="SampleDockerAlign",
        context_vars=("sampleId", "sampleDir", "projectPath"),
        domain_effects=_DE_PARABRICKS,
        action_config_key="docker_align",
        control=CONTROL_STOPPABLE,
        dispatch=DISPATCH_SAMPLE_AFFINITY,
    ),
    _in_process(
        "sample.parabricks_fq2bam",
        "parabricks.fq2bam",
        "sample.parabricks_fq2bam",
        "Align bisulfite FASTQs to BAM using NVIDIA Clara Parabricks fq2bam_meth (Docker).",
        "sample_prep",
        _SAMPLE_MODULE,
        "ParabricksFq2bamTaskInput",
        _SAMPLE_MODULE,
        "ParabricksTaskOutput",
        in_process_handler="_handle_parabricks_fq2bam",
        tool="ParabricksFq2Bam",
        context_vars=("sampleId", "sampleDir", "projectPath"),
        domain_effects=_DE_PARABRICKS,
        action_config_key="parabricks",
        control=CONTROL_STOPPABLE,
        dispatch=DISPATCH_EXCLUSIVE_ONE,
    ),
    _in_process(
        "sample.parabricks_giraffe",
        "parabricks.giraffe",
        "sample.parabricks_giraffe",
        "Align FASTQs with Parabricks vg Giraffe (HPRC pangenome, GRCh38 surjection) + collectmultiplemetrics QC.",
        "sample_prep",
        _SAMPLE_MODULE,
        "ParabricksGiraffeTaskInput",
        _SAMPLE_MODULE,
        "ParabricksTaskOutput",
        in_process_handler="_handle_parabricks_giraffe",
        tool="ParabricksGiraffe",
        context_vars=("sampleId", "sampleDir", "projectPath"),
        domain_effects=_DE_PARABRICKS,
        action_config_key="parabricks",
        control=CONTROL_STOPPABLE,
        dispatch=DISPATCH_EXCLUSIVE_ONE,
    ),
    _in_process(
        "sample.methylgrapher_wgbs_align",
        "methylgrapher.wgbs_align",
        "sample.methylgrapher_wgbs_align",
        "Align WGBS FASTQs with methylGrapher dual C2T/G2A graphs and emit QC-compatible GRCh38 BAM.",
        "sample_prep",
        _SAMPLE_MODULE,
        "MethylGrapherWgbsAlignTaskInput",
        _SAMPLE_MODULE,
        "MethylGrapherWgbsAlignTaskOutput",
        in_process_handler="_handle_methylgrapher_wgbs_align",
        tool="MethylGrapherWgbsAlign",
        context_vars=("sampleId", "sampleDir", "projectPath"),
        domain_effects=_DE_PARABRICKS,
        action_config_key="methylgrapher_wgbs",
        control=CONTROL_STOPPABLE,
        dispatch=DISPATCH_EXCLUSIVE_ONE,
    ),
    _in_process(
        "sample.parabricks_rna_fq2bam",
        "parabricks.rna_fq2bam",
        "sample.parabricks_rna_fq2bam",
        "Align RNA-Seq FASTQs and quantify gene counts using NVIDIA Clara Parabricks rna_fq2bam (STAR, Docker).",
        "sample_prep",
        _RNA_SAMPLE_MODULE,
        "ParabricksRnaFq2bamTaskInput",
        _RNA_SAMPLE_MODULE,
        "RnaQuantTaskOutput",
        in_process_handler="_handle_parabricks_rna_fq2bam",
        tool="ParabricksRnaFq2Bam",
        context_vars=("sampleId", "sampleDir", "projectPath"),
        action_config_key="rna_align",
        control=CONTROL_STOPPABLE,
        dispatch=DISPATCH_EXCLUSIVE_ONE,
    ),
    _in_process(
        "sample.kallisto",
        "parabricks.kallisto",
        "sample.kallisto",
        "Quantify RNA-Seq FASTQs with NVIDIA Clara Parabricks kallisto pseudo-alignment (Docker).",
        "sample_prep",
        _RNA_SAMPLE_MODULE,
        "KallistoTaskInput",
        _RNA_SAMPLE_MODULE,
        "RnaQuantTaskOutput",
        in_process_handler="_handle_kallisto",
        tool="ParabricksKallisto",
        context_vars=("sampleId", "sampleDir", "projectPath"),
        dispatch=DISPATCH_SAMPLE_AFFINITY,
        action_config_key="rna_align",
        control=CONTROL_STOPPABLE,
    ),
    _in_process(
        "sample.rna_qc",
        "rna-qc",
        "sample.rna_qc",
        "RNA-Seq alignment/quantification QC guardrails (mapping/pseudoalignment rate, genes detected).",
        "sample_prep",
        _RNA_SAMPLE_MODULE,
        "RnaQcTaskInput",
        _RNA_SAMPLE_MODULE,
        "RnaQcTaskOutput",
        in_process_handler="_handle_rna_qc",
        tool="RnaAlignmentQc",
        dispatch=DISPATCH_SAMPLE_AFFINITY,
        cli_tool="rna-alignment-qc",
        action_config_key="rna_qc",
        context_vars=("projectPath", "sampleId", "sampleDir"),
        domain_effects=_DE_RNA_QC,
    ),
    _in_process(
        "sample.register_expression",
        "sample.register-expression",
        "sample.register_expression",
        "Normalize STAR gene counts or kallisto transcript abundances into a canonical expression.h5.",
        "sample_prep",
        _RNA_SAMPLE_MODULE,
        "RegisterExpressionTaskInput",
        _RNA_SAMPLE_MODULE,
        "RegisterExpressionTaskOutput",
        in_process_handler="_handle_register_expression",
        tool="RnaRegisterExpression",
        cli_tool="methyl-rna-register-expression",
        action_config_key="rna_align",
        context_vars=("sampleId", "sampleDir", "projectPath"),
    ),
    _cli(
        "pipeline.rna_de_select",
        "methyl-rna-de-select",
        "pipeline.rna_de_select",
        "RNA-Seq differential-expression gene panel selection + tabular classification (replaces methylation centroid/detector).",
        "modeling",
        _RNA_SAMPLE_MODULE,
        "RnaDeSelectTaskInput",
        _RNA_SAMPLE_MODULE,
        "RnaDeSelectTaskOutput",
        cli_tool="methyl-rna-de-select",
        tool="RnaDeSelect",
        action_config_key="rna_de_select",
        context_vars=("comparison", "outputDir"),
        argv_map=DEFAULT_PIPELINE_ARGV_MAP,
    ),
    _in_process(
        "sample.download_msdata",
        "sample.download-msdata",
        "sample.download_msdata",
        "Download proteomics MS data files (.raw/.mzML/.d) from external object storage.",
        "sample_prep",
        _SAMPLE_MODULE,
        "DownloadFastqTaskInput",
        _SAMPLE_MODULE,
        "DownloadFastqTaskOutput",
        in_process_handler="_handle_download_fastq",
        tool="SampleDownloadMsdata",
        context_vars=("sampleId", "sampleDir", "fastqSource"),
    ),
    _in_process(
        "sample.diann",
        "proteomics.diann",
        "sample.diann",
        "Quantify DIA mass-spec data with GPU DIA-NN (Docker; own image env, not Parabricks).",
        "sample_prep",
        _PROTEOMICS_MODULE,
        "DiannTaskInput",
        _PROTEOMICS_MODULE,
        "DiannTaskOutput",
        in_process_handler="_handle_diann",
        tool="ProteomicsDiann",
        context_vars=("sampleId", "sampleDir", "projectPath"),
        action_config_key="proteomics_quant",
    ),
    _in_process(
        "sample.sage",
        "proteomics.sage",
        "sample.sage",
        "DDA database search + LFQ quantification with Sage (Apache-2.0 Rust; CPU, open MSFragger alternative).",
        "sample_prep",
        _PROTEOMICS_MODULE,
        "SageTaskInput",
        _PROTEOMICS_MODULE,
        "SageTaskOutput",
        in_process_handler="_handle_sage",
        tool="ProteomicsSage",
        context_vars=("sampleId", "sampleDir", "projectPath"),
        action_config_key="proteomics_quant",
    ),
    _in_process(
        "sample.ingest_panel",
        "proteomics.panel_ingest",
        "sample.ingest_panel",
        "Ingest an affinity/aptamer panel matrix (Olink NPX / SomaScan RFU / open) to the abundance contract (CPU).",
        "sample_prep",
        _PROTEOMICS_MODULE,
        "IngestPanelTaskInput",
        _PROTEOMICS_MODULE,
        "AbundanceTaskOutput",
        in_process_handler="_handle_ingest_panel",
        tool="ProteomicsIngestPanel",
        context_vars=("sampleId", "sampleDir", "projectPath"),
        action_config_key="proteomics_quant",
    ),
    _in_process(
        "sample.dl_rescore",
        "proteomics.prosit",
        "sample.dl_rescore",
        "Prosit deep-learning rescoring of a DIA-NN report (GPU Docker) to lift IDs at fixed FDR.",
        "sample_prep",
        _PROTEOMICS_MODULE,
        "DlRescoreTaskInput",
        _PROTEOMICS_MODULE,
        "DlRescoreTaskOutput",
        in_process_handler="_handle_dl_rescore",
        tool="ProteomicsProsit",
        context_vars=("sampleId", "sampleDir", "projectPath"),
        action_config_key="proteomics_quant",
    ),
    _in_process(
        "sample.casanovo",
        "proteomics.casanovo",
        "sample.casanovo",
        "Casanovo de novo peptide sequencing (GPU Docker) for novel/variant peptides.",
        "sample_prep",
        _PROTEOMICS_MODULE,
        "CasanovoTaskInput",
        _PROTEOMICS_MODULE,
        "CasanovoTaskOutput",
        in_process_handler="_handle_casanovo",
        tool="ProteomicsCasanovo",
        context_vars=("sampleId", "sampleDir", "projectPath"),
        action_config_key="proteomics_quant",
    ),
    _in_process(
        "sample.register_abundance",
        "sample.register-abundance",
        "sample.register_abundance",
        "Normalize DIA-NN / panel proteomics output into a canonical abundance.h5.",
        "sample_prep",
        _PROTEOMICS_MODULE,
        "RegisterAbundanceTaskInput",
        _PROTEOMICS_MODULE,
        "AbundanceTaskOutput",
        in_process_handler="_handle_register_abundance",
        tool="ProteomicsRegisterAbundance",
        cli_tool="methyl-register-abundance",
        context_vars=("sampleId", "sampleDir", "projectPath"),
        action_config_key="proteomics_quant",
    ),
    _in_process(
        "sample.proteomics_qc",
        "proteomics-qc",
        "sample.proteomics_qc",
        "Proteomics QC guardrails (proteins identified, missingness).",
        "sample_prep",
        _PROTEOMICS_MODULE,
        "ProteomicsQcTaskInput",
        _PROTEOMICS_MODULE,
        "ProteomicsQcTaskOutput",
        in_process_handler="_handle_proteomics_qc",
        tool="ProteomicsQc",
        cli_tool="proteomics-qc",
        action_config_key="proteomics_qc",
        context_vars=("projectPath", "sampleId", "sampleDir"),
        domain_effects=_DE_PROTEOMICS_QC,
    ),
    _cli(
        "pipeline.protein_de_select",
        "methyl-protein-de-select",
        "pipeline.protein_de_select",
        "Proteomics differential-abundance protein panel selection + tabular classification.",
        "modeling",
        _PROTEOMICS_MODULE,
        "ProteinDeSelectTaskInput",
        _PROTEOMICS_MODULE,
        "ProteinDeSelectTaskOutput",
        cli_tool="methyl-protein-de-select",
        tool="ProteinDeSelect",
        action_config_key="protein_de_select",
        context_vars=("comparison", "outputDir"),
        argv_map=DEFAULT_PIPELINE_ARGV_MAP,
    ),
    _in_process(
        "sample.delete_fastqs",
        "sample.delete-fastqs",
        "sample.delete_fastqs",
        "Delete FASTQ files after final QC (pass or final fail) to reclaim storage.",
        "sample_prep",
        _SAMPLE_MODULE,
        "DeleteFastqsTaskInput",
        _SAMPLE_MODULE,
        "DeleteTaskOutput",
        in_process_handler="_handle_delete_fastqs",
        tool="SampleDeleteFastqs",
        context_vars=("sampleId", "sampleDir", "sampleRoot"),
        control=CONTROL_DRAIN_ONLY,
        dispatch=DISPATCH_SAMPLE_AFFINITY,
    ),
    _in_process(
        "sample.trim_fastq",
        "sample.trim-fastq",
        "sample.trim_fastq",
        "Trim Read 1/2 start or end bases with fastp before forced realign.",
        "sample_prep",
        _SAMPLE_MODULE,
        "TrimFastqTaskInput",
        _SAMPLE_MODULE,
        "TrimFastqTaskOutput",
        in_process_handler="_handle_trim_fastq",
        tool="SampleTrimFastq",
        context_vars=(
            "sampleId",
            "sampleDir",
            "trimFront1",
            "trimTail1",
            "trimFront2",
            "trimTail2",
            "remediationReason",
        ),
        dispatch=DISPATCH_SAMPLE_AFFINITY,
    ),
    _in_process(
        "sample.methyl_qc",
        "methyl-qc",
        "sample.methyl_qc",
        "Alignment QC metrics (Picard-style) with guardrails JSON export.",
        "sample_prep",
        _SAMPLE_MODULE,
        "MethylQcTaskInput",
        _SAMPLE_MODULE,
        "MethylQcTaskOutput",
        in_process_handler="_handle_methyl_qc",
        tool="MethylAlignmentQc",
        cli_tool="methyl-qc",
        action_config_key="alignment_qc",
        context_vars=("projectPath", "sampleId", "sampleDir", "primaryAnalyte", "alignmentMode"),
        domain_effects=_DE_METHYL_QC,
        control=CONTROL_STOPPABLE,
        dispatch=DISPATCH_SAMPLE_AFFINITY,
    ),
    _in_process(
        "sample.fragmentomics",
        "methyl-fragmentomics",
        "sample.fragmentomics",
        "cfDNA fragmentomic analysis (WPS, end motifs) when analyte is cfDNA.",
        "sample_prep",
        _SAMPLE_MODULE,
        "FragmentomicsTaskInput",
        _SAMPLE_MODULE,
        "FragmentomicsTaskOutput",
        in_process_handler="_handle_methyl_fragmentomics",
        tool="MethylFragmentomics",
        cli_tool="methyl-fragmentomics",
        action_config_key="fragmentomics",
        context_vars=("projectPath", "sampleId", "sampleDir"),
        domain_effects=_DE_FRAGMENTOMICS,
        control=CONTROL_STOPPABLE,
        dispatch=DISPATCH_SAMPLE_AFFINITY,
    ),
    _in_process(
        "sample.methyl_extract",
        "methyl-extract",
        "sample.methyl_extract",
        "Extract BAM to per-chromosome HDF5 via native MethylExtractor (resolvedConfig.methyl_extract).",
        "sample_prep",
        _SAMPLE_MODULE,
        "MethylExtractTaskInput",
        _SAMPLE_MODULE,
        "MethylExtractTaskOutput",
        in_process_handler="_handle_methyl_extract",
        tool="MethylExtract",
        action_config_key="methyl_extract",
        context_vars=("sampleId", "sampleDir", "projectPath"),
        domain_effects=_DE_METHYL_EXTRACT,
        control=CONTROL_STOPPABLE,
        dispatch=DISPATCH_SAMPLE_AFFINITY,
    ),
    _in_process(
        "sample.methylgrapher_wgbs_extract",
        "methylgrapher.wgbs_extract",
        "sample.methylgrapher_wgbs_extract",
        "Graph-aware methylGrapher extraction into {chrom}-{context}.h5 and patterns.h5 contracts.",
        "sample_prep",
        _SAMPLE_MODULE,
        "MethylGrapherWgbsExtractTaskInput",
        _SAMPLE_MODULE,
        "MethylExtractTaskOutput",
        in_process_handler="_handle_methylgrapher_wgbs_extract",
        tool="MethylGrapherWgbsExtract",
        action_config_key="methylgrapher_wgbs",
        context_vars=("sampleId", "sampleDir", "projectPath"),
        domain_effects=_DE_METHYL_EXTRACT,
        control=CONTROL_STOPPABLE,
        dispatch=DISPATCH_SAMPLE_AFFINITY,
    ),
    _in_process(
        "sample.extraction_qc",
        "methyl-extraction-qc",
        "sample.extraction_qc",
        "Evaluate MethylExtractor manifest guardrails and write extraction QC JSON.",
        "sample_prep",
        _SAMPLE_MODULE,
        "ExtractionQcTaskInput",
        _SAMPLE_MODULE,
        "ExtractionQcTaskOutput",
        in_process_handler="_handle_methyl_extraction_qc",
        tool="MethylExtractionQc",
        cli_tool="methyl-extraction-qc",
        action_config_key="extraction_qc",
        context_vars=("projectPath", "sampleId", "sampleDir"),
        domain_effects=_DE_EXTRACTION_QC,
        control=CONTROL_STOPPABLE,
        dispatch=DISPATCH_SAMPLE_AFFINITY,
    ),
    _in_process(
        "sample.archive_sample",
        "sample.archive-sample",
        "sample.archive_sample",
        "Archive sample bundle (FASTQs, QC JSON, HDF5) or QC-only reject record to durable storage.",
        "sample_prep",
        _SAMPLE_MODULE,
        "ArchiveSampleTaskInput",
        _SAMPLE_MODULE,
        "ArchiveSampleTaskOutput",
        in_process_handler="_handle_archive_sample",
        tool="SampleArchive",
        context_vars=(
            "sampleId",
            "sampleDir",
            "sampleRoot",
            "sampleDestination",
            "h5Destination",
            "projectPath",
            "mode",
            "rejectReason",
            "qcPath",
        ),
        domain_effects=_DE_ARCHIVE_SAMPLE,
        control=CONTROL_DRAIN_ONLY,
        dispatch=DISPATCH_SAMPLE_AFFINITY,
    ),
    _in_process(
        "sample.delete_bam",
        "sample.delete-bam",
        "sample.delete_bam",
        "Delete BAM after methylation extraction to reclaim storage.",
        "sample_prep",
        _SAMPLE_MODULE,
        "DeleteBamTaskInput",
        _SAMPLE_MODULE,
        "DeleteTaskOutput",
        in_process_handler="_handle_delete_bam",
        tool="SampleDeleteBam",
        context_vars=("sampleId", "sampleDir"),
        control=CONTROL_DRAIN_ONLY,
        dispatch=DISPATCH_SAMPLE_AFFINITY,
    ),
    _in_process(
        "sample.qc_failed",
        "sample.mark-failed",
        "sample.qc_failed",
        "Mark sample QC_FAILED and skip downstream steps when guardrails fail.",
        "sample_prep",
        _SAMPLE_MODULE,
        "QcFailedTaskInput",
        _SAMPLE_MODULE,
        "MarkFailedTaskOutput",
        in_process_handler="_handle_mark_failed",
        tool="SampleMarkFailed",
        context_vars=("sampleId", "sampleDir", "reason"),
        domain_effects=_DE_QC_FAILED,
        control=CONTROL_DRAIN_ONLY,
    ),
    _in_process(
        "validation.plan_iterations",
        "validation.plan-iterations",
        "validation.plan_iterations",
        "Monte Carlo planner: stratified per-cohort subsamples and materialize run projects.",
        "validation",
        "methyl_validation.workflow_planner",
        "ValidationPlanRequest",
        _VALIDATION_MODULE,
        "ValidationPlanTaskOutput",
        in_process_handler="_handle_validation_plan_iterations",
        action_config_key="validation",
        context_vars=("projectPath", "featureIterations", "qualityIterations"),
        domain_effects=_DE_PLAN_ITERATIONS,
    ),
    _in_process(
        "validation.stability",
        "validation.stability",
        "validation.stability",
        "Aggregate Monte Carlo DMP/gene stability and write production-ready panels.",
        "validation",
        _VALIDATION_MODULE,
        "StabilityTaskInput",
        _VALIDATION_MODULE,
        "ValidationStabilityOutput",
        in_process_handler="_handle_validation_stability",
        action_config_key="validation",
        context_vars=("projectPath", "monteCarloRunsRoot", "outputDir"),
        domain_effects=_DE_VALIDATION,
    ),
    _in_process(
        "validation.biomarker_filter",
        "validation.biomarker-filter",
        "validation.biomarker_filter",
        "In-process disease CSV + PPI hub biomarker gene pool filter (MC stability).",
        "validation",
        _PIPELINE_MODULE,
        "BiomarkerFilterTaskInput",
        _PIPELINE_MODULE,
        "BiomarkerFilterTaskOutput",
        in_process_handler="_handle_validation_biomarker_filter",
        action_config_key="validation",
        context_vars=("projectPath", "runDir"),
    ),
    _in_process(
        "validation.prepare_freeze_project",
        "validation.prepare-freeze-project",
        "validation.prepare_freeze_project",
        "Write production/project.json with fixed_dmp_panel for granular freeze workflows.",
        "validation",
        _VALIDATION_MODULE,
        "PrepareFreezeTaskInput",
        _VALIDATION_MODULE,
        "ValidationPrepareFreezeOutput",
        in_process_handler="_handle_validation_prepare_freeze",
        action_config_key="validation",
        context_vars=("projectPath", "stableDmpCsv", "productionOutputDir"),
        domain_effects=_DE_PREPARE_FREEZE,
    ),
    _in_process(
        "validation.finalize_freeze_model_bundle",
        "validation.finalize-freeze-model-bundle",
        "validation.finalize_freeze_model_bundle",
        "After freeze mapper: write mapper_dmp_annotations + frozen gene panel and wire production project.json.",
        "validation",
        _VALIDATION_MODULE,
        "FinalizeFreezeModelBundleTaskInput",
        _VALIDATION_MODULE,
        "ValidationFinalizeFreezeModelBundleOutput",
        in_process_handler="_handle_validation_finalize_freeze_model_bundle",
        action_config_key="validation",
        context_vars=("projectPath", "productionOutputDir"),
        domain_effects=_DE_VALIDATION,
    ),
    _in_process(
        "validation.stability_freeze_readiness",
        "validation.stability-freeze-readiness",
        "validation.stability_freeze_readiness",
        "Audit stability, freeze, and progression artifacts before model training.",
        "validation",
        _VALIDATION_MODULE,
        "FreezeReadinessTaskInput",
        _VALIDATION_MODULE,
        "ValidationFreezeReadinessOutput",
        in_process_handler="_handle_validation_stability_freeze_readiness",
        action_config_key="validation",
        context_vars=("projectPath",),
        domain_effects=_DE_VALIDATION,
    ),
    _in_process(
        "validation.link_artifacts",
        "validation.link-artifacts",
        "validation.link_artifacts",
        "Symlink centroids/detections from a source MC run into a model-mc iteration dir.",
        "validation",
        _VALIDATION_MODULE,
        "LinkArtifactsTaskInput",
        _VALIDATION_MODULE,
        "ValidationLinkArtifactsOutput",
        in_process_handler="_handle_validation_link_artifacts",
        context_vars=("sourceRunDir", "targetRunDir", "runDir"),
        domain_effects=_DE_VALIDATION,
        internal=True,
    ),
    _in_process(
        "validation.model_bundle",
        "validation.model-bundle",
        "validation.model_bundle",
        "Build model feature bundle (tabular/generative) from frozen detections.",
        "validation",
        _VALIDATION_MODULE,
        "ModelBundleTaskInput",
        _VALIDATION_MODULE,
        "ValidationModelBundleOutput",
        in_process_handler="_handle_validation_model_bundle",
        context_vars=("projectPath", "bundleDir"),
        domain_effects=_DE_VALIDATION,
        internal=True,
    ),
    _in_process(
        "validation.model_train",
        "validation.model-train",
        "validation.model_train",
        "Train tabular or generative backend model for one MC model iteration.",
        "validation",
        _VALIDATION_MODULE,
        "ModelTrainTaskInput",
        _VALIDATION_MODULE,
        "ValidationModelTrainOutput",
        in_process_handler="_handle_validation_model_train",
        context_vars=("projectPath", "backend", "runDir", "bundleH5", "outputDir"),
        domain_effects=_DE_VALIDATION,
        internal=True,
    ),
    _in_process(
        "validation.model_predict",
        "validation.model-predict",
        "validation.model_predict",
        "Predict with tabular or generative backend for one MC model iteration.",
        "validation",
        _VALIDATION_MODULE,
        "ModelPredictTaskInput",
        _VALIDATION_MODULE,
        "ValidationModelPredictOutput",
        in_process_handler="_handle_validation_model_predict",
        context_vars=("projectPath", "backend", "runDir"),
        domain_effects=_DE_VALIDATION,
        internal=True,
    ),
    _in_process(
        "validation.select_best_model",
        "validation.select-best-model",
        "validation.select_best_model",
        "Rank model-mc backends and build final production model on all data.",
        "validation",
        _VALIDATION_MODULE,
        "SelectBestModelTaskInput",
        _VALIDATION_MODULE,
        "ValidationSelectBestModelOutput",
        in_process_handler="_handle_validation_select_best_model",
        action_config_key="validation",
        context_vars=("projectPath", "modelMcRoot", "backends", "selectionMetric", "selectionStat"),
        domain_effects=_DE_SELECT_BEST_MODEL,
    ),
    _in_process(
        "validation.model_mc",
        "validation.model-mc",
        "validation.model_mc",
        "Monte Carlo model training across backends, optionally requiring strict reuse of primary centroid/detector artifacts.",
        "validation",
        _VALIDATION_MODULE,
        "ModelMcTaskInput",
        _VALIDATION_MODULE,
        "ValidationModelMcOutput",
        in_process_handler="_handle_validation_model_mc",
        action_config_key="validation",
        context_vars=(
            "projectPath",
            "monteCarloRunsRoot",
            "backends",
            "productionOutputDir",
        ),
        domain_effects=_DE_VALIDATION,
    ),
    _in_process(
        "validation.post_model_validation",
        "validation.post-model-validation",
        "validation.post_model_validation",
        "Holdout evaluation with frozen production artifacts (predictor-only / frozen inference).",
        "validation",
        _VALIDATION_MODULE,
        "PostModelValidationTaskInput",
        _VALIDATION_MODULE,
        "ValidationPostModelValidationOutput",
        in_process_handler="_handle_validation_post_model_validation",
        action_config_key="validation",
        context_vars=("projectPath", "outputDir", "productionOutputDir"),
        domain_effects=_DE_VALIDATION,
    ),
    # --- Typed workflow.compute (assign / WHILE / SWITCH helpers) ---
    _in_process(
        "workflow.const_bool",
        "workflow.const-bool",
        "workflow.const_bool",
        "Publish a boolean constant into scope (typed assign helper).",
        "validation",
        _WORKFLOW_MODULE,
        "ConstBoolTaskInput",
        _WORKFLOW_MODULE,
        "ConstBoolTaskOutput",
        in_process_handler="_handle_workflow_const_bool",
        tool="WorkflowConstBool",
        context_vars=("projectPath",),
        domain_effects=_DE_WORKFLOW_VALUE,
        idempotency_enabled=True,
    ),
    _in_process(
        "workflow.const_int",
        "workflow.const-int",
        "workflow.const_int",
        "Publish an integer constant into scope (typed assign helper).",
        "validation",
        _WORKFLOW_MODULE,
        "ConstIntTaskInput",
        _WORKFLOW_MODULE,
        "ConstIntTaskOutput",
        in_process_handler="_handle_workflow_const_int",
        tool="WorkflowConstInt",
        context_vars=("projectPath",),
        domain_effects=_DE_WORKFLOW_VALUE,
        idempotency_enabled=True,
    ),
    _in_process(
        "workflow.const_string",
        "workflow.const-string",
        "workflow.const_string",
        "Publish a string constant into scope (typed assign helper).",
        "validation",
        _WORKFLOW_MODULE,
        "ConstStringTaskInput",
        _WORKFLOW_MODULE,
        "ConstStringTaskOutput",
        in_process_handler="_handle_workflow_const_string",
        tool="WorkflowConstString",
        context_vars=("projectPath",),
        domain_effects=_DE_WORKFLOW_VALUE,
        idempotency_enabled=True,
    ),
    _in_process(
        "workflow.const_path",
        "workflow.const-path",
        "workflow.const_path",
        "Publish a filesystem path string into scope (typed assign helper).",
        "validation",
        _WORKFLOW_MODULE,
        "ConstPathTaskInput",
        _WORKFLOW_MODULE,
        "ConstPathTaskOutput",
        in_process_handler="_handle_workflow_const_path",
        tool="WorkflowConstPath",
        context_vars=("projectPath",),
        domain_effects=_DE_WORKFLOW_VALUE,
        idempotency_enabled=True,
    ),
    _in_process(
        "workflow.json_path_bool",
        "workflow.json-path-bool",
        "workflow.json_path_bool",
        "Extract a boolean from a JSON file via dotted path (typed assign helper).",
        "validation",
        _WORKFLOW_MODULE,
        "JsonPathBoolTaskInput",
        _WORKFLOW_MODULE,
        "JsonPathBoolTaskOutput",
        in_process_handler="_handle_workflow_json_path_bool",
        tool="WorkflowJsonPathBool",
        context_vars=("projectPath",),
        domain_effects=_DE_WORKFLOW_VALUE,
        idempotency_enabled=True,
    ),
    _in_process(
        "workflow.json_path_int",
        "workflow.json-path-int",
        "workflow.json_path_int",
        "Extract an integer from a JSON file via dotted path (typed assign helper).",
        "validation",
        _WORKFLOW_MODULE,
        "JsonPathIntTaskInput",
        _WORKFLOW_MODULE,
        "JsonPathIntTaskOutput",
        in_process_handler="_handle_workflow_json_path_int",
        tool="WorkflowJsonPathInt",
        context_vars=("projectPath",),
        domain_effects=_DE_WORKFLOW_VALUE,
        idempotency_enabled=True,
    ),
    _in_process(
        "workflow.json_path_string",
        "workflow.json-path-string",
        "workflow.json_path_string",
        "Extract a string from a JSON file via dotted path (typed assign helper).",
        "validation",
        _WORKFLOW_MODULE,
        "JsonPathStringTaskInput",
        _WORKFLOW_MODULE,
        "JsonPathStringTaskOutput",
        in_process_handler="_handle_workflow_json_path_string",
        tool="WorkflowJsonPathString",
        context_vars=("projectPath",),
        domain_effects=_DE_WORKFLOW_VALUE,
        idempotency_enabled=True,
    ),
    _in_process(
        "workflow.fs_stat",
        "workflow.fs-stat",
        "workflow.fs_stat",
        "Stat a filesystem path; value=exists (mtime included — CAAS skip may be inappropriate).",
        "validation",
        _WORKFLOW_MODULE,
        "FsStatTaskInput",
        _WORKFLOW_MODULE,
        "FsStatTaskOutput",
        in_process_handler="_handle_workflow_fs_stat",
        tool="WorkflowFsStat",
        context_vars=("projectPath",),
        domain_effects=_DE_WORKFLOW_VALUE,
        idempotency_opt_out_reason="mtime_sensitive",
    ),
)


# Legacy allow-list retained for docs/tests; eligibility is now default-on with opt-outs.
IDEMPOTENT_VALIDATION_ACTIONS: FrozenSet[str] = frozenset(
    {
        "validation.plan_iterations",
        "validation.stability",
        "validation.stability_freeze_readiness",
        "validation.prepare_freeze_project",
        "validation.finalize_freeze_model_bundle",
        "validation.model_mc",
        "validation.select_best_model",
        "validation.post_model_validation",
        "validation.model_bundle",
        "validation.model_train",
        "validation.model_predict",
        "validation.biomarker_filter",
        "validation.link_artifacts",
    }
)

# Hard opt-outs (destructive, control-flow, time-varying, remote etag-only).
# Sample-scoped align/prep uses CAAS unless the operator explicitly rejects it
# (see sample_content_store).
_HARD_IDEMPOTENCY_OPT_OUT: Dict[str, str] = {
    "workflow.fs_stat": "mtime_sensitive",
    "sample.delete_fastqs": "destructive",
    "sample.delete_bam": "destructive",
    "sample.archive_sample": "remote_upload_etag_only",
    "sample.qc_failed": "control_flow",
}

_SAMPLE_SCOPED_PREFIXES = (
    "sample.",
    "parabricks.",
    "proteomics.",
    "align.",
    "methylgrapher.",
)


def idempotency_opt_out_reason_for(
    entry: ActionCatalogEntry,
    input_json: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    """Return opt-out reason when CAAS skip/replay must not apply; else None."""
    if entry.idempotency_opt_out_reason:
        return entry.idempotency_opt_out_reason
    if entry.action_name in _HARD_IDEMPOTENCY_OPT_OUT:
        return _HARD_IDEMPOTENCY_OPT_OUT[entry.action_name]
    if entry.action_name.startswith(_SAMPLE_SCOPED_PREFIXES):
        try:
            from methyl_domain.sample_content_store import sample_caas_enabled_for_action

            if sample_caas_enabled_for_action(entry.action_name, input_json):
                return None
        except Exception:
            pass
        return "sample_caas_disabled"
    return None


def idempotency_enabled_for(
    entry: ActionCatalogEntry,
    input_json: Optional[Mapping[str, Any]] = None,
) -> bool:
    """Return True when signature-based skip/replay is active for this catalog entry.

    Default: every catalog ACTION is eligible unless an explicit opt-out applies
    (destructive sample ops, mtime probes, operator-rejected sample-scoped store).
    """
    return idempotency_opt_out_reason_for(entry, input_json) is None


def list_action_catalog() -> List[ActionCatalogEntry]:
    return list(ACTION_CATALOG)


def find_catalog_entry(action_name: str) -> Optional[ActionCatalogEntry]:
    for entry in ACTION_CATALOG:
        if entry.action_name == action_name:
            return entry
    return None


def control_for(action_name: str) -> ActionControl:
    """Return in-flight control flags for ``action_name`` (defaults if unknown)."""
    entry = find_catalog_entry(action_name)
    if entry is None:
        return DEFAULT_ACTION_CONTROL
    return entry.control


def find_catalog_entry_by_capability(capability: str) -> Optional[ActionCatalogEntry]:
    for entry in ACTION_CATALOG:
        if entry.capability == capability:
            return entry
    return None


def build_capability_handlers() -> Dict[str, str]:
    """Map capability to in-process handler name (cli actions have no handler)."""
    mapping: Dict[str, str] = {}
    for entry in ACTION_CATALOG:
        handler = entry.resolved_in_process_handler()
        if handler:
            mapping[entry.capability] = handler
    return mapping


def build_tool_cli_map() -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for entry in ACTION_CATALOG:
        if entry.tool and entry.cli_tool:
            mapping[entry.tool] = entry.cli_tool
    return mapping


def validate_catalog() -> List[str]:
    """Return all invariant violations across ACTION_CATALOG."""
    errors: List[str] = []
    for entry in ACTION_CATALOG:
        errors.extend(_entry_invariant_errors(entry))
    return errors


def validate_catalog_linkage() -> List[str]:
    """Backward-compatible alias for validate_catalog()."""
    return validate_catalog()
