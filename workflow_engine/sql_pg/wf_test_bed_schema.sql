/*
  Test bed: audit tables for simulated / real worker runs against seeded workflows.
  Prerequisites: 00_schema.sql
*/

CREATE TABLE IF NOT EXISTS wf.test_bed_run (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  workflow_instance_id bigint NOT NULL REFERENCES wf.workflow_instance(id) ON DELETE CASCADE,
  workflow_name text NOT NULL,
  started_at_utc timestamptz NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
  completed_at_utc timestamptz NULL,
  tasks_submitted int NOT NULL DEFAULT 0,
  final_status text NULL,
  notes text NULL
);

CREATE INDEX IF NOT EXISTS ix_tbr_instance ON wf.test_bed_run(workflow_instance_id);

CREATE TABLE IF NOT EXISTS wf.test_bed_task_log (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  test_bed_run_id bigint NOT NULL REFERENCES wf.test_bed_run(id) ON DELETE CASCADE,
  workflow_instance_id bigint NOT NULL REFERENCES wf.workflow_instance(id) ON DELETE CASCADE,
  loop_no int NOT NULL,
  node_execution_id bigint NOT NULL,
  node_key text NOT NULL,
  capability text NULL,
  action_name text NULL,
  result_code int NOT NULL,
  accepted boolean NULL,
  instance_status text NULL,
  input_json jsonb NULL,
  output_json jsonb NULL,
  logged_at_utc timestamptz NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);

CREATE INDEX IF NOT EXISTS ix_tbtl_run ON wf.test_bed_task_log(test_bed_run_id);
CREATE INDEX IF NOT EXISTS ix_tbtl_instance ON wf.test_bed_task_log(workflow_instance_id);

CREATE OR REPLACE VIEW wf.v_test_bed_task_summary AS
SELECT
  r.id AS test_bed_run_id,
  r.workflow_name,
  r.workflow_instance_id,
  r.started_at_utc,
  r.completed_at_utc,
  r.tasks_submitted,
  r.final_status,
  count(t.id) AS log_rows,
  count(*) FILTER (WHERE t.result_code >= 0) AS tasks_ok,
  count(*) FILTER (WHERE t.result_code < 0) AS tasks_failed
FROM wf.test_bed_run r
LEFT JOIN wf.test_bed_task_log t ON t.test_bed_run_id = r.id
GROUP BY r.id, r.workflow_name, r.workflow_instance_id, r.started_at_utc,
         r.completed_at_utc, r.tasks_submitted, r.final_status;
