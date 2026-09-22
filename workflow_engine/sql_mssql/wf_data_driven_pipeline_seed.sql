/*
  Workflow Engine (wf schema) - DataDrivenPipeline seed.

  Domain-neutral compiled workflow using FOREACH over instance context_json arrays.
  No project-specific node explosion: comparisons and chromosomes come from runtime data.

  Tree (~15 nodes):
    SEQUENCE root
    ├─ FOREACH comparisons (parallel) → SEQUENCE one_comparison
    │  └─ FOREACH chromosomes (parallel) → SEQUENCE one_chromosome
    │     ├─ PARALLEL centroids → centroid_g1, centroid_g2
    │     └─ ACTION detect
    └─ SEQUENCE post_pipeline → mapper, enricher, progression

  Instance context_json drives fan-out. Example payloads:
  - workflow_engine/sql_mssql/instance_context_examples/pca_ovr.json
  - workflow_engine/sql_mssql/instance_context_examples/minimal_two_task.json

  Prerequisites:
  - wf_scope_variables.sql through wf_sql_foreach_support.sql
  - wf_sp_delete_workflow_def.sql

  Rebuild:
    EXEC wf.sp_delete_workflow_def @workflow_name = N'DataDrivenPipeline';
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

IF EXISTS (SELECT 1 FROM wf.workflow_def WHERE name = N'DataDrivenPipeline')
BEGIN
    PRINT N'Seed skipped: DataDrivenPipeline already exists. Rebuild: EXEC wf.sp_delete_workflow_def @workflow_name = N''DataDrivenPipeline'';';
END
ELSE
BEGIN
    DECLARE @def_id BIGINT;
    DECLARE @ver_id BIGINT;

    MERGE wf.workflow_action AS t
    USING (VALUES
      (N'pipeline.centroid', N'methyl-centroid'),
      (N'pipeline.detector', N'methyl-detector'),
      (N'pipeline.mapper', N'methyl-mapper'),
      (N'pipeline.enricher', N'methyl-enricher'),
      (N'pipeline.progression', N'methyl-disease-progression')
    ) AS s(action_name, capability)
    ON t.action_name = s.action_name
    WHEN NOT MATCHED THEN
      INSERT (action_name, capability, payload_schema_ref)
      VALUES (s.action_name, s.capability, NULL);

    DECLARE @a_centroid BIGINT = (SELECT id FROM wf.workflow_action WHERE action_name = N'pipeline.centroid');
    DECLARE @a_detector BIGINT = (SELECT id FROM wf.workflow_action WHERE action_name = N'pipeline.detector');
    DECLARE @a_mapper BIGINT = (SELECT id FROM wf.workflow_action WHERE action_name = N'pipeline.mapper');
    DECLARE @a_enricher BIGINT = (SELECT id FROM wf.workflow_action WHERE action_name = N'pipeline.enricher');
    DECLARE @a_progression BIGINT = (SELECT id FROM wf.workflow_action WHERE action_name = N'pipeline.progression');

    INSERT INTO wf.workflow_def (name, description)
    VALUES (
      N'DataDrivenPipeline',
      N'Generic data-driven pipeline: FOREACH comparisons × FOREACH chromosomes (parallel), then sequential post steps. Instance context_json supplies collections.'
    );
    SET @def_id = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_version (workflow_def_id, version_major, version_minor, is_active, root_node_id)
    VALUES (@def_id, 1, 0, 1, NULL);
    SET @ver_id = SCOPE_IDENTITY();

    DECLARE
      @n_root BIGINT, @n_foreach_cmp BIGINT, @n_seq_cmp BIGINT,
      @n_foreach_chr BIGINT, @n_seq_chr BIGINT, @n_par_cent BIGINT,
      @n_c1 BIGINT, @n_c2 BIGINT, @n_det BIGINT,
      @n_post BIGINT, @n_mapper BIGINT, @n_enricher BIGINT, @n_prog BIGINT;

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'SEQUENCE', N'root', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
    SET @n_root = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'FOREACH', N'foreach_comparisons', NULL, NULL, NULL, NULL, NULL, NULL, N'comparisons', N'comparison', N'cmpIndex', 1);
    SET @n_foreach_cmp = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'SEQUENCE', N'one_comparison', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
    SET @n_seq_cmp = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'FOREACH', N'foreach_chromosomes', NULL, NULL, NULL, NULL, NULL, NULL, N'chromosomes', N'chromosome', N'chrIndex', 1);
    SET @n_foreach_chr = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'SEQUENCE', N'one_chromosome', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
    SET @n_seq_chr = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'PARALLEL', N'centroids', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
    SET @n_par_cent = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'ACTION', N'centroid_g1', @a_centroid, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
    SET @n_c1 = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'ACTION', N'centroid_g2', @a_centroid, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
    SET @n_c2 = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'ACTION', N'detect', @a_detector, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
    SET @n_det = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'SEQUENCE', N'post_pipeline', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
    SET @n_post = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'ACTION', N'mapper', @a_mapper, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
    SET @n_mapper = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'ACTION', N'enricher', @a_enricher, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
    SET @n_enricher = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'ACTION', N'progression', @a_progression, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
    SET @n_prog = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_edge (parent_node_id, child_node_id, child_order, branch_kind, condition_expr, switch_case_value, is_default)
    VALUES
      (@n_root, @n_foreach_cmp, 0, N'SEQUENCE', NULL, NULL, 0),
      (@n_root, @n_post, 1, N'SEQUENCE', NULL, NULL, 0),
      (@n_foreach_cmp, @n_seq_cmp, 0, N'BODY', NULL, NULL, 0),
      (@n_seq_cmp, @n_foreach_chr, 0, N'SEQUENCE', NULL, NULL, 0),
      (@n_foreach_chr, @n_seq_chr, 0, N'BODY', NULL, NULL, 0),
      (@n_seq_chr, @n_par_cent, 0, N'SEQUENCE', NULL, NULL, 0),
      (@n_seq_chr, @n_det, 1, N'SEQUENCE', NULL, NULL, 0),
      (@n_par_cent, @n_c1, 0, N'PARALLEL', NULL, NULL, 0),
      (@n_par_cent, @n_c2, 1, N'PARALLEL', NULL, NULL, 0),
      (@n_post, @n_mapper, 0, N'SEQUENCE', NULL, NULL, 0),
      (@n_post, @n_enricher, 1, N'SEQUENCE', NULL, NULL, 0),
      (@n_post, @n_prog, 2, N'SEQUENCE', NULL, NULL, 0);

    INSERT INTO wf.workflow_input_template (workflow_node_id, template_json)
    VALUES
      (@n_c1, N'{"tool":"${var.workerToolCentroid}","project":"${var.projectPath}","group":"${var.group1Label}","chromosome":"${var.chromosome}","context":"${var.context}","comparison":"${var.label}","outputDir":"${var.centroid1Dir}"}'),
      (@n_c2, N'{"tool":"${var.workerToolCentroid}","project":"${var.projectPath}","group":"${var.group2Label}","chromosome":"${var.chromosome}","context":"${var.context}","comparison":"${var.label}","outputDir":"${var.centroid2Dir}"}'),
      (@n_det, N'{"tool":"${var.workerToolDetector}","project":"${var.projectPath}","chromosome":"${var.chromosome}","context":"${var.context}","comparison":"${var.label}","centroid1Dir":"${var.centroid1Dir}","centroid2Dir":"${var.centroid2Dir}","outputDir":"${var.detectOutDir}"}'),
      (@n_mapper, N'{"tool":"${var.workerToolMapper}","project":"${var.projectPath}"}'),
      (@n_enricher, N'{"tool":"${var.workerToolEnricher}","project":"${var.projectPath}"}'),
      (@n_prog, N'{"tool":"${var.workerToolProgression}","project":"${var.projectPath}","orderedComparisonLabels":${var.orderedComparisonLabels}}');

    UPDATE wf.workflow_version SET root_node_id = @n_root WHERE id = @ver_id;

    PRINT CONCAT(N'Seeded DataDrivenPipeline definition_id=', @def_id, N' version_id=', @ver_id);
END
GO
