/*
  Test bed worker registration (deterministic token for simulated runs).
  Prerequisites: 00_schema.sql
*/

DO $$
DECLARE
  v_cluster_id bigint;
  v_worker_id bigint;
  v_token text := 'test-bed-token';
  v_hash bytea := wf.wf_sha256_text(v_token);
BEGIN
  INSERT INTO wf.cluster (cluster_key, name, status)
  VALUES ('test-bed', 'Workflow test bed', 'ACTIVE')
  ON CONFLICT (cluster_key) DO UPDATE SET name = EXCLUDED.name
  RETURNING id INTO v_cluster_id;

  IF v_cluster_id IS NULL THEN
    SELECT id INTO v_cluster_id FROM wf.cluster WHERE cluster_key = 'test-bed';
  END IF;

  INSERT INTO wf.worker (cluster_id, external_worker_key, status)
  VALUES (v_cluster_id, 'simulator-1', 'REGISTERED')
  ON CONFLICT DO NOTHING;

  SELECT w.id INTO v_worker_id
  FROM wf.worker w
  WHERE w.cluster_id = v_cluster_id AND w.external_worker_key = 'simulator-1';

  DELETE FROM wf.worker_token WHERE worker_id = v_worker_id;
  INSERT INTO wf.worker_token (worker_id, token_hash)
  VALUES (v_worker_id, v_hash);

  RAISE NOTICE 'Test bed worker id=% token=% (sha256 registered)', v_worker_id, v_token;
END $$;

CREATE OR REPLACE FUNCTION wf.wf_test_bed_worker_id()
RETURNS bigint
LANGUAGE sql
STABLE
AS $$
  SELECT w.id
  FROM wf.worker w
  INNER JOIN wf.cluster c ON c.id = w.cluster_id
  WHERE c.cluster_key = 'test-bed' AND w.external_worker_key = 'simulator-1'
  LIMIT 1;
$$;
