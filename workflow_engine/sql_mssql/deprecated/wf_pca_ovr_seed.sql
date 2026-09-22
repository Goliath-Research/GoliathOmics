/*
  DEPRECATED: use wf_data_driven_pipeline_seed.sql (DataDrivenPipeline + FOREACH).
  Retained as a historical milestone-2 reference only.

  Workflow Engine (wf schema) - PCaOvrFlow seed.

  OvR scale-up for project_PCa3.json (control_vs_each_disease):
    SEQUENCE pca_ovr
    ├─ PARALLEL by_comparison          (PCa_Low, PCa_High in parallel)
    │  └─ SEQUENCE cmp_{label}
    │     └─ PARALLEL by_chrom_{label} (24 children)
    │        └─ SEQUENCE chr_{label}_{chr}
    │           ├─ PARALLEL cent_{label}_{chr} → centroid_g1, centroid_g2
    │           └─ ACTION detect_{label}_{chr}
    └─ SEQUENCE post_ovr               (after all comparisons complete)
       ├─ ACTION mapper
       ├─ ACTION enricher
       └─ ACTION progression

  Registers actions (shared with PCaTwoGroupFlow where applicable):
    pca.centroid, pca.detector, pca.mapper, pca.enricher, pca.progression

  Prerequisites:
  - Base wf schema + wf_scope_variables.sql
  - wf_sql_runtime_parity.sql, wf_sql_branch_parity.sql, wf_sql_scope_writepath_parity.sql
  - wf_sp_delete_workflow_def.sql

  Rebuild:
    EXEC wf.sp_delete_workflow_def @workflow_name = N'PCaOvrFlow';
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

IF EXISTS (SELECT 1 FROM wf.workflow_def WHERE name = N'PCaOvrFlow')
BEGIN
    PRINT N'Seed skipped: wf.workflow_def "PCaOvrFlow" already exists. Rebuild: EXEC wf.sp_delete_workflow_def @workflow_name = N''PCaOvrFlow''; then re-run this script.';
END
ELSE
BEGIN
    DECLARE @def_id BIGINT;
    DECLARE @ver_id BIGINT;

    MERGE wf.workflow_action AS t
    USING (VALUES
      (N'pca.centroid', N'methyl-centroid'),
      (N'pca.detector', N'methyl-detector'),
      (N'pca.mapper', N'methyl-mapper'),
      (N'pca.enricher', N'methyl-enricher'),
      (N'pca.progression', N'methyl-disease-progression')
    ) AS s(action_name, capability)
    ON t.action_name = s.action_name
    WHEN NOT MATCHED THEN
      INSERT (action_name, capability, payload_schema_ref)
      VALUES (s.action_name, s.capability, NULL);

    DECLARE @a_centroid BIGINT = (SELECT id FROM wf.workflow_action WHERE action_name = N'pca.centroid');
    DECLARE @a_detector BIGINT = (SELECT id FROM wf.workflow_action WHERE action_name = N'pca.detector');
    DECLARE @a_mapper BIGINT = (SELECT id FROM wf.workflow_action WHERE action_name = N'pca.mapper');
    DECLARE @a_enricher BIGINT = (SELECT id FROM wf.workflow_action WHERE action_name = N'pca.enricher');
    DECLARE @a_progression BIGINT = (SELECT id FROM wf.workflow_action WHERE action_name = N'pca.progression');

    INSERT INTO wf.workflow_def (name, description)
    VALUES (
      N'PCaOvrFlow',
      N'PCa3 OvR pipeline: parallel control-vs-disease comparisons (per-chromosome centroid+detect), then mapper, enricher, disease progression.'
    );
    SET @def_id = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_version (workflow_def_id, version_major, version_minor, is_active, root_node_id)
    VALUES (@def_id, 1, 0, 1, NULL);
    SET @ver_id = SCOPE_IDENTITY();

    DECLARE @n_root BIGINT;
    DECLARE @n_by_cmp BIGINT;
    DECLARE @n_post_seq BIGINT;
    DECLARE @n_mapper BIGINT;
    DECLARE @n_enricher BIGINT;
    DECLARE @n_progression BIGINT;

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
    VALUES (@ver_id, N'SEQUENCE', N'pca_ovr', NULL, NULL, NULL, NULL, NULL, NULL);
    SET @n_root = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
    VALUES (@ver_id, N'PARALLEL', N'by_comparison', NULL, NULL, NULL, NULL, NULL, NULL);
    SET @n_by_cmp = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
    VALUES (@ver_id, N'SEQUENCE', N'post_ovr', NULL, NULL, NULL, NULL, NULL, NULL);
    SET @n_post_seq = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
    VALUES (@ver_id, N'ACTION', N'mapper_all', @a_mapper, NULL, NULL, NULL, NULL, NULL);
    SET @n_mapper = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
    VALUES (@ver_id, N'ACTION', N'enricher_all', @a_enricher, NULL, NULL, NULL, NULL, NULL);
    SET @n_enricher = SCOPE_IDENTITY();

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
    VALUES (@ver_id, N'ACTION', N'progression_all', @a_progression, NULL, NULL, NULL, NULL, NULL);
    SET @n_progression = SCOPE_IDENTITY();

    INSERT INTO wf.node_scope_default (workflow_node_id, var_name, default_expr)
    VALUES
      (@n_root, N'projectPath', N'""'),
      (@n_root, N'context', N'"CG"'),
      (@n_root, N'centroid1Dir', N'""'),
      (@n_root, N'group1Label', N'"group1"'),
      (@n_root, N'orderedComparisonLabels', N'["PCa_Low","PCa_High"]');

    INSERT INTO wf.workflow_input_template (workflow_node_id, template_json)
    VALUES
      (@n_mapper, N'{"tool":"MethylMapper","project":"${var.projectPath}"}'),
      (@n_enricher, N'{"tool":"MethylEnricher","project":"${var.projectPath}"}'),
      (@n_progression, N'{"tool":"MethylDiseaseProgression","project":"${var.projectPath}","orderedComparisonLabels":${var.orderedComparisonLabels}}');

    INSERT INTO wf.workflow_edge (parent_node_id, child_node_id, child_order, branch_kind, condition_expr, switch_case_value, is_default)
    VALUES
      (@n_root, @n_by_cmp, 0, N'SEQUENCE', NULL, NULL, 0),
      (@n_root, @n_post_seq, 1, N'SEQUENCE', NULL, NULL, 0),
      (@n_post_seq, @n_mapper, 0, N'SEQUENCE', NULL, NULL, 0),
      (@n_post_seq, @n_enricher, 1, N'SEQUENCE', NULL, NULL, 0),
      (@n_post_seq, @n_progression, 2, N'SEQUENCE', NULL, NULL, 0);

    DECLARE @comparisons TABLE (
        ord INT NOT NULL PRIMARY KEY,
        label NVARCHAR(64) NOT NULL,
        centroid2_dir NVARCHAR(1024) NOT NULL,
        detect_out_dir NVARCHAR(1024) NOT NULL,
        group2_label NVARCHAR(128) NOT NULL
    );
    INSERT INTO @comparisons (ord, label, centroid2_dir, detect_out_dir, group2_label) VALUES
      (0, N'PCa_Low',  N'/work/projects/prostate-cancer/PCa3/centroids/diseases/cancer/PCa_Low',  N'/work/projects/prostate-cancer/PCa3/detections/all/PCa_Low',  N'group2'),
      (1, N'PCa_High', N'/work/projects/prostate-cancer/PCa3/centroids/diseases/cancer/PCa_High', N'/work/projects/prostate-cancer/PCa3/detections/all/PCa_High', N'group2');

    DECLARE @chromosomes TABLE (ord INT NOT NULL PRIMARY KEY, chr NVARCHAR(8) NOT NULL);
    INSERT INTO @chromosomes (ord, chr) VALUES
      (0, N'1'), (1, N'2'), (2, N'3'), (3, N'4'), (4, N'5'), (5, N'6'), (6, N'7'), (7, N'8'),
      (8, N'9'), (9, N'10'), (10, N'11'), (11, N'12'), (12, N'13'), (13, N'14'), (14, N'15'),
      (15, N'16'), (16, N'17'), (17, N'18'), (18, N'19'), (19, N'20'), (20, N'21'), (21, N'22'),
      (22, N'X'), (23, N'Y');

    DECLARE @cmp_ord INT;
    DECLARE @cmp_label NVARCHAR(64);
    DECLARE @c2dir NVARCHAR(1024);
    DECLARE @detdir NVARCHAR(1024);
    DECLARE @g2label NVARCHAR(128);
    DECLARE @n_cmp_seq BIGINT;
    DECLARE @n_by_chrom BIGINT;
    DECLARE @chr_ord INT;
    DECLARE @chr NVARCHAR(8);
    DECLARE @n_chr_seq BIGINT;
    DECLARE @n_cent_par BIGINT;
    DECLARE @n_c1 BIGINT;
    DECLARE @n_c2 BIGINT;
    DECLARE @n_det BIGINT;
    DECLARE @nk_cmp NVARCHAR(128);
    DECLARE @nk_by_chr NVARCHAR(128);
    DECLARE @nk_chr NVARCHAR(128);
    DECLARE @tpl_g1 NVARCHAR(MAX);
    DECLARE @tpl_g2 NVARCHAR(MAX);
    DECLARE @tpl_det NVARCHAR(MAX);
    DECLARE @def_c2 NVARCHAR(1024);
    DECLARE @def_det NVARCHAR(1024);
    DECLARE @def_g2 NVARCHAR(128);
    DECLARE @def_lbl NVARCHAR(128);

    DECLARE cmp_cur CURSOR LOCAL FAST_FORWARD FOR
        SELECT ord, label, centroid2_dir, detect_out_dir, group2_label
        FROM @comparisons
        ORDER BY ord;

    OPEN cmp_cur;
    FETCH NEXT FROM cmp_cur INTO @cmp_ord, @cmp_label, @c2dir, @detdir, @g2label;
    WHILE @@FETCH_STATUS = 0
    BEGIN
        SET @nk_cmp = CONCAT(N'cmp_', @cmp_label);

        INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
        VALUES (@ver_id, N'SEQUENCE', @nk_cmp, NULL, NULL, NULL, NULL, NULL, NULL);
        SET @n_cmp_seq = SCOPE_IDENTITY();

        INSERT INTO wf.workflow_edge (parent_node_id, child_node_id, child_order, branch_kind, condition_expr, switch_case_value, is_default)
        VALUES (@n_by_cmp, @n_cmp_seq, @cmp_ord, N'PARALLEL', NULL, NULL, 0);

        SET @def_c2 = N'"' + REPLACE(@c2dir, N'"', N'\"') + N'"';
        SET @def_det = N'"' + REPLACE(@detdir, N'"', N'\"') + N'"';
        SET @def_g2 = N'"' + REPLACE(@g2label, N'"', N'\"') + N'"';
        SET @def_lbl = N'"' + REPLACE(@cmp_label, N'"', N'\"') + N'"';

        INSERT INTO wf.node_scope_default (workflow_node_id, var_name, default_expr)
        VALUES
          (@n_cmp_seq, N'centroid2Dir', @def_c2),
          (@n_cmp_seq, N'detectOutDir', @def_det),
          (@n_cmp_seq, N'group2Label', @def_g2),
          (@n_cmp_seq, N'comparisonLabel', @def_lbl);

        SET @nk_by_chr = CONCAT(N'by_chrom_', @cmp_label);

        INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
        VALUES (@ver_id, N'PARALLEL', @nk_by_chr, NULL, NULL, NULL, NULL, NULL, NULL);
        SET @n_by_chrom = SCOPE_IDENTITY();

        INSERT INTO wf.workflow_edge (parent_node_id, child_node_id, child_order, branch_kind, condition_expr, switch_case_value, is_default)
        VALUES (@n_cmp_seq, @n_by_chrom, 0, N'SEQUENCE', NULL, NULL, 0);

        DECLARE chrom_cur CURSOR LOCAL FAST_FORWARD FOR
            SELECT ord, chr FROM @chromosomes ORDER BY ord;

        OPEN chrom_cur;
        FETCH NEXT FROM chrom_cur INTO @chr_ord, @chr;
        WHILE @@FETCH_STATUS = 0
        BEGIN
            SET @nk_chr = CONCAT(N'chr_', @cmp_label, N'_', @chr);

            INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
            VALUES (@ver_id, N'SEQUENCE', @nk_chr, NULL, NULL, NULL, NULL, NULL, NULL);
            SET @n_chr_seq = SCOPE_IDENTITY();

            INSERT INTO wf.workflow_edge (parent_node_id, child_node_id, child_order, branch_kind, condition_expr, switch_case_value, is_default)
            VALUES (@n_by_chrom, @n_chr_seq, @chr_ord, N'PARALLEL', NULL, NULL, 0);

            INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
            VALUES (@ver_id, N'PARALLEL', CONCAT(N'cent_', @cmp_label, N'_', @chr), NULL, NULL, NULL, NULL, NULL, NULL);
            SET @n_cent_par = SCOPE_IDENTITY();

            INSERT INTO wf.workflow_edge (parent_node_id, child_node_id, child_order, branch_kind, condition_expr, switch_case_value, is_default)
            VALUES (@n_chr_seq, @n_cent_par, 0, N'SEQUENCE', NULL, NULL, 0);

            INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
            VALUES (@ver_id, N'ACTION', CONCAT(N'centroid_g1_', @cmp_label, N'_', @chr), @a_centroid, NULL, NULL, NULL, NULL, NULL);
            SET @n_c1 = SCOPE_IDENTITY();

            INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
            VALUES (@ver_id, N'ACTION', CONCAT(N'centroid_g2_', @cmp_label, N'_', @chr), @a_centroid, NULL, NULL, NULL, NULL, NULL);
            SET @n_c2 = SCOPE_IDENTITY();

            INSERT INTO wf.workflow_edge (parent_node_id, child_node_id, child_order, branch_kind, condition_expr, switch_case_value, is_default)
            VALUES
              (@n_cent_par, @n_c1, 0, N'PARALLEL', NULL, NULL, 0),
              (@n_cent_par, @n_c2, 1, N'PARALLEL', NULL, NULL, 0);

            INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id, repeat_count, condition_ref_node_key, switch_ref_node_key, condition_var, switch_var)
            VALUES (@ver_id, N'ACTION', CONCAT(N'detect_', @cmp_label, N'_', @chr), @a_detector, NULL, NULL, NULL, NULL, NULL);
            SET @n_det = SCOPE_IDENTITY();

            INSERT INTO wf.workflow_edge (parent_node_id, child_node_id, child_order, branch_kind, condition_expr, switch_case_value, is_default)
            VALUES (@n_chr_seq, @n_det, 1, N'SEQUENCE', NULL, NULL, 0);

            SET @tpl_g1 = N'{"tool":"MethylCentroid","project":"${var.projectPath}","group":"${var.group1Label}","chromosome":"' + @chr + N'","context":"${var.context}","comparison":"${var.comparisonLabel}","outputDir":"${var.centroid1Dir}"}';
            SET @tpl_g2 = N'{"tool":"MethylCentroid","project":"${var.projectPath}","group":"${var.group2Label}","chromosome":"' + @chr + N'","context":"${var.context}","comparison":"${var.comparisonLabel}","outputDir":"${var.centroid2Dir}"}';
            SET @tpl_det = N'{"tool":"MethylDetector","project":"${var.projectPath}","chromosome":"' + @chr + N'","context":"${var.context}","comparison":"${var.comparisonLabel}","centroid1Dir":"${var.centroid1Dir}","centroid2Dir":"${var.centroid2Dir}","outputDir":"${var.detectOutDir}"}';

            INSERT INTO wf.workflow_input_template (workflow_node_id, template_json)
            VALUES
              (@n_c1, @tpl_g1),
              (@n_c2, @tpl_g2),
              (@n_det, @tpl_det);

            FETCH NEXT FROM chrom_cur INTO @chr_ord, @chr;
        END
        CLOSE chrom_cur;
        DEALLOCATE chrom_cur;

        FETCH NEXT FROM cmp_cur INTO @cmp_ord, @cmp_label, @c2dir, @detdir, @g2label;
    END
    CLOSE cmp_cur;
    DEALLOCATE cmp_cur;

    UPDATE wf.workflow_version
    SET root_node_id = @n_root
    WHERE id = @ver_id;

    PRINT CONCAT(N'Seeded PCaOvrFlow definition_id=', @def_id, N' version_id=', @ver_id, N' root_node_id=', @n_root);
END
GO
