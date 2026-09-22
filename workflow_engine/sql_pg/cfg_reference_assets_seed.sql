/*
  Seed versioned reference_asset rows for the canonical genomes tree.
  Deploy after cfg.reference_asset exists; endpoint from portal_resource_profile.sql.

  Site role binding is cfg_site_reference_assets_seed.sql — not this file.
  Both pangenome bundles are published here; uq_cfg_sra_site_role + ck_cfg_sra_role
  allow only one pangenome_bundle per site (no WGBS role). The site-link seed
  attaches d9-1.70 and leaves d9-bs-1.70 for an explicit links-list swap.
*/

DO $$
DECLARE
  ep_id bigint;
BEGIN
  SELECT id INTO ep_id
  FROM cfg.storage_endpoint
  WHERE name = 'goliath-genomes' AND version = '1'
  LIMIT 1;

  IF ep_id IS NULL THEN
    RAISE EXCEPTION 'cfg.storage_endpoint goliath-genomes@1 missing; deploy portal_resource_profile.sql first';
  END IF;

  INSERT INTO cfg.reference_asset (
    name, version, status, content_hash, document_json, storage_endpoint_id, asset_type
  )
  SELECT
    'linear-grch38-ensembl-114',
    '1',
    'published',
    md5('linear-grch38-ensembl-114@1'),
    '{"assetType":"linear_genome","destRoot":"/work/genomes/linear/GRCh38/ensembl-114","storageEndpoint":"goliath-genomes","inventoryPrefix":"linear/GRCh38/ensembl-114","recipe":{"steps":[{"op":"mkdir"},{"op":"s3_sync","storageEndpoint":"goliath-genomes","key":"linear/GRCh38/ensembl-114/","dest":"/work/genomes/linear/GRCh38/ensembl-114"}]}}'::jsonb,
    ep_id,
    'linear_genome'
  WHERE NOT EXISTS (
    SELECT 1 FROM cfg.reference_asset
    WHERE name = 'linear-grch38-ensembl-114' AND version = '1'
  );

  INSERT INTO cfg.reference_asset (
    name, version, status, content_hash, document_json, storage_endpoint_id, asset_type
  )
  SELECT
    'gencode-v49',
    '1',
    'published',
    md5('gencode-v49@1'),
    '{"assetType":"gtf","destRoot":"/work/genomes/annotation/gencode/v49","storageEndpoint":"goliath-genomes","inventoryPrefix":"annotation/gencode/v49","recipe":{"steps":[{"op":"mkdir"},{"op":"s3_sync","storageEndpoint":"goliath-genomes","key":"annotation/gencode/v49/","dest":"/work/genomes/annotation/gencode/v49"}]}}'::jsonb,
    ep_id,
    'gtf'
  WHERE NOT EXISTS (
    SELECT 1 FROM cfg.reference_asset WHERE name = 'gencode-v49' AND version = '1'
  );

  INSERT INTO cfg.reference_asset (
    name, version, status, content_hash, document_json, storage_endpoint_id, asset_type
  )
  SELECT
    'linear-grch38-ensembl-116',
    '1',
    'published',
    md5('linear-grch38-ensembl-116@1'),
    '{"assetType":"linear_genome","destRoot":"/work/genomes/linear/GRCh38/ensembl-116","storageEndpoint":"goliath-genomes","inventoryPrefix":"linear/GRCh38/ensembl-116","recipe":{"steps":[{"op":"mkdir"},{"op":"s3_sync","storageEndpoint":"goliath-genomes","key":"linear/GRCh38/ensembl-116/","dest":"/work/genomes/linear/GRCh38/ensembl-116"}]}}'::jsonb,
    ep_id,
    'linear_genome'
  WHERE NOT EXISTS (
    SELECT 1 FROM cfg.reference_asset
    WHERE name = 'linear-grch38-ensembl-116' AND version = '1'
  );

  INSERT INTO cfg.reference_asset (
    name, version, status, content_hash, document_json, storage_endpoint_id, asset_type
  )
  SELECT
    'gencode-v50',
    '1',
    'published',
    md5('gencode-v50@1'),
    '{"assetType":"gtf","destRoot":"/work/genomes/annotation/gencode/v50","storageEndpoint":"goliath-genomes","inventoryPrefix":"annotation/gencode/v50","recipe":{"steps":[{"op":"mkdir"},{"op":"s3_sync","storageEndpoint":"goliath-genomes","key":"annotation/gencode/v50/","dest":"/work/genomes/annotation/gencode/v50"}]}}'::jsonb,
    ep_id,
    'gtf'
  WHERE NOT EXISTS (
    SELECT 1 FROM cfg.reference_asset WHERE name = 'gencode-v50' AND version = '1'
  );

  INSERT INTO cfg.reference_asset (
    name, version, status, content_hash, document_json, storage_endpoint_id, asset_type
  )
  SELECT
    'rna-grch38-star-ensembl-116',
    '1',
    'published',
    md5('rna-grch38-star-ensembl-116@1'),
    '{"assetType":"rna_star_index","destRoot":"/work/genomes/rna/GRCh38/star/ensembl-116","storageEndpoint":"goliath-genomes","inventoryPrefix":"rna/GRCh38/star/ensembl-116","recipe":{"steps":[{"op":"mkdir"},{"op":"s3_sync","storageEndpoint":"goliath-genomes","key":"rna/GRCh38/star/ensembl-116/","dest":"/work/genomes/rna/GRCh38/star/ensembl-116"}]}}'::jsonb,
    ep_id,
    'rna_star_index'
  WHERE NOT EXISTS (
    SELECT 1 FROM cfg.reference_asset
    WHERE name = 'rna-grch38-star-ensembl-116' AND version = '1'
  );

  INSERT INTO cfg.reference_asset (
    name, version, status, content_hash, document_json, storage_endpoint_id, asset_type
  )
  SELECT
    'rna-grch38-kallisto-gencode-v50',
    '1',
    'published',
    md5('rna-grch38-kallisto-gencode-v50@1'),
    '{"assetType":"rna_kallisto_index","destRoot":"/work/genomes/rna/GRCh38/kallisto","storageEndpoint":"goliath-genomes","inventoryPrefix":"rna/GRCh38/kallisto","recipe":{"steps":[{"op":"mkdir"},{"op":"s3_sync","storageEndpoint":"goliath-genomes","key":"rna/GRCh38/kallisto/","dest":"/work/genomes/rna/GRCh38/kallisto"}]}}'::jsonb,
    ep_id,
    'rna_kallisto_index'
  WHERE NOT EXISTS (
    SELECT 1 FROM cfg.reference_asset
    WHERE name = 'rna-grch38-kallisto-gencode-v50' AND version = '1'
  );

  INSERT INTO cfg.reference_asset (
    name, version, status, content_hash, document_json, storage_endpoint_id, asset_type
  )
  SELECT
    'pangenome-grch38-d9-1.70',
    '1',
    'published',
    md5('pangenome-grch38-d9-1.70@1'),
    '{"assetType":"pangenome_bundle","destRoot":"/work/genomes/pangenome/GRCh38/d9/1.70","storageEndpoint":"goliath-genomes","inventoryPrefix":"pangenome/GRCh38/d9/1.70","recipe":{"steps":[{"op":"mkdir"},{"op":"s3_sync","storageEndpoint":"goliath-genomes","key":"pangenome/GRCh38/d9/1.70/","dest":"/work/genomes/pangenome/GRCh38/d9/1.70"}]}}'::jsonb,
    ep_id,
    'pangenome_bundle'
  WHERE NOT EXISTS (
    SELECT 1 FROM cfg.reference_asset
    WHERE name = 'pangenome-grch38-d9-1.70' AND version = '1'
  );

  INSERT INTO cfg.reference_asset (
    name, version, status, content_hash, document_json, storage_endpoint_id, asset_type
  )
  SELECT
    'pangenome-grch38-d9-bs-1.70',
    '1',
    'published',
    md5('pangenome-grch38-d9-bs-1.70@1'),
    '{"assetType":"pangenome_wgbs_bundle","destRoot":"/work/genomes/pangenome/GRCh38/d9-bs/1.70","storageEndpoint":"goliath-genomes","inventoryPrefix":"pangenome/GRCh38/d9-bs/1.70","recipe":{"steps":[{"op":"mkdir"},{"op":"s3_sync","storageEndpoint":"goliath-genomes","key":"pangenome/GRCh38/d9-bs/1.70/","dest":"/work/genomes/pangenome/GRCh38/d9-bs/1.70"}]}}'::jsonb,
    ep_id,
    'pangenome_wgbs_bundle'
  WHERE NOT EXISTS (
    SELECT 1 FROM cfg.reference_asset
    WHERE name = 'pangenome-grch38-d9-bs-1.70' AND version = '1'
  );
END $$;
