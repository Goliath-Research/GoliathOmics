"""In-process handlers package (catalog-referenced by function name).

Public API matches the former ``methyl_worker.handlers`` module: ``execute_task``,
``CAPABILITY_HANDLERS``, ``TOOL_CLI``, and every ``_handle_*`` name used by the
action catalog.
"""

from __future__ import annotations

from .common import resolve_reference_fasta
from .context import (
    _handle_context_resolve_project,
    _handle_validation_plan_iterations,
)
from ..actions.base import build_action_from_catalog
from .dispatch import (
    CAPABILITY_HANDLERS,
    TOOL_CLI,
    _attach_domain_sample_ref,
    _log_action_execution,
    execute_task,
)

# Backward-compatible alias (formerly handlers._resolve_reference_fasta).
_resolve_reference_fasta = resolve_reference_fasta
from .sample_prep import (
    _handle_archive_sample,
    _handle_delete_bam,
    _handle_delete_fastqs,
    _handle_demultiplex,
    _handle_docker_align,
    _handle_download_fastq,
    _handle_mark_failed,
    _handle_methyl_extract,
    _handle_methyl_extraction_qc,
    _handle_methyl_fragmentomics,
    _handle_methyl_qc,
    _handle_parabricks_fq2bam,
    _handle_parabricks_giraffe,
    _handle_methylgrapher_wgbs_align,
    _handle_methylgrapher_wgbs_extract,
    _handle_trim_fastq,
)
from .rna_prep import (
    _handle_kallisto,
    _handle_parabricks_rna_fq2bam,
    _handle_register_expression,
    _handle_rna_qc,
)
from .proteomics_prep import (
    _handle_casanovo,
    _handle_diann,
    _handle_dl_rescore,
    _handle_ingest_panel,
    _handle_proteomics_qc,
    _handle_register_abundance,
    _handle_sage,
)
from .stub import _handle_stub_external
from .workflow_compute import (
    _handle_workflow_const_bool,
    _handle_workflow_const_int,
    _handle_workflow_const_path,
    _handle_workflow_const_string,
    _handle_workflow_fs_stat,
    _handle_workflow_json_path_bool,
    _handle_workflow_json_path_int,
    _handle_workflow_json_path_string,
)
from .validation import (
    _handle_validation_biomarker_filter,
    _handle_validation_link_artifacts,
    _handle_validation_model_bundle,
    _handle_validation_model_mc,
    _handle_validation_model_predict,
    _handle_validation_model_train,
    _handle_validation_post_model_validation,
    _handle_validation_prepare_freeze,
    _handle_validation_finalize_freeze_model_bundle,
    _handle_validation_select_best_model,
    _handle_validation_stability,
    _handle_validation_stability_freeze_readiness,
    _load_mc_config,
)

__all__ = [
    "CAPABILITY_HANDLERS",
    "TOOL_CLI",
    "build_action_from_catalog",
    "execute_task",
    "resolve_reference_fasta",
    "_resolve_reference_fasta",
    "_handle_archive_sample",
    "_handle_context_resolve_project",
    "_handle_delete_bam",
    "_handle_delete_fastqs",
    "_handle_demultiplex",
    "_handle_docker_align",
    "_handle_download_fastq",
    "_handle_mark_failed",
    "_handle_methyl_extract",
    "_handle_methyl_extraction_qc",
    "_handle_methyl_fragmentomics",
    "_handle_methyl_qc",
    "_handle_parabricks_fq2bam",
    "_handle_parabricks_giraffe",
    "_handle_methylgrapher_wgbs_align",
    "_handle_methylgrapher_wgbs_extract",
    "_handle_parabricks_rna_fq2bam",
    "_handle_kallisto",
    "_handle_rna_qc",
    "_handle_register_expression",
    "_handle_diann",
    "_handle_sage",
    "_handle_ingest_panel",
    "_handle_register_abundance",
    "_handle_proteomics_qc",
    "_handle_dl_rescore",
    "_handle_casanovo",
    "_handle_stub_external",
    "_handle_trim_fastq",
    "_handle_validation_biomarker_filter",
    "_handle_validation_link_artifacts",
    "_handle_validation_model_bundle",
    "_handle_validation_model_mc",
    "_handle_validation_model_predict",
    "_handle_validation_model_train",
    "_handle_validation_plan_iterations",
    "_handle_validation_post_model_validation",
    "_handle_validation_prepare_freeze",
    "_handle_validation_finalize_freeze_model_bundle",
    "_handle_validation_select_best_model",
    "_handle_validation_stability",
    "_handle_validation_stability_freeze_readiness",
    "_handle_workflow_const_bool",
    "_handle_workflow_const_int",
    "_handle_workflow_const_path",
    "_handle_workflow_const_string",
    "_handle_workflow_fs_stat",
    "_handle_workflow_json_path_bool",
    "_handle_workflow_json_path_int",
    "_handle_workflow_json_path_string",
    "_load_mc_config",
]
