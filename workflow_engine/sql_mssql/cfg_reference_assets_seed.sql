/*
  Seed versioned reference_asset rows for the canonical genomes tree.
  Deploy after cfg.reference_asset exists (cfg_registry_tables + relationships).
  Endpoint goliath-genomes is seeded in portal_resource_profile.sql.

  Site role binding is cfg_site_reference_assets_seed.sql — not this file.
  Both pangenome bundles are published here; uq_cfg_sra_site_role + ck_cfg_sra_role
  allow only one pangenome_bundle per site (no WGBS role). The site-link seed
  attaches d9-1.70 and leaves d9-bs-1.70 for an explicit @links swap.
*/

DECLARE @ep_id bigint = (
    SELECT TOP (1) id FROM cfg.storage_endpoint
    WHERE name = N'goliath-genomes' AND version = N'1'
);

IF @ep_id IS NULL
BEGIN
    RAISERROR(N'cfg.storage_endpoint goliath-genomes@1 missing; deploy portal_resource_profile.sql first', 16, 1);
    RETURN;
END
GO

DECLARE @ep_id bigint = (
    SELECT TOP (1) id FROM cfg.storage_endpoint
    WHERE name = N'goliath-genomes' AND version = N'1'
);

IF NOT EXISTS (SELECT 1 FROM cfg.reference_asset WHERE name = N'linear-grch38-ensembl-114' AND version = N'1')
BEGIN
    INSERT INTO cfg.reference_asset (
        name, version, status, content_hash, document_json, storage_endpoint_id, asset_type
    )
    VALUES (
        N'linear-grch38-ensembl-114',
        N'1',
        N'published',
        CONVERT(nvarchar(128), HASHBYTES('SHA2_256', N'linear-grch38-ensembl-114@1'), 2),
        CAST(N'{"assetType":"linear_genome","destRoot":"/work/genomes/linear/GRCh38/ensembl-114","storageEndpoint":"goliath-genomes","inventoryPrefix":"linear/GRCh38/ensembl-114","recipe":{"steps":[{"op":"mkdir"},{"op":"s3_sync","storageEndpoint":"goliath-genomes","key":"linear/GRCh38/ensembl-114/","dest":"/work/genomes/linear/GRCh38/ensembl-114"}]}}' AS json),
        @ep_id,
        N'linear_genome'
    );
END
GO

DECLARE @ep_id bigint = (
    SELECT TOP (1) id FROM cfg.storage_endpoint
    WHERE name = N'goliath-genomes' AND version = N'1'
);

IF NOT EXISTS (SELECT 1 FROM cfg.reference_asset WHERE name = N'gencode-v49' AND version = N'1')
BEGIN
    INSERT INTO cfg.reference_asset (
        name, version, status, content_hash, document_json, storage_endpoint_id, asset_type
    )
    VALUES (
        N'gencode-v49',
        N'1',
        N'published',
        CONVERT(nvarchar(128), HASHBYTES('SHA2_256', N'gencode-v49@1'), 2),
        CAST(N'{"assetType":"gtf","destRoot":"/work/genomes/annotation/gencode/v49","storageEndpoint":"goliath-genomes","inventoryPrefix":"annotation/gencode/v49","recipe":{"steps":[{"op":"mkdir"},{"op":"s3_sync","storageEndpoint":"goliath-genomes","key":"annotation/gencode/v49/","dest":"/work/genomes/annotation/gencode/v49"}]}}' AS json),
        @ep_id,
        N'gtf'
    );
END
GO

DECLARE @ep_id bigint = (
    SELECT TOP (1) id FROM cfg.storage_endpoint
    WHERE name = N'goliath-genomes' AND version = N'1'
);

IF NOT EXISTS (SELECT 1 FROM cfg.reference_asset WHERE name = N'linear-grch38-ensembl-116' AND version = N'1')
BEGIN
    INSERT INTO cfg.reference_asset (
        name, version, status, content_hash, document_json, storage_endpoint_id, asset_type
    )
    VALUES (
        N'linear-grch38-ensembl-116',
        N'1',
        N'published',
        CONVERT(nvarchar(128), HASHBYTES('SHA2_256', N'linear-grch38-ensembl-116@1'), 2),
        CAST(N'{"assetType":"linear_genome","destRoot":"/work/genomes/linear/GRCh38/ensembl-116","storageEndpoint":"goliath-genomes","inventoryPrefix":"linear/GRCh38/ensembl-116","recipe":{"steps":[{"op":"mkdir"},{"op":"s3_sync","storageEndpoint":"goliath-genomes","key":"linear/GRCh38/ensembl-116/","dest":"/work/genomes/linear/GRCh38/ensembl-116"}]}}' AS json),
        @ep_id,
        N'linear_genome'
    );
END
GO

DECLARE @ep_id bigint = (
    SELECT TOP (1) id FROM cfg.storage_endpoint
    WHERE name = N'goliath-genomes' AND version = N'1'
);

IF NOT EXISTS (SELECT 1 FROM cfg.reference_asset WHERE name = N'gencode-v50' AND version = N'1')
BEGIN
    INSERT INTO cfg.reference_asset (
        name, version, status, content_hash, document_json, storage_endpoint_id, asset_type
    )
    VALUES (
        N'gencode-v50',
        N'1',
        N'published',
        CONVERT(nvarchar(128), HASHBYTES('SHA2_256', N'gencode-v50@1'), 2),
        CAST(N'{"assetType":"gtf","destRoot":"/work/genomes/annotation/gencode/v50","storageEndpoint":"goliath-genomes","inventoryPrefix":"annotation/gencode/v50","recipe":{"steps":[{"op":"mkdir"},{"op":"s3_sync","storageEndpoint":"goliath-genomes","key":"annotation/gencode/v50/","dest":"/work/genomes/annotation/gencode/v50"}]}}' AS json),
        @ep_id,
        N'gtf'
    );
END
GO

DECLARE @ep_id bigint = (
    SELECT TOP (1) id FROM cfg.storage_endpoint
    WHERE name = N'goliath-genomes' AND version = N'1'
);

IF NOT EXISTS (SELECT 1 FROM cfg.reference_asset WHERE name = N'rna-grch38-star-ensembl-116' AND version = N'1')
BEGIN
    INSERT INTO cfg.reference_asset (
        name, version, status, content_hash, document_json, storage_endpoint_id, asset_type
    )
    VALUES (
        N'rna-grch38-star-ensembl-116',
        N'1',
        N'published',
        CONVERT(nvarchar(128), HASHBYTES('SHA2_256', N'rna-grch38-star-ensembl-116@1'), 2),
        CAST(N'{"assetType":"rna_star_index","destRoot":"/work/genomes/rna/GRCh38/star/ensembl-116","storageEndpoint":"goliath-genomes","inventoryPrefix":"rna/GRCh38/star/ensembl-116","recipe":{"steps":[{"op":"mkdir"},{"op":"s3_sync","storageEndpoint":"goliath-genomes","key":"rna/GRCh38/star/ensembl-116/","dest":"/work/genomes/rna/GRCh38/star/ensembl-116"}]}}' AS json),
        @ep_id,
        N'rna_star_index'
    );
END
GO

DECLARE @ep_id bigint = (
    SELECT TOP (1) id FROM cfg.storage_endpoint
    WHERE name = N'goliath-genomes' AND version = N'1'
);

IF NOT EXISTS (SELECT 1 FROM cfg.reference_asset WHERE name = N'rna-grch38-kallisto-gencode-v50' AND version = N'1')
BEGIN
    INSERT INTO cfg.reference_asset (
        name, version, status, content_hash, document_json, storage_endpoint_id, asset_type
    )
    VALUES (
        N'rna-grch38-kallisto-gencode-v50',
        N'1',
        N'published',
        CONVERT(nvarchar(128), HASHBYTES('SHA2_256', N'rna-grch38-kallisto-gencode-v50@1'), 2),
        CAST(N'{"assetType":"rna_kallisto_index","destRoot":"/work/genomes/rna/GRCh38/kallisto","storageEndpoint":"goliath-genomes","inventoryPrefix":"rna/GRCh38/kallisto","recipe":{"steps":[{"op":"mkdir"},{"op":"s3_sync","storageEndpoint":"goliath-genomes","key":"rna/GRCh38/kallisto/","dest":"/work/genomes/rna/GRCh38/kallisto"}]}}' AS json),
        @ep_id,
        N'rna_kallisto_index'
    );
END
GO

DECLARE @ep_id bigint = (
    SELECT TOP (1) id FROM cfg.storage_endpoint
    WHERE name = N'goliath-genomes' AND version = N'1'
);

IF NOT EXISTS (SELECT 1 FROM cfg.reference_asset WHERE name = N'pangenome-grch38-d9-1.70' AND version = N'1')
BEGIN
    INSERT INTO cfg.reference_asset (
        name, version, status, content_hash, document_json, storage_endpoint_id, asset_type
    )
    VALUES (
        N'pangenome-grch38-d9-1.70',
        N'1',
        N'published',
        CONVERT(nvarchar(128), HASHBYTES('SHA2_256', N'pangenome-grch38-d9-1.70@1'), 2),
        CAST(N'{"assetType":"pangenome_bundle","destRoot":"/work/genomes/pangenome/GRCh38/d9/1.70","storageEndpoint":"goliath-genomes","inventoryPrefix":"pangenome/GRCh38/d9/1.70","recipe":{"steps":[{"op":"mkdir"},{"op":"s3_sync","storageEndpoint":"goliath-genomes","key":"pangenome/GRCh38/d9/1.70/","dest":"/work/genomes/pangenome/GRCh38/d9/1.70"}]}}' AS json),
        @ep_id,
        N'pangenome_bundle'
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM cfg.reference_asset WHERE name = N'pangenome-grch38-d9-bs-1.70' AND version = N'1')
BEGIN
    INSERT INTO cfg.reference_asset (
        name, version, status, content_hash, document_json, storage_endpoint_id, asset_type
    )
    VALUES (
        N'pangenome-grch38-d9-bs-1.70',
        N'1',
        N'published',
        CONVERT(nvarchar(128), HASHBYTES('SHA2_256', N'pangenome-grch38-d9-bs-1.70@1'), 2),
        CAST(N'{"assetType":"pangenome_wgbs_bundle","destRoot":"/work/genomes/pangenome/GRCh38/d9-bs/1.70","storageEndpoint":"goliath-genomes","inventoryPrefix":"pangenome/GRCh38/d9-bs/1.70","recipe":{"steps":[{"op":"mkdir"},{"op":"s3_sync","storageEndpoint":"goliath-genomes","key":"pangenome/GRCh38/d9-bs/1.70/","dest":"/work/genomes/pangenome/GRCh38/d9-bs/1.70"}]}}' AS json),
        @ep_id,
        N'pangenome_wgbs_bundle'
    );
END
GO
