-- Register Omics navigation nodes via the GoliathApp portal shell API.

CREATE OR REPLACE FUNCTION portal.sp_register_omics_clinical_nav()
RETURNS void
LANGUAGE plpgsql
AS $nav$
DECLARE
  v_info_id integer;
  v_node_id integer;
BEGIN
  SELECT ni."Id"
  INTO v_info_id
  FROM portal."NodeInfos" ni
  WHERE ni."Name" = 'omics_clinical';

  IF v_info_id IS NULL THEN
    INSERT INTO portal."NodeInfos" ("Name", "Singleton", "TypeId", "FrameKey")
    VALUES ('omics_clinical', true, 1, 'omics.clinical')
    RETURNING "Id" INTO v_info_id;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM portal."NavTree" n
    WHERE n."InfoID" = v_info_id AND n."Caption" = 'Clinical data'
  ) THEN
    SELECT node_id
    INTO v_node_id
    FROM portal.sp_nav_register_node(NULL, 'Clinical data', 10::smallint, v_info_id);
  END IF;
END;
$nav$;

SELECT portal.sp_register_omics_clinical_nav();
