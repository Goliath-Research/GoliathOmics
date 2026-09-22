/*
  DEPRECATED — use ValidationPipeline + context_json.iterations[] instead.

  Workflow Engine (wf schema) - MethylValidation explicit workflow seed (legacy MC bridge).

  Superseded by:
  - wf_validation_pipeline_seed.sql (ValidationPipeline)
  - contract/validation_planner_capabilities.md (planner populates iterations[])

  Encodes this runtime shape:
    1) MC feature loop (repeat N)
       - MethylCentroid
       - MethylDetector
    2) Post-loop quality/final sample set
       - MethylCentroid
       - MethylDetector
       - MethylMapper
       - MethylEnricher
       - MethylDiseaseProgression

  Prerequisites:
  - Base wf schema deployed
  - wf_scope_variables.sql applied
  - (deprecated) wf_monte_carlo_support.sql — no longer required
*/

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

DECLARE @feature_iterations INT = 30;
DECLARE @quality_iterations INT = 20;

IF @feature_iterations < 1 SET @feature_iterations = 1;
IF @quality_iterations < 1 SET @quality_iterations = 1;

IF COL_LENGTH('wf.workflow_node', 'condition_var') IS NULL
   OR COL_LENGTH('wf.workflow_node', 'switch_var') IS NULL
   OR OBJECT_ID(N'wf.node_scope_default', N'U') IS NULL
BEGIN
    RAISERROR(N'Prerequisite missing: run wf_scope_variables.sql first.', 16, 1);
    RETURN;
END
GO

IF EXISTS (SELECT 1 FROM wf.workflow_def WHERE name = N'MethylValidationFlow')
BEGIN
    PRINT N'DEPRECATED seed skipped: MethylValidationFlow already exists. Prefer ValidationPipeline (wf_validation_pipeline_seed.sql).';
END
ELSE
BEGIN
    PRINT N'WARNING: MethylValidationFlow is deprecated. Deploy ValidationPipeline + validation planner instead.';
    DECLARE @feature_iterations INT = 30;
    DECLARE @quality_iterations INT = 20;
    IF @feature_iterations < 1 SET @feature_iterations = 1;
    IF @quality_iterations < 1 SET @quality_iterations = 1;

    DECLARE @def_id BIGINT;
    DECLARE @ver_id BIGINT;

    MERGE wf.workflow_action AS t
    USING (VALUES
      (N'methylvalidation.mc.centroid', N'methyl-centroid'),
      (N'methylvalidation.mc.detector', N'methyl-detector'),
      (N'methylvalidation.final.centroid', N'methyl-centroid'),
      (N'methylvalidation.final.detector', N'methyl-detector'),
      (N'methylvalidation.final.mapper', N'methyl-mapper'),
      (N'methylvalidation.final.enricher', N'methyl-enricher'),
      (N'methylvalidation.final.disease_progression', N'methyl-disease-progression')
    ) AS s(action_name, capability)
    ON t.action_name = s.action_name
    WHEN NOT MATCHED THEN
      INSERT (action_name, capability, payload_schema_ref)
      VALUES (s.action_name, s.capability, NULL);

    INSERT INTO wf.workflow_def (name, description)
    VALUES (
      N'MethylValidationFlow',
      N'Explicit MethylValidation workflow: MC feature loop (centroid/detector) then final sample-set centroid/detector/mapper/enricher/disease progression.'
    );
    SET @def_id = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_version (workflow_def_id, version_major, version_minor, is_active, root_node_id)
    VALUES (@def_id, 1, 0, 1, NULL);
    SET @ver_id = SCOPE_IDENTITY();

    DECLARE
      @n_root BIGINT, @n_mc_repeat BIGINT, @n_mc_seq BIGINT,
      @n_mc_centroid BIGINT, @n_mc_detector BIGINT,
      @n_final_seq BIGINT, @n_final_centroid BIGINT, @n_final_detector BIGINT,
      @n_final_mapper BIGINT, @n_final_enricher BIGINT, @n_final_progression BIGINT;

    DECLARE
      @a_mc_centroid BIGINT = (SELECT id FROM wf.workflow_action WHERE action_name = N'methylvalidation.mc.centroid'),
      @a_mc_detector BIGINT = (SELECT id FROM wf.workflow_action WHERE action_name = N'methylvalidation.mc.detector'),
      @a_final_centroid BIGINT = (SELECT id FROM wf.workflow_action WHERE action_name = N'methylvalidation.final.centroid'),
      @a_final_detector BIGINT = (SELECT id FROM wf.workflow_action WHERE action_name = N'methylvalidation.final.detector'),
      @a_final_mapper BIGINT = (SELECT id FROM wf.workflow_action WHERE action_name = N'methylvalidation.final.mapper'),
      @a_final_enricher BIGINT = (SELECT id FROM wf.workflow_action WHERE action_name = N'methylvalidation.final.enricher'),
      @a_final_progression BIGINT = (SELECT id FROM wf.workflow_action WHERE action_name = N'methylvalidation.final.disease_progression');

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
    VALUES (@ver_id, N'SEQUENCE', N'mv_root', NULL, NULL, NULL, NULL, NULL, NULL);
    SET @n_root = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
    VALUES (@ver_id, N'REPEAT', N'mv_feature_loop', NULL, @feature_iterations, NULL, NULL, NULL, NULL);
    SET @n_mc_repeat = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
    VALUES (@ver_id, N'SEQUENCE', N'mv_feature_sequence', NULL, NULL, NULL, NULL, NULL, NULL);
    SET @n_mc_seq = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
    VALUES (@ver_id, N'ACTION', N'mv_feature_centroid', @a_mc_centroid, NULL, NULL, NULL, NULL, NULL);
    SET @n_mc_centroid = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
    VALUES (@ver_id, N'ACTION', N'mv_feature_detector', @a_mc_detector, NULL, NULL, NULL, NULL, NULL);
    SET @n_mc_detector = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
    VALUES (@ver_id, N'SEQUENCE', N'mv_final_sequence', NULL, NULL, NULL, NULL, NULL, NULL);
    SET @n_final_seq = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
    VALUES (@ver_id, N'ACTION', N'mv_final_centroid', @a_final_centroid, NULL, NULL, NULL, NULL, NULL);
    SET @n_final_centroid = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
    VALUES (@ver_id, N'ACTION', N'mv_final_detector', @a_final_detector, NULL, NULL, NULL, NULL, NULL);
    SET @n_final_detector = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
    VALUES (@ver_id, N'ACTION', N'mv_final_mapper', @a_final_mapper, NULL, NULL, NULL, NULL, NULL);
    SET @n_final_mapper = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
    VALUES (@ver_id, N'ACTION', N'mv_final_enricher', @a_final_enricher, NULL, NULL, NULL, NULL, NULL);
    SET @n_final_enricher = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
    VALUES (@ver_id, N'ACTION', N'mv_final_disease_progression', @a_final_progression, NULL, NULL, NULL, NULL, NULL);
    SET @n_final_progression = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_edge (parent_node_id, child_node_id, child_order, branch_kind, switch_case_value, is_default)
    VALUES
      (@n_root, @n_mc_repeat, 0, N'SEQUENCE', NULL, 0),
      (@n_root, @n_final_seq, 1, N'SEQUENCE', NULL, 0),
      (@n_mc_repeat, @n_mc_seq, 0, N'BODY', NULL, 0),
      (@n_mc_seq, @n_mc_centroid, 0, N'SEQUENCE', NULL, 0),
      (@n_mc_seq, @n_mc_detector, 1, N'SEQUENCE', NULL, 0),
      (@n_final_seq, @n_final_centroid, 0, N'SEQUENCE', NULL, 0),
      (@n_final_seq, @n_final_detector, 1, N'SEQUENCE', NULL, 0),
      (@n_final_seq, @n_final_mapper, 2, N'SEQUENCE', NULL, 0),
      (@n_final_seq, @n_final_enricher, 3, N'SEQUENCE', NULL, 0),
      (@n_final_seq, @n_final_progression, 4, N'SEQUENCE', NULL, 0);

    UPDATE wf.workflow_version
    SET root_node_id = @n_root
    WHERE id = @ver_id;

    /*
      Root defaults keep templates resolvable even before planner enriches context.
      Planner/Delphi can overwrite these with actual groups/comparisons/run payloads.
    */
    INSERT INTO wf.node_scope_default (workflow_node_id, var_name, default_expr)
    VALUES
      (@n_root, N'mc.groups', N'[]'),
      (@n_root, N'mc.comparisons', N'[]'),
      (@n_root, N'mc.taskConfig', N'{}'),
      (@n_root, N'mc.finalTaskConfig', N'{}');

    INSERT INTO wf.workflow_input_template (workflow_node_id, template_json)
    VALUES
      (@n_mc_centroid, N'{"phase":"feature","tool":"MethylCentroid","iteration":${ctx.iterationNo},"runId":${var.mc.runId},"groups":${var.mc.groups},"comparisons":${var.mc.comparisons},"taskConfig":${var.mc.taskConfig}}'),
      (@n_mc_detector, N'{"phase":"feature","tool":"MethylDetector","iteration":${ctx.iterationNo},"runId":${var.mc.runId},"groups":${var.mc.groups},"comparisons":${var.mc.comparisons},"taskConfig":${var.mc.taskConfig}}'),
      (@n_final_centroid, N'{"phase":"quality","tool":"MethylCentroid","sampleSet":"final","groups":${var.mc.groups},"comparisons":${var.mc.comparisons},"taskConfig":${var.mc.finalTaskConfig}}'),
      (@n_final_detector, N'{"phase":"quality","tool":"MethylDetector","sampleSet":"final","groups":${var.mc.groups},"comparisons":${var.mc.comparisons},"taskConfig":${var.mc.finalTaskConfig}}'),
      (@n_final_mapper, N'{"phase":"quality","tool":"MethylMapper","sampleSet":"final","groups":${var.mc.groups},"comparisons":${var.mc.comparisons},"taskConfig":${var.mc.finalTaskConfig}}'),
      (@n_final_enricher, N'{"phase":"quality","tool":"MethylEnricher","sampleSet":"final","groups":${var.mc.groups},"comparisons":${var.mc.comparisons},"taskConfig":${var.mc.finalTaskConfig}}'),
      (@n_final_progression, N'{"phase":"quality","tool":"MethylDiseaseProgression","sampleSet":"final","groups":${var.mc.groups},"comparisons":${var.mc.comparisons},"taskConfig":${var.mc.finalTaskConfig}}');

    PRINT N'Seed complete: MethylValidationFlow created.';
END
GO

/*
  Suggested context_json for /startinstance:
  {
    "monteCarlo": {
      "baseProject": "C:/work/project.json",
      "layout": "binary",
      "seed": 42,
      "featureIterations": 30,
      "qualityIterations": 20
    },
    "mc": {
      "groups": [],
      "comparisons": [],
      "finalTaskConfig": {}
    }
  }
*/

