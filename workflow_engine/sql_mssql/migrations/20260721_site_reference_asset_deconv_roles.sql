-- Widen cfg.site_reference_asset.asset_role for plant / deconvolution atlases.
-- For existing Azure SQL databases created before houseman/hitimed roles were
-- added to ck_cfg_sra_role. Fresh installs already get the wide CHECK from
-- cfg_wf_relationships.sql — this migration is idempotent (drop + recreate).
-- Does not add a WGBS pangenome role; that plus relaxing uq_cfg_sra_site_role
-- is a separate model change (see cfg_site_reference_assets_seed.sql).
--
-- Apply manually when upgrading an older DB (not part of deploy_azure.sh):
--   sqlcmd ... -i workflow_engine/sql_mssql/migrations/20260721_site_reference_asset_deconv_roles.sql
-- PostgreSQL twin: ../sql_pg/migrations/20260721_site_reference_asset_deconv_roles.sql

IF EXISTS (
    SELECT 1
    FROM sys.check_constraints
    WHERE name = N'ck_cfg_sra_role'
      AND parent_object_id = OBJECT_ID(N'cfg.site_reference_asset')
)
BEGIN
    ALTER TABLE cfg.site_reference_asset DROP CONSTRAINT ck_cfg_sra_role;
END
GO

ALTER TABLE cfg.site_reference_asset
    ADD CONSTRAINT ck_cfg_sra_role CHECK (asset_role IN (
        N'reference_genome', N'annotation_gtf', N'pangenome_bundle',
        N'mapper_cache',
        N'houseman_seed_basis', N'hitimed_hierarchy_basis',
        N'other'
    ));
GO
