/*
  Two-group comparison test workflow (PostgreSQL, no FOREACH required).

  Static fan-out over chromosomes 1 and 2 only (fast engine test bed):
    SEQUENCE two_group_root
    └─ PARALLEL by_chrom
       └─ SEQUENCE chr_{n}
          ├─ PARALLEL cent_{n} → centroid_g1, centroid_g2
          └─ detect

  Rebuild:
    SELECT * FROM wf.sp_delete_workflow_def(NULL, 'TwoGroupTestFlow', true);
    \\i wf_two_group_test_seed.sql
*/

DO $$
DECLARE
  v_def_id bigint;
  v_ver_id bigint;
  v_root bigint;
  v_by_chrom bigint;
  v_chr_seq bigint;
  v_cent_par bigint;
  v_c1 bigint;
  v_c2 bigint;
  v_det bigint;
  v_a_centroid bigint;
  v_a_detector bigint;
  v_chr text;
  v_ord int := 0;
  v_nk_chr text;
BEGIN
  IF EXISTS (SELECT 1 FROM wf.workflow_def WHERE name = 'TwoGroupTestFlow') THEN
    RAISE NOTICE 'Seed skipped: TwoGroupTestFlow already exists.';
    RETURN;
  END IF;

  INSERT INTO wf.workflow_action (action_name, capability)
  VALUES ('test.centroid', 'methyl-centroid')
  ON CONFLICT (action_name) DO NOTHING;
  INSERT INTO wf.workflow_action (action_name, capability)
  VALUES ('test.detector', 'methyl-detector')
  ON CONFLICT (action_name) DO NOTHING;

  SELECT id INTO v_a_centroid FROM wf.workflow_action WHERE action_name = 'test.centroid';
  SELECT id INTO v_a_detector FROM wf.workflow_action WHERE action_name = 'test.detector';

  INSERT INTO wf.workflow_def (name, description)
  VALUES (
    'TwoGroupTestFlow',
    'Two-group test bed: 2 chromosomes, parallel centroids then detect per chr.'
  )
  RETURNING id INTO v_def_id;

  INSERT INTO wf.workflow_version (workflow_def_id, version_major, version_minor, is_active, root_node_id)
  VALUES (v_def_id, 1, 0, true, NULL)
  RETURNING id INTO v_ver_id;

  INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key)
  VALUES (v_ver_id, 'SEQUENCE', 'two_group_root')
  RETURNING id INTO v_root;

  INSERT INTO wf.workflow_node (workflow_version_id, node_type, node_key)
  VALUES (v_ver_id, 'PARALLEL', 'by_chrom')
  RETURNING id INTO v_by_chrom;

  INSERT INTO wf.node_scope_default (workflow_node_id, var_name, default_expr)
  VALUES
    (v_root, 'projectPath', '""'),
    (v_root, 'context', '"CG"');

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
        'project', '${var.projectPath}',
        'group', '${var.group1Label}',
        'chromosome', v_chr,
        'context', '${var.context}',
        'outputDir', '${var.centroid1Dir}'
      )),
      (v_c2, jsonb_build_object(
        'tool', 'MethylCentroid',
        'project', '${var.projectPath}',
        'group', '${var.group2Label}',
        'chromosome', v_chr,
        'context', '${var.context}',
        'outputDir', '${var.centroid2Dir}'
      )),
      (v_det, jsonb_build_object(
        'tool', 'MethylDetector',
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

  INSERT INTO wf.workflow_edge (parent_node_id, child_node_id, child_order, branch_kind)
  VALUES (v_root, v_by_chrom, 0, 'SEQUENCE');

  UPDATE wf.workflow_version SET root_node_id = v_root WHERE id = v_ver_id;

  RAISE NOTICE 'Seeded TwoGroupTestFlow def_id=% version_id=% (6 ACTION tasks)', v_def_id, v_ver_id;
END $$;
