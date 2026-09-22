/*
  Bind published genome assets to cfg.site roles (default@1).

  Construction: cfg_reference_assets_seed.sql inserts cfg.reference_asset only.
  This script is the deploy-time caller of cfg.cfg_repo_link_site_asset — the
  sole writer of cfg.site_reference_asset. methyl-cfg link-site-assets is the
  post-import caller when the site is published after DDL.

  Model limits — do not "fix" in this seed:
  - uq_cfg_sra_site_role: one asset per (site_id, asset_role)
  - ck_cfg_sra_role: no WGBS pangenome role (no pangenome_wgbs_bundle)

  Both pangenome-grch38-d9-1.70 and pangenome-grch38-d9-bs-1.70 exist as
  assets. Both would compete for pangenome_bundle. The links list binds the
  stock (non-BS) bundle. WGBS site: swap that row. Dual bind is a model change.

  Skips if cfg.site default@1 is missing (re-run after site publish, or
  methyl-cfg link-site-assets --deploy-db).
*/

DO $$
DECLARE
  v_site_id bigint;
  v_missing text;
  r record;
BEGIN
  SELECT id INTO v_site_id
  FROM cfg.site
  WHERE name = 'default' AND version = '1'
  LIMIT 1;

  IF v_site_id IS NULL THEN
    RAISE NOTICE 'skip cfg.site_reference_asset: cfg.site default@1 missing; publish the site then re-run this script or methyl-cfg link-site-assets --deploy-db';
    RETURN;
  END IF;

  CREATE TEMP TABLE links (
    asset_name text NOT NULL,
    asset_version text NOT NULL,
    asset_role text NOT NULL
  ) ON COMMIT DROP;

  INSERT INTO links (asset_name, asset_version, asset_role) VALUES
    ('linear-grch38-ensembl-116', '1', 'reference_genome'),
    ('gencode-v50', '1', 'annotation_gtf'),
    -- Stock Giraffe HPRC d9. WGBS site: replace with pangenome-grch38-d9-bs-1.70
    -- under the same pangenome_bundle role (cannot link both).
    ('pangenome-grch38-d9-1.70', '1', 'pangenome_bundle');
    -- Swap-in for a WGBS-only site (comment the d9-1.70 row above):
    -- ('pangenome-grch38-d9-bs-1.70', '1', 'pangenome_bundle');

  SELECT string_agg(l.asset_name || '@' || l.asset_version, ', ')
  INTO v_missing
  FROM links l
  WHERE NOT EXISTS (
    SELECT 1 FROM cfg.reference_asset ra
    WHERE ra.name = l.asset_name AND ra.version = l.asset_version
  );

  IF v_missing IS NOT NULL THEN
    RAISE EXCEPTION 'cfg.site_reference_asset: missing reference_asset(s) %; deploy cfg_reference_assets_seed.sql first', v_missing;
  END IF;

  FOR r IN
    SELECT ra.id AS reference_asset_id, l.asset_role
    FROM links l
    INNER JOIN cfg.reference_asset ra
      ON ra.name = l.asset_name AND ra.version = l.asset_version
  LOOP
    PERFORM * FROM cfg.cfg_repo_link_site_asset(
      v_site_id, r.reference_asset_id, r.asset_role
    );
  END LOOP;
END $$;
