/*
  Monte Carlo two-group stability test workflow (PostgreSQL, REPEAT — no FOREACH).

  REPEAT mc_stability (10) × two-group chr 1+2 pipeline, then final post steps:
    SEQUENCE mc_root
    ├─ REPEAT mc_stability (10)
    │  └─ SEQUENCE mc_iter
    │     └─ PARALLEL by_chrom → (same chr_1/chr_2 tree as TwoGroupTestFlow)
    └─ SEQUENCE final_post → mapper, enricher, progression

  Simulates feature-stability MC iterations before downstream aggregation.
  Rebuild:
    SELECT * FROM wf.sp_delete_workflow_def(NULL, 'McTwoGroupTestFlow', true);
    \\i wf_mc_two_group_test_seed.sql
*/

DO $$
DECLARE
  v_feature_iters int := 10;
  v_def_id bigint;
  v_ver_id bigint;
  v_root bigint;
  v_repeat bigint;
  v_iter_seq bigint;
  v_by_chrom bigint;
  v_final bigint;
  v_mapper bigint;
  v_enricher bigint;
  v_prog bigint;
  v_chr_seq bigint;
  v_cent_par bigint;
  v_c1 bigint;
  v_c2 bigint;
  v_det bigint;
  v_a_centroid bigint;
  v_a_detector bigint;
  v_a_mapper bigint;
  v_a_enricher bigint;
  v_a_prog bigint;
  v_chr text;
  v_ord int;
  v_nk_chr text;
BEGIN
  IF EXISTS (SELECT 1 FROM wf.workflow_def WHERE name = 'McTwoGroupTestFlow') THEN
    RAISE NOTICE 'Seed skipped: McTwoGroupTestFlow already exists.';
    RETURN;
  END IF;

  INSERT INTO wf.workflow_action (action_name, capability) VALUES
    ('test.centroid', 'methyl-centroid'),
    ('test.detector', 'methyl-detector'),
    ('test.mapper', 'methyl-mapper'),
    ('test.enricher', 'methyl-enricher'),
    ('test.progression', 'methyl-disease-progression')
  ON CONFLICT (action_name) DO NOTHING;

  SELECT id INTO v_a_centroid FROM wf.workflow_action WHERE action_name = 'test.centroid';
  SELECT id INTO v_a_detector FROM wf.workflow_action WHERE action_name = 'test.detector';
  SELECT id INTO v_a_mapper FROM wf.workflow_action WHERE action_name = 'test.mapper';
  SELECT id INTO v_a_enricher FROM wf.workflow_action WHERE action_name = 'test.enricher';
  SELECT id INTO v_a_prog FROM wf.workflow_action WHERE action_name = 'test.progression';

  INSERT INTO wf.workflow_def (name, description)
  VALUES (
    'McTwoGroupTestFlow',
    'MC stability test bed: REPEAT feature iterations × two-group chr1+2, then mapper/enricher/progression.'
  )
  RETURNING id INTO v_def_id;

  INSERT INTO wf.workflow_version (workflow_def_id, version_major, version_minor, is_active, root_node_id)
  VALUES (v_def_id, 1, 0, true, NULL)
  RETURNING id INTO v_ver_id;

  INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key)
  VALUES (v_ver_id, 'SEQUENCE', 'mc_root')
  RETURNING id INTO v_root;

  INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, repeat_count)
  VALUES (v_ver_id, 'REPEAT', 'mc_stability', v_feature_iters)
  RETURNING id INTO v_repeat;

  INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key)
  VALUES (v_ver_id, 'SEQUENCE', 'mc_iter')
  RETURNING id INTO v_iter_seq;

  INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key)
  VALUES (v_ver_id, 'PARALLEL', 'by_chrom')
  RETURNING id INTO v_by_chrom;

  INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key)
  VALUES (v_ver_id, 'SEQUENCE', 'final_post')
  RETURNING id INTO v_final;

  INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id)
  VALUES (v_ver_id, 'ACTION', 'mapper', v_a_mapper)
  RETURNING id INTO v_mapper;

  INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id)
  VALUES (v_ver_id, 'ACTION', 'enricher', v_a_enricher)
  RETURNING id INTO v_enricher;

  INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id)
  VALUES (v_ver_id, 'ACTION', 'progression', v_a_prog)
  RETURNING id INTO v_prog;

  INSERT INTO wf.node_scope_default (workflow_node_id, var_name, default_expr)
  VALUES
    (v_root, 'projectPath', '""'),
    (v_root, 'context', '"CG"'),
    (v_repeat, 'mcPhase', '"feature"');

  v_ord := 0;
  FOREACH v_chr IN ARRAY ARRAY['1', '2']
  LOOP
    v_nk_chr := 'chr_' || v_chr;

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key)
    VALUES (v_ver_id, 'SEQUENCE', v_nk_chr)
    RETURNING id INTO v_chr_seq;

    INSERT INTO wf.workflow_edge (parent_node_id, child_node_id, child_order, branch_kind)
    VALUES (v_by_chrom, v_chr_seq, v_ord, 'PARALLEL');

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key)
    VALUES (v_ver_id, 'PARALLEL', 'cent_' || v_chr)
    RETURNING id INTO v_cent_par;

    INSERT INTO wf.workflow_edge (parent_node_id, child_node_id, child_order, branch_kind)
    VALUES (v_chr_seq, v_cent_par, 0, 'SEQUENCE');

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id)
    VALUES (v_ver_id, 'ACTION', 'centroid_g1_' || v_chr, v_a_centroid)
    RETURNING id INTO v_c1;

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id)
    VALUES (v_ver_id, 'ACTION', 'centroid_g2_' || v_chr, v_a_centroid)
    RETURNING id INTO v_c2;

    INSERT INTO wf.workflow_edge (parent_node_id, child_node_id, child_order, branch_kind)
    VALUES
      (v_cent_par, v_c1, 0, 'PARALLEL'),
      (v_cent_par, v_c2, 1, 'PARALLEL');

    INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key, workflow_action_id)
    VALUES (v_ver_id, 'ACTION', 'detect_' || v_chr, v_a_detector)
    RETURNING id INTO v_det;

    INSERT INTO wf.workflow_edge (parent_node_id, child_node_id, child_order, branch_kind)
    VALUES (v_chr_seq, v_det, 1, 'SEQUENCE');

    INSERT INTO wf.workflow_input_template (workflow_node_id, template_json)
    VALUES
      (v_c1, jsonb_build_object(
        'tool', 'MethylCentroid',
        'phase', 'feature',
        'project', '${var.projectPath}',
        'group', '${var.group1Label}',
        'chromosome', v_chr,
        'context', '${var.context}',
        'outputDir', '${var.centroid1Dir}'
      )),
      (v_c2, jsonb_build_object(
        'tool', 'MethylCentroid',
        'phase', 'feature',
        'project', '${var.projectPath}',
        'group', '${var.group2Label}',
        'chromosome', v_chr,
        'context', '${var.context}',
        'outputDir', '${var.centroid2Dir}'
      )),
      (v_det, jsonb_build_object(
        'tool', 'MethylDetector',
        'phase', 'feature',
        'project', '${var.projectPath}',
        'chromosome', v_chr,
        'context', '${var.context}',
        'centroid1Dir', '${var.centroid1Dir}',
        'centroid2Dir', '${var.centroid2Dir}',
        'outputDir', '${var.detectOutDir}'
      ));

    INSERT INTO wf.variable_output_binding (workflow_node_id, var_name, source_kind, source_json_path)
    VALUES (v_det, 'lastDetectN', 'output_path', 'nDmps');

    v_ord := v_ord + 1;
  END LOOP;

  INSERT INTO wf.workflow_input_template (workflow_node_id, template_json)
  VALUES
    (v_mapper, jsonb_build_object('tool', '${var.workerToolMapper}', 'project', '${var.projectPath}')),
    (v_enricher, jsonb_build_object('tool', '${var.workerToolEnricher}', 'project', '${var.projectPath}')),
    (v_prog, jsonb_build_object('tool', '${var.workerToolProgression}', 'project', '${var.projectPath}'));

  INSERT INTO wf.workflow_input_binding (workflow_node_id, target_json_path, source_expr, is_required)
  VALUES (v_prog, 'orderedComparisonLabels', '${var.orderedComparisonLabels}', false);

  INSERT INTO wf.workflow_edge (parent_node_id, child_node_id, child_order, branch_kind)
  VALUES
    (v_root, v_repeat, 0, 'SEQUENCE'),
    (v_root, v_final, 1, 'SEQUENCE'),
    (v_repeat, v_iter_seq, 0, 'BODY'),
    (v_iter_seq, v_by_chrom, 0, 'SEQUENCE'),
    (v_final, v_mapper, 0, 'SEQUENCE'),
    (v_final, v_enricher, 1, 'SEQUENCE'),
    (v_final, v_prog, 2, 'SEQUENCE');

  UPDATE wf.workflow_version SET root_node_id = v_root WHERE id = v_ver_id;

  RAISE NOTICE 'Seeded McTwoGroupTestFlow def_id=% version_id=% (% MC iters × 6 + 3 post = % tasks)',
    v_def_id, v_ver_id, v_feature_iters, v_feature_iters * 6 + 3;
END $$;
