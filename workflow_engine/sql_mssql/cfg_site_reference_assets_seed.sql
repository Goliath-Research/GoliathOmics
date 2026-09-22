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
  assets. Both would compete for pangenome_bundle. @links binds the stock
  (non-BS) bundle. WGBS site: swap that row. Dual bind is a model change.

  Skips if cfg.site default@1 is missing (re-run after site publish, or
  methyl-cfg link-site-assets --deploy-db).
*/

DECLARE @site_id bigint = (
    SELECT TOP (1) id FROM cfg.site
    WHERE name = N'default' AND version = N'1'
);

IF @site_id IS NULL
BEGIN
    PRINT N'skip cfg.site_reference_asset: cfg.site default@1 missing; publish the site then re-run this script or methyl-cfg link-site-assets --deploy-db';
    RETURN;
END

DECLARE @links TABLE (
    asset_name nvarchar(256) NOT NULL,
    asset_version nvarchar(64) NOT NULL,
    asset_role nvarchar(64) NOT NULL
);

INSERT INTO @links (asset_name, asset_version, asset_role) VALUES
    (N'linear-grch38-ensembl-116', N'1', N'reference_genome'),
    (N'gencode-v50', N'1', N'annotation_gtf'),
    -- Stock Giraffe HPRC d9. WGBS site: replace with pangenome-grch38-d9-bs-1.70
    -- under the same pangenome_bundle role (cannot link both).
    (N'pangenome-grch38-d9-1.70', N'1', N'pangenome_bundle');
    -- Swap-in for a WGBS-only site (comment the d9-1.70 row above):
    -- (N'pangenome-grch38-d9-bs-1.70', N'1', N'pangenome_bundle');

IF EXISTS (
    SELECT 1
    FROM @links l
    WHERE NOT EXISTS (
        SELECT 1 FROM cfg.reference_asset ra
        WHERE ra.name = l.asset_name AND ra.version = l.asset_version
    )
)
BEGIN
    RAISERROR(N'cfg.site_reference_asset: a @links asset is missing from cfg.reference_asset; deploy cfg_reference_assets_seed.sql first', 16, 1);
    RETURN;
END

DECLARE @asset_id bigint;
DECLARE @role nvarchar(64);
DECLARE @link_name nvarchar(256);
DECLARE @link_ver nvarchar(64);

DECLARE link_cursor CURSOR LOCAL FAST_FORWARD FOR
    SELECT l.asset_name, l.asset_version, l.asset_role, ra.id
    FROM @links l
    INNER JOIN cfg.reference_asset ra
        ON ra.name = l.asset_name AND ra.version = l.asset_version;

OPEN link_cursor;
FETCH NEXT FROM link_cursor INTO @link_name, @link_ver, @role, @asset_id;
WHILE @@FETCH_STATUS = 0
BEGIN
    EXEC cfg.cfg_repo_link_site_asset
        @site_id = @site_id,
        @reference_asset_id = @asset_id,
        @asset_role = @role;
    FETCH NEXT FROM link_cursor INTO @link_name, @link_ver, @role, @asset_id;
END
CLOSE link_cursor;
DEALLOCATE link_cursor;
GO
