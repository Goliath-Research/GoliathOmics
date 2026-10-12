/*
  Test bed: start workflow instances and simulate worker submit loop with task logging.
  Prerequisites: wf_test_bed_schema.sql, wf_test_bed_worker.sql, worker API, engine core.
*/

CREATE OR REPLACE FUNCTION wf.wf_test_bed_fake_output(
  p_capability text,
  p_node_key text,
  p_input_json jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
  v_chr text;
  v_n int;
BEGIN
  v_chr := coalesce(p_input_json ->> 'chromosome', '1');
  IF p_capability = 'methyl-centroid' THEN
    RETURN jsonb_build_object(
      'ok', true,
      'h5', coalesce(p_input_json ->> 'outputDir', '/work/out') || '/' || v_chr || '-CG.h5',
      'chromosome', v_chr
    );
  ELSIF p_capability = 'methyl-detector' THEN
    v_n := (abs(hashtext(p_node_key)) % 1000) + 1;
    RETURN jsonb_build_object(
      'ok', true,
      'dmpsCsv', coalesce(p_input_json ->> 'outputDir', '/work/out') || '/dmps-' || v_chr || '.csv',
      'chromosome', v_chr,
      'nDmps', v_n
    );
  ELSIF p_capability IN ('methyl-mapper', 'methyl-enricher', 'methyl-disease-progression') THEN
    RETURN jsonb_build_object('ok', true, 'nodeKey', p_node_key);
  END IF;
  RETURN jsonb_build_object('ok', false, 'nodeKey', p_node_key);
END;
$$;

CREATE OR REPLACE FUNCTION wf.sp_test_bed_create_instance(
  p_workflow_name text,
  p_context_json jsonb DEFAULT NULL
)
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
  v_ver_id bigint;
  v_instance_id bigint;
  v_ctx jsonb := coalesce(p_context_json, '{}'::jsonb);
BEGIN
  SELECT wv.id INTO v_ver_id
  FROM wf.workflow_version wv
  INNER JOIN wf.workflow_def wd ON wd.id = wv.workflow_def_id
  WHERE wd.name = p_workflow_name AND wv.is_active
  ORDER BY wv.id DESC
  LIMIT 1;

  IF v_ver_id IS NULL THEN
    RAISE EXCEPTION 'Workflow % not found', p_workflow_name USING ERRCODE = '50012';
  END IF;

  INSERT INTO wf.workflow_instance (workflow_version_id, status, context_json)
  VALUES (v_ver_id, 'CREATED', v_ctx)
  RETURNING id INTO v_instance_id;

  INSERT INTO wf.instance_cursor (workflow_instance_id, notes)
  VALUES (v_instance_id, 'test bed: ' || p_workflow_name);

  RETURN v_instance_id;
END;
$$;

CREATE OR REPLACE FUNCTION wf.sp_test_bed_simulate(
  p_instance_id bigint,
  p_worker_id bigint DEFAULT NULL,
  p_worker_token text DEFAULT 'test-bed-token',
  p_max_loops int DEFAULT 5000
)
RETURNS TABLE (
  test_bed_run_id bigint,
  tasks_submitted int,
  final_status text
)
LANGUAGE plpgsql
AS $$
DECLARE
  v_worker_id bigint := coalesce(p_worker_id, wf.wf_test_bed_worker_id());
  v_run_id bigint;
  v_wf_name text;
  v_loop int := 0;
  v_idle int := 0;
  v_tasks int := 0;
  v_inst_status text;
  v_claim record;
  v_out jsonb;
  v_rc int;
  v_ack record;
BEGIN
  IF v_worker_id IS NULL THEN
    RAISE EXCEPTION 'Test bed worker not registered; run wf_test_bed_worker.sql' USING ERRCODE = '50013';
  END IF;

  SELECT wd.name INTO v_wf_name
  FROM wf.workflow_instance wi
  INNER JOIN wf.workflow_version wv ON wv.id = wi.workflow_version_id
  INNER JOIN wf.workflow_def wd ON wd.id = wv.workflow_def_id
  WHERE wi.id = p_instance_id;

  INSERT INTO wf.test_bed_run (workflow_instance_id, workflow_name, notes)
  VALUES (p_instance_id, coalesce(v_wf_name, '?'), 'simulated worker loop')
  RETURNING id INTO v_run_id;

  CALL wf.sp_start_workflow_instance(p_instance_id);

  WHILE v_loop < p_max_loops LOOP
    v_loop := v_loop + 1;

    SELECT * INTO v_claim
    FROM wf.sp_worker_request_task(v_worker_id, p_worker_token, NULL, 300)
    LIMIT 1;

    IF v_claim.node_execution_id IS NULL THEN
      v_idle := v_idle + 1;
      SELECT wi.status INTO v_inst_status FROM wf.workflow_instance wi WHERE wi.id = p_instance_id;
      EXIT WHEN v_inst_status IN ('COMPLETED', 'FAILED', 'CANCELLED');
      EXIT WHEN v_idle > 100;
      CONTINUE;
    END IF;

    v_idle := 0;
    v_rc := 0;
    v_out := wf.wf_test_bed_fake_output(v_claim.capability, v_claim.node_key, v_claim.input_json);
    IF coalesce(v_out ->> 'ok', 'false') <> 'true' THEN
      v_rc := -1;
    END IF;

    SELECT * INTO v_ack
    FROM wf.sp_worker_submit_result(
      v_claim.node_execution_id, v_worker_id, p_worker_token, v_rc, v_out
    );

    INSERT INTO wf.test_bed_task_log (
      test_bed_run_id, workflow_instance_id, loop_no,
      node_execution_id, node_key, capability, action_name,
      result_code, accepted, instance_status, input_json, output_json
    ) VALUES (
      v_run_id, p_instance_id, v_loop,
      v_claim.node_execution_id, v_claim.node_key, v_claim.capability, v_claim.action_name,
      v_rc, v_ack.accepted, v_ack.instance_status, v_claim.input_json, v_out
    );

    v_tasks := v_tasks + 1;

    SELECT wi.status INTO v_inst_status FROM wf.workflow_instance wi WHERE wi.id = p_instance_id;
    EXIT WHEN v_inst_status IN ('COMPLETED', 'FAILED', 'CANCELLED');
  END LOOP;

  SELECT wi.status INTO v_inst_status FROM wf.workflow_instance wi WHERE wi.id = p_instance_id;

  UPDATE wf.test_bed_run
  SET completed_at_utc = (now() AT TIME ZONE 'utc'),
      tasks_submitted = v_tasks,
      final_status = v_inst_status
  WHERE id = v_run_id;

  test_bed_run_id := v_run_id;
  tasks_submitted := v_tasks;
  final_status := v_inst_status;
  RETURN NEXT;
END;
$$;

CREATE OR REPLACE FUNCTION wf.sp_test_bed_run_workflow(
  p_workflow_name text,
  p_context_json jsonb DEFAULT NULL,
  p_worker_id bigint DEFAULT NULL,
  p_worker_token text DEFAULT 'test-bed-token'
)
RETURNS TABLE (
  workflow_instance_id bigint,
  test_bed_run_id bigint,
  tasks_submitted int,
  final_status text
)
LANGUAGE plpgsql
AS $$
DECLARE
  v_instance_id bigint;
  v_sim record;
BEGIN
  v_instance_id := wf.sp_test_bed_create_instance(p_workflow_name, p_context_json);

  SELECT * INTO v_sim
  FROM wf.sp_test_bed_simulate(v_instance_id, p_worker_id, p_worker_token);

  workflow_instance_id := v_instance_id;
  test_bed_run_id := v_sim.test_bed_run_id;
  tasks_submitted := v_sim.tasks_submitted;
  final_status := v_sim.final_status;
  RETURN NEXT;
END;
$$;
