/*
  Versioned cfg.analyte catalog + study/sample FKs (Azure SQL).

  - Ensures cfg.analyte exists (also declared in cfg_registry_tables.sql).
  - Study default_analyte_id + dual-write regulatory.primary_analyte.
  - assay_procedure.analyte_id (typed; keeps primary_analyte string).
  - portal.Samples.analyte_id for enrollment hard-filter.
  - Portal list/get/catalog + sample setter + study defaults extension.

  Prerequisites:
  - cfg_registry_tables.sql, cfg_repo_api.sql (analyte kind)
  - cfg_assay_procedure_links.sql (study default profile/procedure FKs)
  - cfg_portal_api.sql (enrollment picker)
*/

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

IF SCHEMA_ID(N'cfg') IS NULL
BEGIN
    RAISERROR(N'Prerequisite missing: cfg schema (cfg_schema.sql).', 16, 1);
    RETURN;
END
GO

IF SCHEMA_ID(N'portal') IS NULL
    EXEC(N'CREATE SCHEMA portal');
GO

IF OBJECT_ID(N'cfg.analyte', N'U') IS NULL
BEGIN
    CREATE TABLE cfg.analyte (
        id bigint IDENTITY(1,1) NOT NULL PRIMARY KEY,
        name nvarchar(256) NOT NULL,
        version nvarchar(64) NOT NULL CONSTRAINT DF_cfg_analyte_version_cat DEFAULT (N'1'),
        status varchar(32) NOT NULL CONSTRAINT DF_cfg_analyte_status_cat DEFAULT ('draft'),
        content_hash nvarchar(128) NOT NULL,
        document_json json NOT NULL,
        created_at_utc datetime2(3) NOT NULL CONSTRAINT DF_cfg_analyte_created_cat DEFAULT (SYSUTCDATETIME()),
        updated_at_utc datetime2(3) NULL,
        CONSTRAINT uq_cfg_analyte_name_version UNIQUE (name, version),
        CONSTRAINT ck_cfg_analyte_status CHECK (status IN ('draft', 'published', 'retired'))
    );
END
GO

/* --- study / assay_procedure / Samples FKs --- */
IF COL_LENGTH(N'cfg.study', N'default_analyte_id') IS NULL
    ALTER TABLE cfg.study ADD default_analyte_id bigint NULL;
GO
IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_cfg_study_default_analyte'
)
    ALTER TABLE cfg.study WITH CHECK
    ADD CONSTRAINT FK_cfg_study_default_analyte
        FOREIGN KEY (default_analyte_id) REFERENCES cfg.analyte (id);
GO
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = N'IX_cfg_study_default_analyte'
      AND object_id = OBJECT_ID(N'cfg.study')
)
    CREATE INDEX IX_cfg_study_default_analyte ON cfg.study (default_analyte_id);
GO

IF OBJECT_ID(N'cfg.assay_procedure', N'U') IS NOT NULL
   AND COL_LENGTH(N'cfg.assay_procedure', N'analyte_id') IS NULL
    ALTER TABLE cfg.assay_procedure ADD analyte_id bigint NULL;
GO
IF OBJECT_ID(N'cfg.assay_procedure', N'U') IS NOT NULL
   AND NOT EXISTS (
        SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_cfg_ap_analyte_id'
   )
    ALTER TABLE cfg.assay_procedure WITH CHECK
    ADD CONSTRAINT FK_cfg_ap_analyte_id
        FOREIGN KEY (analyte_id) REFERENCES cfg.analyte (id);
GO
IF OBJECT_ID(N'cfg.assay_procedure', N'U') IS NOT NULL
   AND NOT EXISTS (
        SELECT 1 FROM sys.indexes WHERE name = N'IX_cfg_ap_analyte_id'
          AND object_id = OBJECT_ID(N'cfg.assay_procedure')
   )
    CREATE INDEX IX_cfg_ap_analyte_id ON cfg.assay_procedure (analyte_id);
GO

IF OBJECT_ID(N'portal.Samples', N'U') IS NOT NULL
   AND COL_LENGTH(N'portal.Samples', N'analyte_id') IS NULL
    ALTER TABLE portal.Samples ADD analyte_id bigint NULL;
GO
IF OBJECT_ID(N'portal.Samples', N'U') IS NOT NULL
   AND NOT EXISTS (
        SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_portal_Samples_analyte'
   )
    ALTER TABLE portal.Samples WITH CHECK
    ADD CONSTRAINT FK_portal_Samples_analyte
        FOREIGN KEY (analyte_id) REFERENCES cfg.analyte (id);
GO
IF OBJECT_ID(N'portal.Samples', N'U') IS NOT NULL
   AND NOT EXISTS (
        SELECT 1 FROM sys.indexes WHERE name = N'IX_portal_Samples_analyte'
          AND object_id = OBJECT_ID(N'portal.Samples')
   )
    CREATE INDEX IX_portal_Samples_analyte ON portal.Samples (analyte_id);
GO

CREATE OR ALTER VIEW cfg.v_analyte AS
SELECT
    a.id AS analyte_id,
    a.name AS analyte_name,
    a.version AS analyte_version,
    a.status,
    JSON_VALUE(a.document_json, '$.catalog.title') AS catalog_title,
    JSON_VALUE(a.document_json, '$.catalog.visibility') AS catalog_visibility,
    JSON_VALUE(a.document_json, '$.catalog.lifecycle') AS catalog_lifecycle,
    CAST(JSON_QUERY(a.document_json, '$.aliases') AS json) AS aliases
FROM cfg.analyte a;
GO

CREATE OR ALTER VIEW cfg.v_assay_procedure AS
SELECT
    ap.id AS assay_procedure_id,
    ap.name AS procedure_name,
    ap.version AS procedure_version,
    ap.status,
    ap.primary_analyte,
    ap.analyte_id,
    an.name AS analyte_name,
    ap.default_pipeline_profile_id,
    pp.name AS default_pipeline_profile_name,
    ap.sample_prep_program_id,
    sp.name AS sample_prep_program_name,
    ap.lifecycle_program_id,
    lp.name AS lifecycle_program_name,
    JSON_VALUE(ap.document_json, '$.catalog.title') AS catalog_title,
    JSON_VALUE(ap.document_json, '$.catalog.visibility') AS catalog_visibility,
    JSON_VALUE(ap.document_json, '$.catalog.lifecycle') AS catalog_lifecycle
FROM cfg.assay_procedure ap
LEFT JOIN cfg.analyte an ON an.id = ap.analyte_id
LEFT JOIN cfg.pipeline_profile pp ON pp.id = ap.default_pipeline_profile_id
LEFT JOIN cfg.domain_program sp ON sp.id = ap.sample_prep_program_id
LEFT JOIN cfg.domain_program lp ON lp.id = ap.lifecycle_program_id;
GO

/* Bind assay_procedure.analyte_id from primary_analyte / analyteExpectation name. */
CREATE OR ALTER PROCEDURE cfg.cfg_repo_bind_assay_procedure
    @procedure_name nvarchar(256),
    @procedure_version nvarchar(64) = N'1',
    @primary_analyte nvarchar(64) = NULL,
    @default_pipeline_profile_name nvarchar(256) = NULL,
    @sample_prep_program_name nvarchar(256) = NULL,
    @lifecycle_program_name nvarchar(256) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @ap_id bigint = (
        SELECT TOP (1) id FROM cfg.assay_procedure
        WHERE name = @procedure_name
          AND (@procedure_version IS NULL OR version = @procedure_version)
        ORDER BY id DESC
    );
    IF @ap_id IS NULL
    BEGIN
        RAISERROR(N'cfg.assay_procedure not found: %s', 16, 1, @procedure_name);
        RETURN;
    END;

    DECLARE @profile_id bigint = NULL;
    IF @default_pipeline_profile_name IS NOT NULL AND LTRIM(RTRIM(@default_pipeline_profile_name)) <> N''
        SET @profile_id = (
            SELECT TOP (1) id FROM cfg.pipeline_profile
            WHERE name = @default_pipeline_profile_name
            ORDER BY CASE status WHEN 'published' THEN 0 WHEN 'retired' THEN 1 ELSE 2 END, id DESC
        );

    DECLARE @sp_id bigint = NULL;
    IF @sample_prep_program_name IS NOT NULL AND LTRIM(RTRIM(@sample_prep_program_name)) <> N''
        SET @sp_id = (
            SELECT TOP (1) id FROM cfg.domain_program
            WHERE name = @sample_prep_program_name
            ORDER BY CASE status WHEN 'published' THEN 0 ELSE 1 END, id DESC
        );

    DECLARE @lc_id bigint = NULL;
    IF @lifecycle_program_name IS NOT NULL AND LTRIM(RTRIM(@lifecycle_program_name)) <> N''
        SET @lc_id = (
            SELECT TOP (1) id FROM cfg.domain_program
            WHERE name = @lifecycle_program_name
            ORDER BY CASE status WHEN 'published' THEN 0 ELSE 1 END, id DESC
        );

    DECLARE @analyte_row_id bigint = NULL;
    IF @primary_analyte IS NOT NULL AND LTRIM(RTRIM(@primary_analyte)) <> N''
        SET @analyte_row_id = (
            SELECT TOP (1) id FROM cfg.analyte
            WHERE name = @primary_analyte
              AND status IN ('published', 'retired')
            ORDER BY id DESC
        );

    UPDATE cfg.assay_procedure
    SET primary_analyte = COALESCE(@primary_analyte, primary_analyte),
        analyte_id = COALESCE(@analyte_row_id, analyte_id),
        default_pipeline_profile_id = COALESCE(@profile_id, default_pipeline_profile_id),
        sample_prep_program_id = COALESCE(@sp_id, sample_prep_program_id),
        lifecycle_program_id = COALESCE(@lc_id, lifecycle_program_id),
        updated_at_utc = SYSUTCDATETIME()
    WHERE id = @ap_id;

    SELECT
        ap.id,
        ap.name,
        ap.primary_analyte,
        ap.analyte_id,
        ap.default_pipeline_profile_id,
        ap.sample_prep_program_id,
        ap.lifecycle_program_id
    FROM cfg.assay_procedure ap
    WHERE ap.id = @ap_id;
END
GO

/* Backfill study.default_analyte_id from regulatory.primary_analyte when unset. */
CREATE OR ALTER PROCEDURE cfg.cfg_repo_backfill_study_analyte_defaults
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE s
    SET default_analyte_id = a.id,
        updated_at_utc = SYSUTCDATETIME()
    FROM cfg.study s
    CROSS APPLY (
        SELECT TOP (1) x.id
        FROM cfg.analyte x
        WHERE x.name = JSON_VALUE(s.document_json, '$.regulatory.primary_analyte')
          AND x.status IN ('published', 'retired')
        ORDER BY x.id DESC
    ) a
    WHERE s.default_analyte_id IS NULL
      AND NULLIF(LTRIM(RTRIM(JSON_VALUE(s.document_json, '$.regulatory.primary_analyte'))), N'') IS NOT NULL;

    SELECT @@ROWCOUNT AS studies_updated;
END
GO

CREATE OR ALTER PROCEDURE portal.sp_list_analytes
    @published_only bit = 0
AS
BEGIN
    SET NOCOUNT ON;
    SELECT
        a.id,
        a.name,
        a.version,
        a.status,
        a.content_hash,
        a.document_json AS document_json,
        a.created_at_utc,
        a.updated_at_utc
    FROM cfg.analyte a
    WHERE (@published_only = 0 OR a.status = 'published')
    ORDER BY a.name, a.version;
END
GO

CREATE OR ALTER PROCEDURE portal.sp_get_analyte
    @name nvarchar(256),
    @version nvarchar(64) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP (1)
        a.id,
        a.name,
        a.version,
        a.status,
        a.content_hash,
        a.document_json AS document_json,
        a.created_at_utc,
        a.updated_at_utc
    FROM cfg.analyte a
    WHERE a.name = @name
      AND (@version IS NULL OR a.version = @version)
    ORDER BY a.id DESC;
END
GO

CREATE OR ALTER PROCEDURE portal.sp_list_analyte_catalog
    @include_advanced bit = 0
AS
BEGIN
    SET NOCOUNT ON;
    SELECT
        a.id,
        a.name,
        a.version,
        a.status,
        JSON_VALUE(a.document_json, '$.catalog.title') AS title,
        JSON_VALUE(a.document_json, '$.catalog.summary') AS summary,
        JSON_VALUE(a.document_json, '$.catalog.visibility') AS visibility,
        JSON_VALUE(a.document_json, '$.catalog.lifecycle') AS lifecycle,
        JSON_VALUE(a.document_json, '$.catalog.family') AS family,
        JSON_VALUE(a.document_json, '$.catalog.replacedBy') AS replaced_by,
        CAST(JSON_QUERY(a.document_json, '$.aliases') AS json) AS aliases
    FROM cfg.analyte a
    WHERE a.status = 'published'
      AND JSON_VALUE(a.document_json, '$.catalog.lifecycle') = N'active'
      AND (
            JSON_VALUE(a.document_json, '$.catalog.visibility') = N'operator'
         OR (@include_advanced = 1
             AND JSON_VALUE(a.document_json, '$.catalog.visibility') = N'advanced')
          )
    ORDER BY a.name, a.version;
END
GO

CREATE OR ALTER PROCEDURE portal.sp_set_sample_analyte
    @portal_sample_id int,
    @analyte nvarchar(256) = NULL,
    @analyte_id bigint = NULL
AS
BEGIN
    SET NOCOUNT ON;

    IF OBJECT_ID(N'portal.Samples', N'U') IS NULL
    BEGIN
        RAISERROR(N'portal.Samples not found', 16, 1);
        RETURN;
    END;

    IF NOT EXISTS (SELECT 1 FROM portal.Samples WHERE ID = @portal_sample_id)
    BEGIN
        RAISERROR(N'portal.Samples not found: %d', 16, 1, @portal_sample_id);
        RETURN;
    END;

    DECLARE @resolved_id bigint = @analyte_id;
    IF @resolved_id IS NULL
       AND @analyte IS NOT NULL AND LTRIM(RTRIM(@analyte)) <> N''
        SET @resolved_id = (
            SELECT TOP (1) id FROM cfg.analyte
            WHERE name = @analyte
              AND status IN ('published', 'retired')
            ORDER BY id DESC
        );

    IF @analyte IS NOT NULL AND LTRIM(RTRIM(@analyte)) <> N'' AND @resolved_id IS NULL
       AND @analyte_id IS NULL
    BEGIN
        RAISERROR(N'cfg.analyte not found: %s', 16, 1, @analyte);
        RETURN;
    END;

    IF @analyte IS NOT NULL AND LTRIM(RTRIM(@analyte)) = N''
        SET @resolved_id = NULL;

    UPDATE portal.Samples
    SET analyte_id = @resolved_id
    WHERE ID = @portal_sample_id;

    SELECT
        s.ID AS portal_sample_id,
        s.analyte_id,
        a.name AS analyte_name
    FROM portal.Samples s
    LEFT JOIN cfg.analyte a ON a.id = s.analyte_id
    WHERE s.ID = @portal_sample_id;
END
GO

CREATE OR ALTER PROCEDURE portal.sp_list_samples_for_study_enrollment
    @customer_id int = NULL,
    @study_row_id bigint = NULL,
    @analyte_id bigint = NULL
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @filter_analyte_id bigint = @analyte_id;
    IF @filter_analyte_id IS NULL AND @study_row_id IS NOT NULL
        SELECT @filter_analyte_id = default_analyte_id
        FROM cfg.study
        WHERE id = @study_row_id;

    SELECT
        s.ID AS portal_sample_id,
        s.PatientID,
        s.CustomerID,
        s.DiseaseID,
        s.analyte_id,
        a.name AS analyte_name,
        ls.ID AS lab_sample_id,
        ls.Sample AS processing_sample_key,
        ls.BatchID
    FROM portal.Samples s
    LEFT JOIN cfg.analyte a ON a.id = s.analyte_id
    LEFT JOIN portal.LabSamples ls ON ls.SampleID = s.ID
    WHERE (@customer_id IS NULL OR s.CustomerID = @customer_id)
      AND (
            @filter_analyte_id IS NULL
         OR s.analyte_id = @filter_analyte_id
          )
    ORDER BY s.ID, ls.ID;
END
GO

/* FK-aware study process defaults including analyte (overrides links script). */
CREATE OR ALTER PROCEDURE portal.sp_get_study_process_defaults
    @study_row_id bigint
AS
BEGIN
    SET NOCOUNT ON;
    SELECT
        s.id AS study_row_id,
        s.name AS study_name,
        s.version AS study_version,
        COALESCE(
            pp.name,
            JSON_VALUE(s.document_json, '$.pipelineProfile')
        ) AS pipeline_profile,
        COALESCE(
            ap.name,
            JSON_VALUE(s.document_json, '$.pipelineProcedure')
        ) AS pipeline_procedure,
        COALESCE(
            an.name,
            JSON_VALUE(s.document_json, '$.regulatory.primary_analyte')
        ) AS analyte,
        JSON_VALUE(s.document_json, '$.researchMode') AS research_mode,
        s.default_pipeline_profile_id,
        s.default_assay_procedure_id,
        s.default_analyte_id,
        JSON_VALUE(pp.document_json, '$.catalog.title') AS pipeline_profile_title,
        JSON_VALUE(ap.document_json, '$.catalog.title') AS pipeline_procedure_title,
        JSON_VALUE(an.document_json, '$.catalog.title') AS analyte_title
    FROM cfg.study s
    LEFT JOIN cfg.pipeline_profile pp ON pp.id = s.default_pipeline_profile_id
    LEFT JOIN cfg.assay_procedure ap ON ap.id = s.default_assay_procedure_id
    LEFT JOIN cfg.analyte an ON an.id = s.default_analyte_id
    WHERE s.id = @study_row_id;
END
GO

CREATE OR ALTER PROCEDURE portal.sp_set_study_process_defaults
    @study_row_id bigint,
    @pipeline_profile nvarchar(256) = NULL,
    @pipeline_procedure nvarchar(256) = NULL,
    @research_mode nvarchar(64) = NULL,
    @analyte nvarchar(256) = NULL,
    @allow_advanced bit = 0
AS
BEGIN
    SET NOCOUNT ON;

    IF NOT EXISTS (SELECT 1 FROM cfg.study WHERE id = @study_row_id)
    BEGIN
        RAISERROR(N'cfg.study not found: %I64d', 16, 1, @study_row_id);
        RETURN;
    END;

    DECLARE @profile_id bigint = NULL;
    DECLARE @assay_id bigint = NULL;
    DECLARE @analyte_row_id bigint = NULL;

    IF @pipeline_profile IS NOT NULL AND LTRIM(RTRIM(@pipeline_profile)) <> N''
    BEGIN
        SELECT TOP (1) @profile_id = p.id
        FROM cfg.pipeline_profile p
        WHERE p.name = @pipeline_profile
          AND p.status = 'published'
          AND JSON_VALUE(p.document_json, '$.catalog.lifecycle') = N'active'
          AND (
                JSON_VALUE(p.document_json, '$.catalog.visibility') = N'operator'
             OR (@allow_advanced = 1
                 AND JSON_VALUE(p.document_json, '$.catalog.visibility') = N'advanced')
              )
        ORDER BY p.id DESC;
        IF @profile_id IS NULL
        BEGIN
            RAISERROR(N'pipeline profile not in operator/advanced catalog: %s', 16, 1, @pipeline_profile);
            RETURN;
        END;
    END;

    IF @pipeline_procedure IS NOT NULL AND LTRIM(RTRIM(@pipeline_procedure)) <> N''
    BEGIN
        SELECT TOP (1) @assay_id = p.id
        FROM cfg.assay_procedure p
        WHERE p.name = @pipeline_procedure
          AND p.status = 'published'
          AND JSON_VALUE(p.document_json, '$.catalog.lifecycle') = N'active'
          AND (
                JSON_VALUE(p.document_json, '$.catalog.visibility') = N'operator'
             OR (@allow_advanced = 1
                 AND JSON_VALUE(p.document_json, '$.catalog.visibility') = N'advanced')
              )
        ORDER BY p.id DESC;
        IF @assay_id IS NULL
        BEGIN
            RAISERROR(N'assay procedure not in operator/advanced catalog: %s', 16, 1, @pipeline_procedure);
            RETURN;
        END;
    END;

    IF @analyte IS NOT NULL AND LTRIM(RTRIM(@analyte)) <> N''
    BEGIN
        SELECT TOP (1) @analyte_row_id = a.id
        FROM cfg.analyte a
        WHERE a.name = @analyte
          AND a.status = 'published'
          AND JSON_VALUE(a.document_json, '$.catalog.lifecycle') = N'active'
          AND (
                JSON_VALUE(a.document_json, '$.catalog.visibility') = N'operator'
             OR (@allow_advanced = 1
                 AND JSON_VALUE(a.document_json, '$.catalog.visibility') = N'advanced')
              )
        ORDER BY a.id DESC;
        IF @analyte_row_id IS NULL
        BEGIN
            RAISERROR(N'analyte not in operator/advanced catalog: %s', 16, 1, @analyte);
            RETURN;
        END;
    END;

    DECLARE @doc json = (
        SELECT document_json FROM cfg.study WHERE id = @study_row_id
    );
    IF @doc IS NULL
        SET @doc = CAST(N'{}' AS json);

    IF @pipeline_profile IS NOT NULL
        SET @doc = CAST(JSON_MODIFY(@doc, '$.pipelineProfile',
            CASE WHEN LTRIM(RTRIM(@pipeline_profile)) = N'' THEN NULL ELSE @pipeline_profile END) AS json);
    IF @pipeline_procedure IS NOT NULL
        SET @doc = CAST(JSON_MODIFY(@doc, '$.pipelineProcedure',
            CASE WHEN LTRIM(RTRIM(@pipeline_procedure)) = N'' THEN NULL ELSE @pipeline_procedure END) AS json);
    IF @research_mode IS NOT NULL
        SET @doc = CAST(JSON_MODIFY(@doc, '$.researchMode',
            CASE WHEN LTRIM(RTRIM(@research_mode)) = N'' THEN NULL ELSE @research_mode END) AS json);

    /* Dual-write regulatory.primary_analyte for Python runtime. */
    IF @analyte IS NOT NULL
    BEGIN
        IF JSON_QUERY(@doc, '$.regulatory') IS NULL
            SET @doc = CAST(JSON_MODIFY(@doc, '$.regulatory', JSON_QUERY(N'{}')) AS json);
        SET @doc = CAST(JSON_MODIFY(@doc, '$.regulatory.primary_analyte',
            CASE WHEN LTRIM(RTRIM(@analyte)) = N'' THEN NULL ELSE @analyte END) AS json);
    END;

    UPDATE cfg.study
    SET document_json = @doc,
        content_hash = CONVERT(nvarchar(128), HASHBYTES('SHA2_256', CONVERT(nvarchar(max), @doc)), 2),
        default_pipeline_profile_id = CASE
            WHEN @pipeline_profile IS NULL THEN default_pipeline_profile_id
            WHEN LTRIM(RTRIM(@pipeline_profile)) = N'' THEN NULL
            ELSE @profile_id
        END,
        default_assay_procedure_id = CASE
            WHEN @pipeline_procedure IS NULL THEN default_assay_procedure_id
            WHEN LTRIM(RTRIM(@pipeline_procedure)) = N'' THEN NULL
            ELSE @assay_id
        END,
        default_analyte_id = CASE
            WHEN @analyte IS NULL THEN default_analyte_id
            WHEN LTRIM(RTRIM(@analyte)) = N'' THEN NULL
            ELSE @analyte_row_id
        END,
        updated_at_utc = SYSUTCDATETIME()
    WHERE id = @study_row_id;

    EXEC portal.sp_get_study_process_defaults @study_row_id = @study_row_id;
END
GO
