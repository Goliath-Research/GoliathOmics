/*
  DEPRECATED: use wf_data_driven_pipeline_seed.sql (DataDrivenPipeline + FOREACH).
  Retained as a historical milestone-1 reference only.

  Workflow Engine (wf schema) - PCaTwoGroupFlow seed.

  Two-group comparison (control vs one disease stage), per-chromosome fan-out:
    SEQUENCE pca_two_group
    └─ PARALLEL by_chrom (24 children)
       └─ SEQUENCE chr_{chr}
          ├─ PARALLEL cent_{chr} → centroid_g1_{chr}, centroid_g2_{chr}
          └─ ACTION detect_{chr}

  Registers actions:
    pca.centroid  -> methyl-centroid
    pca.detector  -> methyl-detector

  Prerequisites:
  - Base wf schema + wf_scope_variables.sql
  - wf_sql_runtime_parity.sql, wf_sql_branch_parity.sql, wf_sql_scope_writepath_parity.sql (recommended)
  - wf_sp_delete_workflow_def.sql (to rebuild after schema/seed changes)

  Rebuild:
    EXEC wf.sp_delete_workflow_def @workflow_name = N'PCaTwoGroupFlow';
    -- then re-run this script
*/

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

IF COL_LENGTH('wf.workflow_node', 'condition_var') IS NULL
   OR OBJECT_ID(N'wf.node_scope_default', N'U') IS NULL
BEGIN
    RAISERROR(N'Prerequisite missing: run wf_scope_variables.sql first.', 16, 1);
    RETURN;
END
GO

IF EXISTS (SELECT 1 FROM wf.workflow_def WHERE name = N'PCaTwoGroupFlow')
BEGIN
    PRINT N'Seed skipped: wf.workflow_def "PCaTwoGroupFlow" already exists. Rebuild: EXEC wf.sp_delete_workflow_def @workflow_name = N''PCaTwoGroupFlow''; then re-run this script.';
END
ELSE
BEGIN
    DECLARE @def_id BIGINT;
    DECLARE @ver_id BIGINT;

    MERGE wf.workflow_action AS t
    USING (VALUES
      (N'pca.centroid', N'methyl-centroid'),
      (N'pca.detector', N'methyl-detector')
    ) AS s(action_name, capability)
    ON t.action_name = s.action_name
    WHEN NOT MATCHED THEN
      INSERT (action_name, capability, payload_schema_ref)
      VALUES (s.action_name, s.capability, NULL);

    DECLARE @a_centroid BIGINT = (SELECT id FROM wf.workflow_action WHERE action_name = N'pca.centroid');
    DECLARE @a_detector BIGINT = (SELECT id FROM wf.workflow_action WHERE action_name = N'pca.detector');

    INSERT INTO wf.workflow_def (name, description)
    VALUES (
      N'PCaTwoGroupFlow',
      N'Two-group PCa comparison: parallel per-chromosome centroid (group1+group2) then detection. Milestone 1 for project_PCa3-style pipelines.'
    );
    SET @def_id = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_version (workflow_def_id, version_major, version_minor, is_active, root_node_id)
    VALUES (@def_id, 1, 0, 1, NULL);
    SET @ver_id = SCOPE_IDENTITY();

    DECLARE @n_root BIGINT;
    DECLARE @n_by_chrom BIGINT;

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
    VALUES (@ver_id, N'SEQUENCE', N'pca_two_group', NULL, NULL, NULL, NULL, NULL, NULL);
    SET @n_root = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
    VALUES (@ver_id, N'PARALLEL', N'by_chrom', NULL, NULL, NULL, NULL, NULL, NULL);
    SET @n_by_chrom = SCOPE_IDENTITY();

    INSERT INTO wf.node_scope_default (workflow_node_id, var_name, default_expr)
    VALUES
      (@n_root, N'projectPath', N'""'),
      (@n_root, N'context', N'"CG"');

    DECLARE @chromosomes TABLE (ord INT NOT NULL PRIMARY KEY, chr NVARCHAR(8) NOT NULL);
    INSERT INTO @chromosomes (ord, chr) VALUES
      (0, N'1'), (1, N'2'), (2, N'3'), (3, N'4'), (4, N'5'), (5, N'6'), (6, N'7'), (7, N'8'),
      (8, N'9'), (9, N'10'), (10, N'11'), (11, N'12'), (12, N'13'), (13, N'14'), (14, N'15'),
      (15, N'16'), (16, N'17'), (17, N'18'), (18, N'19'), (19, N'20'), (20, N'21'), (21, N'22'),
      (22, N'X'), (23, N'Y');

    DECLARE @ord INT;
    DECLARE @chr NVARCHAR(8);
    DECLARE @n_chr_seq BIGINT;
    DECLARE @n_cent_par BIGINT;
    DECLARE @n_c1 BIGINT;
    DECLARE @n_c2 BIGINT;
    DECLARE @n_det BIGINT;
    DECLARE @nk_chr NVARCHAR(128);
    DECLARE @tpl_g1 NVARCHAR(MAX);
    DECLARE @tpl_g2 NVARCHAR(MAX);
    DECLARE @tpl_det NVARCHAR(MAX);

    DECLARE chrom_cur CURSOR LOCAL FAST_FORWARD FOR
        SELECT ord, chr FROM @chromosomes ORDER BY ord;

    OPEN chrom_cur;
    FETCH NEXT FROM chrom_cur INTO @ord, @chr;
    WHILE @@FETCH_STATUS = 0
    BEGIN
        SET @nk_chr = CONCAT(N'chr_', @chr);

        INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
        VALUES (@ver_id, N'SEQUENCE', @nk_chr, NULL, NULL, NULL, NULL, NULL, NULL);
        SET @n_chr_seq = SCOPE_IDENTITY();

        INSERT INTO wf.workflow_edge (parent_node_id, child_node_id, child_order, branch_kind, condition_expr, switch_case_value, is_default)
        VALUES (@n_by_chrom, @n_chr_seq, @ord, N'PARALLEL', NULL, NULL, 0);

        INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
        VALUES (@ver_id, N'PARALLEL', CONCAT(N'cent_', @chr), NULL, NULL, NULL, NULL, NULL, NULL);
        SET @n_cent_par = SCOPE_IDENTITY();

        INSERT INTO wf.workflow_edge (parent_node_id, child_node_id, child_order, branch_kind, condition_expr, switch_case_value, is_default)
        VALUES (@n_chr_seq, @n_cent_par, 0, N'SEQUENCE', NULL, NULL, 0);

        INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
        VALUES (@ver_id, N'ACTION', CONCAT(N'centroid_g1_', @chr), @a_centroid, NULL, NULL, NULL, NULL, NULL);
        SET @n_c1 = SCOPE_IDENTITY();

        INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
        VALUES (@ver_id, N'ACTION', CONCAT(N'centroid_g2_', @chr), @a_centroid, NULL, NULL, NULL, NULL, NULL);
        SET @n_c2 = SCOPE_IDENTITY();

        INSERT INTO wf.workflow_edge (parent_node_id, child_node_id, child_order, branch_kind, condition_expr, switch_case_value, is_default)
        VALUES
          (@n_cent_par, @n_c1, 0, N'PARALLEL', NULL, NULL, 0),
          (@n_cent_par, @n_c2, 1, N'PARALLEL', NULL, NULL, 0);

        INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
        VALUES (@ver_id, N'ACTION', CONCAT(N'detect_', @chr), @a_detector, NULL, NULL, NULL, NULL, NULL);
        SET @n_det = SCOPE_IDENTITY();

        INSERT INTO wf.workflow_edge (parent_node_id, child_node_id, child_order, branch_kind, condition_expr, switch_case_value, is_default)
        VALUES (@n_chr_seq, @n_det, 1, N'SEQUENCE', NULL, NULL, 0);

        SET @tpl_g1 = N'{"tool":"MethylCentroid","project":"${var.projectPath}","group":"${var.group1Label}","chromosome":"' + @chr + N'","context":"${var.context}","outputDir":"${var.centroid1Dir}"}';
        SET @tpl_g2 = N'{"tool":"MethylCentroid","project":"${var.projectPath}","group":"${var.group2Label}","chromosome":"' + @chr + N'","context":"${var.context}","outputDir":"${var.centroid2Dir}"}';
        SET @tpl_det = N'{"tool":"MethylDetector","project":"${var.projectPath}","chromosome":"' + @chr + N'","context":"${var.context}","centroid1Dir":"${var.centroid1Dir}","centroid2Dir":"${var.centroid2Dir}","outputDir":"${var.detectOutDir}"}';

        INSERT INTO wf.workflow_input_template (workflow_node_id, template_json)
        VALUES
          (@n_c1, @tpl_g1),
          (@n_c2, @tpl_g2),
          (@n_det, @tpl_det);

        INSERT INTO wf.variable_output_binding (workflow_node_id, var_name, source_kind, source_json_path)
        VALUES (@n_det, N'lastDetectN', N'output_path', N'nDmps');

        FETCH NEXT FROM chrom_cur INTO @ord, @chr;
    END
    CLOSE chrom_cur;
    DEALLOCATE chrom_cur;

    INSERT INTO wf.workflow_edge (parent_node_id, child_node_id, child_order, branch_kind, condition_expr, switch_case_value, is_default)
    VALUES (@n_root, @n_by_chrom, 0, N'SEQUENCE', NULL, NULL, 0);

    UPDATE wf.workflow_version
    SET root_node_id = @n_root
    WHERE id = @ver_id;

    PRINT CONCAT(N'Seeded PCaTwoGroupFlow definition_id=', @def_id, N' version_id=', @ver_id, N' root_node_id=', @n_root);
END
GO
