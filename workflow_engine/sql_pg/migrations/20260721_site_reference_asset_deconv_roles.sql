-- Widen cfg.site_reference_asset.asset_role for plant / deconvolution atlases.
-- Safe to re-run: drops and recreates the CHECK constraint.
-- Fresh installs already get these roles from cfg_wf_relationships.sql.
-- Not applied by deploy_azure.sh — run once on upgrade of older DBs.
-- Does not add a WGBS pangenome role; that plus relaxing uq_cfg_sra_site_role
-- is a separate model change (see cfg_site_reference_assets_seed.sql).
-- MSSQL twin: ../../sql_mssql/migrations/20260721_site_reference_asset_deconv_roles.sql

ALTER TABLE cfg.site_reference_asset
  DROP CONSTRAINT IF EXISTS ck_cfg_sra_role;

ALTER TABLE cfg.site_reference_asset
  ADD CONSTRAINT ck_cfg_sra_role CHECK (asset_role IN (
    'reference_genome', 'annotation_gtf', 'pangenome_bundle',
    'mapper_cache',
    'houseman_seed_basis', 'hitimed_hierarchy_basis',
    'other'
  ));
