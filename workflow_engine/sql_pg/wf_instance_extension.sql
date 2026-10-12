/*
  MethylPipeline wf schema - generic instance extension storage (PostgreSQL).

  Prerequisites: 00_schema.sql (workflow_instance)
*/

CREATE TABLE IF NOT EXISTS wf.instance_extension (
  workflow_instance_id bigint NOT NULL REFERENCES wf.workflow_instance(id) ON DELETE CASCADE,
  extension_key text NOT NULL,
  data_json jsonb NULL,
  created_at_utc timestamptz NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
  updated_at_utc timestamptz NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
  PRIMARY KEY (workflow_instance_id, extension_key)
);

CREATE INDEX IF NOT EXISTS ix_ie_instance ON wf.instance_extension(workflow_instance_id);

CREATE OR REPLACE PROCEDURE wf.wf_repo_upsert_instance_extension(
  IN p_instance_id bigint,
  IN p_extension_key text,
  IN p_data_json jsonb
)
LANGUAGE plpgsql
AS $$
BEGIN
  IF p_extension_key IS NULL OR btrim(p_extension_key) = '' THEN
    RETURN;
  END IF;

  INSERT INTO wf.instance_extension (workflow_instance_id, extension_key, data_json)
  VALUES (p_instance_id, p_extension_key, p_data_json)
  ON CONFLICT (workflow_instance_id, extension_key) DO UPDATE SET
    data_json = EXCLUDED.data_json,
    updated_at_utc = (now() AT TIME ZONE 'utc');
END;
$$;

CREATE OR REPLACE FUNCTION wf.wf_repo_get_instance_extension(
  p_instance_id bigint,
  p_extension_key text
)
RETURNS jsonb
LANGUAGE sql
STABLE
AS $$
  SELECT ie.data_json
  FROM wf.instance_extension ie
  WHERE ie.workflow_instance_id = p_instance_id
    AND ie.extension_key = p_extension_key;
$$;
