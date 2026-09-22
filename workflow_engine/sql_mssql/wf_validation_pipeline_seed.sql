/*
  Workflow Engine (wf schema) - ValidationPipeline seed.

  Generic Monte Carlo / validation orchestration via FOREACH over context_json.iterations[].
  No domain tables in the engine: a planner worker populates iterations before instance start.

  Tree:
    SEQUENCE root
    ├─ FOREACH iterations (sequential) → SEQUENCE one_iteration
    │  ├─ ACTION validation_centroid
    │  └─ ACTION validation_detector
    └─ SEQUENCE final_sequence
       ├─ ACTION mapper
       ├─ ACTION enricher
       └─ ACTION progression

  Instance context_json example:
  - workflow_engine/sql_mssql/instance_context_examples/validation_mc.json

  Prerequisites:
  - wf_scope_variables.sql through wf_sql_foreach_support.sql
  - wf_sp_delete_workflow_def.sql

  Rebuild:
    EXEC wf.sp_delete_workflow_def @workflow_name = N'ValidationPipeline';
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

IF EXISTS (SELECT 1 FROM wf.workflow_def WHERE name = N'ValidationPipeline')
BEGIN
    PRINT N'Seed skipped: ValidationPipeline already exists. Rebuild: EXEC wf.sp_delete_workflow_def @workflow_name = N''ValidationPipeline'';';
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
      N'ValidationPipeline',
      N'Validation / Monte Carlo pipeline: FOREACH context_json.iterations (centroid+detector), then mapper/enricher/progression on base project.'
    );
    SET @def_id = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_version (workflow_def_id, version_major, version_minor, is_active, root_node_id)
    VALUES (@def_id, 1, 0, 1, NULL);
    SET @ver_id = SCOPE_IDENTITY();

    DECLARE
      @n_root BIGINT, @n_foreach BIGINT, @n_seq_iter BIGINT,
      @n_centroid BIGINT, @n_detector BIGINT,
      @n_final BIGINT, @n_mapper BIGINT, @n_enricher BIGINT, @n_prog BIGINT;

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'SEQUENCE', N'root', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
    SET @n_root = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'FOREACH', N'foreach_iterations', NULL, NULL, NULL, NULL, NULL, NULL, N'iterations', N'iteration', N'iterIndex', 0);
    SET @n_foreach = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'SEQUENCE', N'one_iteration', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
    SET @n_seq_iter = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'ACTION', N'validation_centroid', @a_centroid, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
    SET @n_centroid = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'ACTION', N'validation_detector', @a_detector, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
    SET @n_detector = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var, foreach_collection_var, foreach_item_var, foreach_index_var, foreach_parallel)
    VALUES (@ver_id, N'SEQUENCE', N'final_sequence', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0);
    SET @n_final = SCOPE_IDENTITY();

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
      (@n_root, @n_foreach, 0, N'SEQUENCE', NULL, NULL, 0),
      (@n_root, @n_final, 1, N'SEQUENCE', NULL, NULL, 0),
      (@n_foreach, @n_seq_iter, 0, N'BODY', NULL, NULL, 0),
      (@n_seq_iter, @n_centroid, 0, N'SEQUENCE', NULL, NULL, 0),
      (@n_seq_iter, @n_detector, 1, N'SEQUENCE', NULL, NULL, 0),
      (@n_final, @n_mapper, 0, N'SEQUENCE', NULL, NULL, 0),
      (@n_final, @n_enricher, 1, N'SEQUENCE', NULL, NULL, 0),
      (@n_final, @n_prog, 2, N'SEQUENCE', NULL, NULL, 0);

    INSERT INTO wf.workflow_input_template (workflow_node_id, template_json)
    VALUES
      (@n_centroid, N'{"tool":"MethylCentroid","project":${var.projectPath},"phase":${var.phase},"runId":${var.runId},"taskConfig":${var.taskConfig}}'),
      (@n_detector, N'{"tool":"MethylDetector","project":${var.projectPath},"phase":${var.phase},"runId":${var.runId},"taskConfig":${var.taskConfig}}'),
      (@n_mapper, N'{"tool":"${var.workerToolMapper}","project":"${var.projectPath}"}'),
      (@n_enricher, N'{"tool":"${var.workerToolEnricher}","project":"${var.projectPath}"}'),
      (@n_prog, N'{"tool":"${var.workerToolProgression}","project":"${var.projectPath}","orderedComparisonLabels":${var.orderedComparisonLabels}}');

    UPDATE wf.workflow_version SET root_node_id = @n_root WHERE id = @ver_id;

    PRINT CONCAT(N'Seeded ValidationPipeline definition_id=', @def_id, N' version_id=', @ver_id);
END
GO
