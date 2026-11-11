-- Operator portal NavTree for the shared platform host (/app/omics/* branch).
-- Requires GoliathApp seed_platform_nav_tree.sql (or equivalent) so /app exists.
-- Skips quietly when /app is not present (gateways without platform nav).

BEGIN;

SELECT portal.sp_nav_ensure_meta_catalog();

DO $omics_nav$
DECLARE
  v_parent_app int;
  v_parent_omics int;
  v_info_id int;
  v_role_admin int;
  v_role_god int;
  v_scope int;
  v_node int;
  nav_row record;
BEGIN
  SELECT n."ID" INTO v_parent_app
  FROM portal."NavTree" n
  WHERE n."NodePath" = '/app'
  LIMIT 1;

  IF v_parent_app IS NULL THEN
    RAISE NOTICE 'platform /app nav root missing; skipping portal host omics nav (run GoliathApp seed_platform_nav_tree.sql)';
    RETURN;
  END IF;

  SELECT ni."Id" INTO v_info_id
  FROM portal."NodeInfos" ni
  WHERE ni."Name" = 'platform.nav.omics.root'
  LIMIT 1;
  IF v_info_id IS NULL THEN
    INSERT INTO portal."NodeInfos" ("Name", "Singleton", "FrameKey")
    VALUES ('platform.nav.omics.root', true, 'artifact')
    RETURNING "Id" INTO v_info_id;
  END IF;

  SELECT n."ID" INTO v_parent_omics
  FROM portal."NavTree" n
  WHERE n."NodePath" = '/app/omics'
  LIMIT 1;

  IF v_parent_omics IS NULL THEN
    SELECT node_id INTO v_parent_omics
    FROM portal.sp_nav_register_node(
      v_parent_app,
      'Omics',
      85::smallint,
      v_info_id,
      '/app/omics',
      '0010.0085'
    );
  END IF;

  CREATE TEMP TABLE _omics_nav (
    caption text NOT NULL,
    node_path text NOT NULL,
    sort_path text NOT NULL,
    seq smallint NOT NULL,
    parent_path text NOT NULL,
    info_name text NOT NULL
  ) ON COMMIT DROP;

  INSERT INTO _omics_nav (caption, node_path, sort_path, seq, parent_path, info_name)
  VALUES
    ('Customers', '/app/omics/customers', '0010.0085.0010', 10, '/app/omics', 'platform.nav.omics.customers'),
    ('Samples', '/app/omics/samples', '0010.0085.0020', 20, '/app/omics', 'platform.nav.omics.samples'),
    ('Studies', '/app/omics/studies', '0010.0085.0030', 30, '/app/omics', 'platform.nav.omics.studies'),
    ('Sequencing runs', '/app/omics/runs', '0010.0085.0040', 40, '/app/omics', 'platform.nav.omics.runs');

  FOR nav_row IN SELECT * FROM _omics_nav ORDER BY sort_path
  LOOP
    SELECT ni."Id" INTO v_info_id
    FROM portal."NodeInfos" ni
    WHERE ni."Name" = nav_row.info_name
    LIMIT 1;
    IF v_info_id IS NULL THEN
      INSERT INTO portal."NodeInfos" ("Name", "Singleton", "FrameKey")
      VALUES (nav_row.info_name, true, 'artifact')
      RETURNING "Id" INTO v_info_id;
    END IF;

    SELECT n."ID" INTO v_node
    FROM portal."NavTree" n
    WHERE n."NodePath" = nav_row.node_path
    LIMIT 1;

    IF v_node IS NULL THEN
      SELECT node_id INTO v_node
      FROM portal.sp_nav_register_node(
        (SELECT n."ID" FROM portal."NavTree" n WHERE n."NodePath" = nav_row.parent_path LIMIT 1),
        nav_row.caption,
        nav_row.seq,
        v_info_id,
        nav_row.node_path,
        nav_row.sort_path
      );
    END IF;
  END LOOP;

  PERFORM nav_id FROM portal.sp_nav_backfill_meta(false);

  SELECT role_row."ID" INTO v_role_admin
  FROM "RBAC"."Roles" role_row
  WHERE role_row."Name" = 'organization_admin'
  LIMIT 1;
  v_role_god := portal.fn_god_architect_role_id();

  FOR v_scope IN
    SELECT s."ScopeID"
    FROM "RBAC"."Scopes" s
    WHERE s."ScopeType" = 'organization' AND COALESCE(s."Active", true)
  LOOP
    FOR v_node IN
      SELECT n."ID"
      FROM portal."NavTree" n
      WHERE n."NodePath" LIKE '/app/omics%'
    LOOP
      IF v_role_admin IS NOT NULL THEN
        PERFORM portal.sp_grant_role_nav_node(v_role_admin, v_node, v_scope);
      END IF;
      IF v_role_god IS NOT NULL THEN
        PERFORM portal.sp_grant_role_nav_node(v_role_god, v_node, v_scope);
      END IF;
    END LOOP;
  END LOOP;

  IF to_regprocedure('portal.sp_sync_god_architect_operator_entitlements()') IS NOT NULL THEN
    PERFORM portal.sp_sync_god_architect_operator_entitlements();
  END IF;
END
$omics_nav$;

COMMIT;
