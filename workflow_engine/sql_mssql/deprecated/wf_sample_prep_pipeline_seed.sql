/*
  DEPRECATED — use DomainProgram deploy instead.

  SamplePrepPipeline is defined in:
    workflow_engine/domain/fixtures/sample_prep.program.json
  Deploy via:
    bash scripts/deploy_workflow_definitions.sh

  This SQL seed is retained for legacy SQL Server environments only.
  It does NOT include extraction_qc, upload_h5, or the full fastp remediation graph.

  Workflow Engine (wf schema) - SamplePrepPipeline seed (legacy).

  Per-sample upstream preprocessing: FASTQ ingest -> Parabricks -> methyl-qc gate (+ optional R2 trim remediation) ->
  delete_fastqs on terminal paths -> conditional cfDNA fragmentomics -> MethylExtractor -> BAM cleanup.

  Tree (~14+ nodes):
    SEQUENCE root
    └─ FOREACH samples (parallel) → SEQUENCE one_sample
       ├─ ACTION download_fastq, parabricks_fq2bam, methyl_qc
       └─ IF qc_passed (qcPass)
          ├─ THEN delete_fastqs → [IF is_cfdna → fragmentomics] → extract → delete_bam
          └─ ELSE IF remediateR2Trim → trim → realign → methyl_qc retry → pass/fail paths with delete_fastqs
             ELSE delete_fastqs → qc_failed

  Instance context_json example:
  - workflow_engine/sql_mssql/instance_context_examples/sample_prep_plasma.json

  Prerequisites:
  - wf_scope_variables.sql through wf_sql_foreach_support.sql
  - wf_sp_delete_workflow_def.sql

  Rebuild:
    EXEC wf.sp_delete_workflow_def @workflow_name = N'SamplePrepPipeline';
    -- re-run this script
*/

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

IF COL_LENGTH('wf.workflow_node', 'foreach_collection_var') IS NULL
BEGIN
    RAISERROR(N'Prerequisite missing: run wf_sql_foreach_support.sql first.', 16, 1);
    RETURN;
END
GO

IF EXISTS (SELECT 1 FROM wf.workflow_def WHERE name = N'SamplePrepPipeline')
BEGIN
    PRINT N'Seed skipped: SamplePrepPipeline already exists. Rebuild: EXEC wf.sp_delete_workflow_def @workflow_name = N''SamplePrepPipeline'';';
END
ELSE
BEGIN
    DECLARE @def_id BIGINT;
    DECLARE @ver_id BIGINT;

    MERGE wf.workflow_action AS t
    USING (VALUES
      (N'sample.download_fastq', N'sample.download-fastq'),
      (N'sample.parabricks_fq2bam', N'parabricks.fq2bam'),
      (N'sample.delete_fastqs', N'sample.delete-fastqs'),
      (N'sample.methyl_qc', N'methyl-qc'),
      (N'sample.fragmentomics', N'methyl-fragmentomics'),
      (N'sample.methyl_extract', N'methyl-extract'),
      (N'sample.delete_bam', N'sample.delete-bam'),
      (N'sample.qc_failed', N'sample.mark-failed')
    ) AS s(action_name, capability)
    ON t.action_name = s.action_name
    WHEN NOT MATCHED THEN
      INSERT (action_name, capability, payload_schema_ref)
      VALUES (s.action_name, s.capability, NULL);

    DECLARE @a_download BIGINT = (SELECT id FROM wf.workflow_action WHERE action_name = N'sample.download_fastq');
    DECLARE @a_parabricks BIGINT = (SELECT id FROM wf.workflow_action WHERE action_name = N'sample.parabricks_fq2bam');
    DECLARE @a_delete_fq BIGINT = (SELECT id FROM wf.workflow_action WHERE action_name = N'sample.delete_fastqs');
    DECLARE @a_methyl_qc BIGINT = (SELECT id FROM wf.workflow_action WHERE action_name = N'sample.methyl_qc');
    DECLARE @a_fragmentomics BIGINT = (SELECT id FROM wf.workflow_action WHERE action_name = N'sample.fragmentomics');
    DECLARE @a_extract BIGINT = (SELECT id FROM wf.workflow_action WHERE action_name = N'sample.methyl_extract');
    DECLARE @a_delete_bam BIGINT = (SELECT id FROM wf.workflow_action WHERE action_name = N'sample.delete_bam');
    DECLARE @a_qc_failed BIGINT = (SELECT id FROM wf.workflow_action WHERE action_name = N'sample.qc_failed');

    INSERT INTO wf.workflow_def (name, description)
    VALUES (
      N'SamplePrepPipeline',
      N'Per-sample upstream preprocessing: FASTQ download, Parabricks alignment, QC gate, optional cfDNA fragmentomics, methylation extraction, cleanup. Instance context_json.samples[] drives FOREACH fan-out.'
    );
    SET @def_id = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_version (workflow_def_id, version_major, version_minor, is_active, root_node_id)
    VALUES (@def_id, 1, 0, 1, NULL);
    SET @ver_id = SCOPE_IDENTITY();

    DECLARE
      @n_root BIGINT, @n_foreach_samples BIGINT, @n_one_sample BIGINT,
      @n_download BIGINT, @n_parabricks BIGINT, @n_delete_fq BIGINT, @n_methyl_qc BIGINT,
      @n_if_qc BIGINT, @n_on_pass BIGINT, @n_if_cfdna BIGINT, @n_fragmentomics BIGINT,
      @n_extract BIGINT, @n_delete_bam BIGINT, @n_qc_failed BIGINT;

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'SEQUENCE', N'root', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
    SET @n_root = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'FOREACH', N'foreach_samples', NULL, NULL, NULL, NULL, NULL, NULL, N'samples', N'sample', N'sampleIndex', 1);
    SET @n_foreach_samples = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'SEQUENCE', N'one_sample', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
    SET @n_one_sample = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'ACTION', N'download_fastq', @a_download, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
    SET @n_download = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'ACTION', N'parabricks_fq2bam', @a_parabricks, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
    SET @n_parabricks = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'ACTION', N'delete_fastqs', @a_delete_fq, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
    SET @n_delete_fq = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'ACTION', N'methyl_qc', @a_methyl_qc, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
    SET @n_methyl_qc = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'IF', N'if_qc_passed', NULL, NULL, NULL, NULL, N'qcPass', NULL, NULL, NULL, NULL, 0);
    SET @n_if_qc = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'SEQUENCE', N'on_pass', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
    SET @n_on_pass = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'IF', N'if_is_cfdna', NULL, NULL, NULL, NULL, N'isCfdna', NULL, NULL, NULL, NULL, 0);
    SET @n_if_cfdna = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'ACTION', N'methyl_fragmentomics', @a_fragmentomics, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
    SET @n_fragmentomics = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'ACTION', N'methyl_extract', @a_extract, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
    SET @n_extract = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'ACTION', N'delete_bam', @a_delete_bam, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
    SET @n_delete_bam = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'ACTION', N'qc_failed', @a_qc_failed, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
    SET @n_qc_failed = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_edge (parent_node_id, child_node_id, child_order, branch_kind, condition_expr, switch_case_value, is_default)
    VALUES
      (@n_root, @n_foreach_samples, 0, N'SEQUENCE', NULL, NULL, 0),
      (@n_foreach_samples, @n_one_sample, 0, N'BODY', NULL, NULL, 0),
      (@n_one_sample, @n_download, 0, N'SEQUENCE', NULL, NULL, 0),
      (@n_one_sample, @n_parabricks, 1, N'SEQUENCE', NULL, NULL, 0),
      (@n_one_sample, @n_delete_fq, 2, N'SEQUENCE', NULL, NULL, 0),
      (@n_one_sample, @n_methyl_qc, 3, N'SEQUENCE', NULL, NULL, 0),
      (@n_one_sample, @n_if_qc, 4, N'SEQUENCE', NULL, NULL, 0),
      (@n_if_qc, @n_on_pass, 0, N'THEN', NULL, NULL, 0),
      (@n_if_qc, @n_qc_failed, 1, N'ELSE', NULL, NULL, 0),
      (@n_on_pass, @n_if_cfdna, 0, N'SEQUENCE', NULL, NULL, 0),
      (@n_on_pass, @n_extract, 1, N'SEQUENCE', NULL, NULL, 0),
      (@n_on_pass, @n_delete_bam, 2, N'SEQUENCE', NULL, NULL, 0),
      (@n_if_cfdna, @n_fragmentomics, 0, N'THEN', NULL, NULL, 0);

    INSERT INTO wf.workflow_input_template (workflow_node_id, template_json)
    VALUES
      (@n_download, N'{"tool":"SampleDownloadFastq","sampleId":"${var.sampleId}","sampleDir":"${var.sampleDir}","fastqSource":"${var.fastqSource}"}'),
      (@n_parabricks, N'{"tool":"ParabricksFq2Bam","sampleId":"${var.sampleId}","sampleDir":"${var.sampleDir}","referenceFasta":"${var.referenceFasta}","referenceGtf":"${var.referenceGtf}"}'),
      (@n_delete_fq, N'{"tool":"SampleDeleteFastqs","sampleId":"${var.sampleId}","sampleDir":"${var.sampleDir}"}'),
      (@n_methyl_qc, N'{"tool":"MethylAlignmentQc","project":"${var.projectPath}","sampleId":"${var.sampleId}","sampleDir":"${var.sampleDir}","primaryAnalyte":"${var.primaryAnalyte}"}'),
      (@n_fragmentomics, N'{"tool":"MethylFragmentomics","project":"${var.projectPath}","sampleId":"${var.sampleId}","sampleDir":"${var.sampleDir}"}'),
      (@n_extract, N'{"tool":"MethylExtract","sampleId":"${var.sampleId}","sampleDir":"${var.sampleDir}","project":"${var.projectPath}","referenceFasta":"${var.referenceFasta}"}'),
      (@n_delete_bam, N'{"tool":"SampleDeleteBam","sampleId":"${var.sampleId}","sampleDir":"${var.sampleDir}"}'),
      (@n_qc_failed, N'{"tool":"SampleMarkFailed","sampleId":"${var.sampleId}","sampleDir":"${var.sampleDir}","reason":"alignment_qc_failed"}');

    INSERT INTO wf.variable_output_binding (workflow_node_id, var_name, source_kind, source_json_path)
    VALUES (@n_methyl_qc, N'qcPass', N'output_path', N'$.guardrails.overall_pass');

    UPDATE wf.workflow_version SET root_node_id = @n_root WHERE id = @ver_id;

    PRINT CONCAT(N'Seeded SamplePrepPipeline definition_id=', @def_id, N' version_id=', @ver_id);
END
GO
