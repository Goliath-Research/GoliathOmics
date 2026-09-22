-- Auto-extracted by scripts/extract_legacy_schema.py from MethylPipeline.sql
-- Idempotent incremental deploy (IF OBJECT_ID / CREATE OR ALTER).
-- Do not edit by hand unless also updating the twin under sql_pg/.

-- ============================================================
-- Author	Inty Saez 
-- portal.spGetCollectionItems
-- Generic SP to retrieve any Collection with its attributes pivoted.
-- Designed for Master/Details consumption from Delphi DataModules.
--
-- IMPORTANT: This procedure lives in the [portal] schema (not e_portal).
--
-- Usage from DataModule:
--   qry.SQL.Text := 'EXEC portal.spGetCollectionItems :CollectionName';
--   qry.ParamByName('CollectionName').AsString := 'Gleason Score';
--   qry.Open;
-- ============================================================
CREATE OR ALTER PROCEDURE portal.spGetCollectionItems
    @CollectionName  varchar(200)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @CollID int;

    SELECT @CollID = ID 
    FROM [Meta].[Collections] 
    WHERE [Name] = @CollectionName;

    IF @CollID IS NULL
    BEGIN
        RAISERROR('Collection "%s" not found.', 16, 1, @CollectionName);
        RETURN;
    END;

    -- Pre-compute distinct attributes with their dominant ValueType
    -- (we take the most common type in case of mixed usage)
    IF OBJECT_ID('tempdb..#CollectionAttrs') IS NOT NULL DROP TABLE #CollectionAttrs;

    SELECT 
        civ.Name,
        MAX(civ.ValueType) AS ValueType   -- safe: all rows for same Name should have same type
    INTO #CollectionAttrs
    FROM [Meta].[CollectionItemValue] civ
    INNER JOIN [Meta].[CollectionItem] ci ON ci.ID = civ.CollectionItemID
    WHERE ci.CollectionID = @CollID
    GROUP BY civ.Name;

    DECLARE @ExtendedAttrs nvarchar(max) = N'';

    -- Build the pivot expression list
    SELECT @ExtendedAttrs = STRING_AGG(
        N'MAX(CASE WHEN civ.Name = ' + QUOTENAME(Name, '''') + 
        N' THEN ' +
        CASE 
            WHEN ValueType = 'N' THEN 'civ.ValueNumber'
            WHEN ValueType = 'D' THEN 'civ.ValueDate'
            WHEN ValueType = 'B' THEN 'civ.ValueBit'
            ELSE 'civ.ValueString'
        END +
        N' END) AS ' + QUOTENAME(Name),
        ', '
    )
    FROM #CollectionAttrs;

    IF @ExtendedAttrs IS NULL OR @ExtendedAttrs = ''
    BEGIN
        -- No attributes defined yet → return base columns only
        SELECT 
            ci.ID               AS CollectionItemID,
            ci.ParentID,
            ci.Idx,
            ci.ValueString      AS PrimaryValueString,
            ci.ValueNumber      AS PrimaryValueNumber,
            ci.ValueObjectID
        FROM [Meta].[CollectionItem] ci
        WHERE ci.CollectionID = @CollID
        ORDER BY ci.Idx;
        RETURN;
    END;

    -- Final dynamic query
    DECLARE @SQL nvarchar(max) = N'
        SELECT 
            ci.ID               AS CollectionItemID,
            ci.ParentID,
            ci.Idx,
            ci.ValueString      AS PrimaryValueString,
            ci.ValueNumber      AS PrimaryValueNumber,
            ci.ValueObjectID,
            ' + @ExtendedAttrs + N'
        FROM [Meta].[CollectionItem] ci
        LEFT JOIN [Meta].[CollectionItemValue] civ ON civ.CollectionItemID = ci.ID
        WHERE ci.CollectionID = @CollID
        GROUP BY 
            ci.ID, ci.ParentID, ci.Idx, ci.ValueString, ci.ValueNumber, ci.ValueObjectID
        ORDER BY ci.Idx;
    ';

    EXEC sp_executesql @SQL, N'@CollID int', @CollID;

    DROP TABLE #CollectionAttrs;
END
GO

/*
    Author  Inty Saez
    Date    05/07/2026
    Subject Move NavTree Node (rebuild NodePath/SortPath for subtree)
*/
CREATE OR ALTER PROCEDURE portal.spNavTreeMove
    @NodeID INT,
    @NewParentID INT = -1,
    @NewSeq SMALLINT = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    IF @NodeID IS NULL OR @NodeID <= 0
        THROW 50010, 'ValidationError: NodeID is required.', 1;

    IF @NewParentID IS NULL OR @NewParentID = 0 OR @NewParentID < -1
        THROW 50023, 'ValidationError: NewParentID must be -1 (root) or a valid node ID.', 1;

    IF @NewParentID = @NodeID
        THROW 50011, 'BusinessRule: A node cannot be its own parent.', 1;

    DECLARE @CurrentParentID INT;
    DECLARE @CurrentSeq SMALLINT;
    DECLARE @OldNodePath VARCHAR(2048);
    DECLARE @OldSortPath VARCHAR(2048);
    DECLARE @ScopeRootPath VARCHAR(2048);
    DECLARE @RootSlashPos INT;

    DECLARE @ParentNodePath VARCHAR(2048) = NULL;
    DECLARE @ParentSortPath VARCHAR(2048) = NULL;
    DECLARE @EffectiveNewSeq SMALLINT;
    DECLARE @NewNodePath VARCHAR(2048);
    DECLARE @NewSortPath VARCHAR(2048);
    DECLARE @SortSegment VARCHAR(5);

    BEGIN TRY
        BEGIN TRANSACTION;

        SELECT
            @CurrentParentID = n.ParentID,
            @CurrentSeq = n.Seq,
            @OldNodePath = n.NodePath,
            @OldSortPath = n.SortPath
        FROM [portal].[NavTree] n WITH (UPDLOCK, HOLDLOCK)
        WHERE n.ID = @NodeID;

        IF @OldNodePath IS NULL
            THROW 50012, 'BusinessRule: Node does not exist or does not have a NodePath.', 1;

        IF @OldSortPath IS NULL OR LTRIM(RTRIM(@OldSortPath)) = ''
            THROW 50013, 'BusinessRule: Node does not have a SortPath.', 1;

        -- Extract root path segment from current node path (e.g., '/1' from '/1/22/23').
        SET @RootSlashPos = CHARINDEX('/', @OldNodePath, 2);
        SET @ScopeRootPath = CASE
                                WHEN @RootSlashPos > 0 THEN LEFT(@OldNodePath, @RootSlashPos - 1)
                                ELSE @OldNodePath
                             END;

        -- Validate new parent node exists and has a SortPath.
        IF @NewParentID <> -1
        BEGIN
            SELECT
                @ParentNodePath = p.NodePath,
                @ParentSortPath = p.SortPath
            FROM [portal].[NavTree] p WITH (UPDLOCK, HOLDLOCK)
            WHERE p.ID = @NewParentID;

            IF @ParentNodePath IS NULL
                THROW 50014, 'BusinessRule: Target parent does not exist or does not have a NodePath.', 1;

            -- Validate new parent has a SortPath.
            IF @ParentSortPath IS NULL OR LTRIM(RTRIM(@ParentSortPath)) = ''
                THROW 50015, 'BusinessRule: Target parent does not have a SortPath.', 1;

            -- Validate new parent is not the same as the old parent or a descendant of the old parent.
            IF @ParentNodePath = @OldNodePath OR @ParentNodePath LIKE @OldNodePath + '/%'
                THROW 50016, 'BusinessRule: Cannot move node under itself or its descendants.', 1;

            IF NOT (@ParentNodePath = @ScopeRootPath OR @ParentNodePath LIKE @ScopeRootPath + '/%')
                THROW 50020, 'BusinessRule: Cannot move node outside its scope tree.', 1;
        END

        IF @OldNodePath = @ScopeRootPath AND @NewParentID <> -1
            THROW 50021, 'BusinessRule: Scope root node cannot be moved under another parent.', 1;

        IF @NewParentID = -1 AND @OldNodePath <> @ScopeRootPath
            THROW 50022, 'BusinessRule: Cannot detach a scope node from its root tree.', 1;

        SET @EffectiveNewSeq = ISNULL(@NewSeq, @CurrentSeq);
        SET @SortSegment = RIGHT(REPLICATE('0', 5) + CONVERT(VARCHAR(5), @EffectiveNewSeq), 5);

        -- Early exit when parent and effective sequence are unchanged (no-op move request).
        IF @NewParentID = @CurrentParentID AND @EffectiveNewSeq = @CurrentSeq
        BEGIN
            COMMIT TRANSACTION;

            SELECT
                @NodeID AS NodeID,
                @CurrentParentID AS ParentID,
                @CurrentSeq AS Seq,
                @OldNodePath AS NodePath,
                @OldSortPath AS SortPath;
            RETURN 0;
        END

        
        IF @NewParentID = -1
        BEGIN
            SET @NewNodePath = '/' + CONVERT(VARCHAR(20), @NodeID);
            SET @NewSortPath = '/' + @SortSegment;
        END
        ELSE
        BEGIN
            SET @NewNodePath = @ParentNodePath + '/' + CONVERT(VARCHAR(20), @NodeID);
            SET @NewSortPath = @ParentSortPath + '/' + @SortSegment;
        END

        IF LEN(@NewNodePath) > 2048 OR LEN(@NewSortPath) > 2048
            THROW 50017, 'BusinessRule: New NodePath/SortPath length exceeds 2048.', 1;

        IF EXISTS
        (
            SELECT 1
            FROM [portal].[NavTree] d
            WHERE d.NodePath LIKE @OldNodePath + '/%'
              AND
              (
                  LEN(@NewNodePath + SUBSTRING(d.NodePath, LEN(@OldNodePath) + 1, 2048)) > 2048
                  OR LEN(@NewSortPath + SUBSTRING(d.SortPath, LEN(@OldSortPath) + 1, 2048)) > 2048
              )
        )
            THROW 50018, 'BusinessRule: Move would exceed NodePath/SortPath length for descendants.', 1;

        IF EXISTS
        (
            SELECT 1
            FROM [portal].[NavTree] d
            WHERE d.NodePath LIKE @OldNodePath + '/%'
              AND (d.SortPath IS NULL OR LTRIM(RTRIM(d.SortPath)) = '')
        )
            THROW 50019, 'BusinessRule: Cannot move subtree with descendants missing SortPath.', 1;

        UPDATE [portal].[NavTree]
        SET ParentID = @NewParentID,
            Seq = @EffectiveNewSeq,
            NodePath = @NewNodePath,
            SortPath = @NewSortPath
        WHERE ID = @NodeID;

        UPDATE d
        SET
            d.NodePath = @NewNodePath + SUBSTRING(d.NodePath, LEN(@OldNodePath) + 1, 2048),
            d.SortPath = @NewSortPath + SUBSTRING(d.SortPath, LEN(@OldSortPath) + 1, 2048)
        FROM [portal].[NavTree] d
        WHERE d.NodePath LIKE @OldNodePath + '/%';

        COMMIT TRANSACTION;

        SELECT
            @NodeID AS NodeID,
            @NewParentID AS ParentID,
            @EffectiveNewSeq AS Seq,
            @NewNodePath AS NodePath,
            @NewSortPath AS SortPath;
    END TRY
    BEGIN CATCH
        IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH
END
GO

/*
    Author  Inty Saez
    Date    05/07/2026
    Subject Delete NavTree Node (cascade subtree delete by NodePath)
*/
CREATE OR ALTER PROCEDURE portal.spNavTreeDeleteNode
    @NodeID INT
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    IF @NodeID IS NULL OR @NodeID <= 0
        THROW 50030, 'ValidationError: NodeID is required.', 1;

    DECLARE @NodePath VARCHAR(2048);
    DECLARE @ParentID INT;
    DECLARE @DeletedRows INT;

    BEGIN TRY
        BEGIN TRANSACTION;

        SELECT
            @NodePath = n.NodePath,
            @ParentID = n.ParentID
        FROM [portal].[NavTree] n WITH (UPDLOCK, HOLDLOCK)
        WHERE n.ID = @NodeID;

        IF @NodePath IS NULL
            THROW 50031, 'BusinessRule: Node does not exist or does not have a NodePath.', 1;

        IF @ParentID = -1
            THROW 50032, 'BusinessRule: Scope root node cannot be deleted.', 1;

        DELETE n
        FROM [portal].[NavTree] n
        WHERE n.NodePath = @NodePath
           OR n.NodePath LIKE @NodePath + '/%';

        SET @DeletedRows = @@ROWCOUNT;

        COMMIT TRANSACTION;

        SELECT
            @NodeID AS DeletedNodeID,
            @NodePath AS DeletedNodePath,
            @DeletedRows AS DeletedRows;
    END TRY
    BEGIN CATCH
        IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH
END
GO

/*
	Author	Inty Saez 
	Date 	5/13/2026
	Subject	Get all samples vy customer 
*/

CREATE OR ALTER PROCEDURE portal.spGetSamplesByCustomer(
	@CustomerId INT
) AS
BEGIN
	SET NOCOUNT ON;

	SELECT
			s.ID 
		,	Age
		, 	Height
		, 	Weight
		, 	BMI
		, 	d.Name AS Disease
		, 	Stage
	FROM 
		portal.Samples s 
		JOIN portal.Diseases d ON s.DiseaseID = d.ID
	WHERE 
		S.CustomerID = @CustomerId;

END
GO

/*
	Author 	Inty Saez 
	Date 	6/6/2026
	Subject	Get Prostate samples by customer 
*/

CREATE OR ALTER PROCEDURE portal.spGetProstateSamplesByCustomer(
	@CustomerId INT 
) AS 
BEGIN
	SELECT 
	  s.PatientID,
	  s.Age,
	  s.Height,
	  s.Weight,
	  s.BMI,
	  s.DiseaseID,
	  s.Stage,
	  spc.PSA,
	  spc.DRE,
	  spc.GleasonScore,
	  spc.GleasonScore                 AS ReportedGleason,
	  gm.EquivalentPattern,
	  gm.RealGleasonScore,
	  gm.GradeGroup,
	  gm.RiskLevel
	FROM 
		[portal].[vSamples] s
		JOIN [portal].[SamplesProstateCancer] spc ON spc.ID = s.ID
		LEFT JOIN [portal].[vGleasonMapping] gm ON gm.ReportedValue = spc.GleasonScore
	WHERE 
		s.CustomerID = @CustomerId 
END
GO

/* 
	Author 	Inty Saez 
	Date 	5/13/2026
	Subject Get All Samples Group by customer 
*/

CREATE OR ALTER PROCEDURE portal.spGetGroupSamplesByCustomer(
	@CustomerId INT 
) AS 
BEGIN
	SET NOCOUNT ON;

	SELECT 
		g.id, g.Name, g.Description
	FROM 
		portal.Groups g 
	WHERE 
		g.CustomerID = @CustomerId;
END
GO

/* 
	Author	Inty Saez 
	Date 	5/13/2026
	Subject	Get Samples by Group 
*/

CREATE OR ALTER PROCEDURE portal.spGetSamplesByGroup(
	@GroupId INT 
) AS 
BEGIN
	set NOCOUNT ON;

	SELECT 
			S.PatientID
		,	s.Age
		, 	s.Height
		, 	s.Weight
		, 	s.BMI
		, 	d.Name AS Disease 
		, 	s.Stage
	FROM 
		portal.Samples s
		JOIN portal.GroupSamples gs ON s.ID = gs.SampleID
		JOIN portal.Diseases d ON s.DiseaseID = d.ID
	WHERE 
		gs.GroupID = @GroupId
END
GO

/* 
	Auther 	Inty Saez 
	Date 	5/13/2026
	Subject List all institution by customer 
*/

CREATE OR ALTER PROCEDURE portal.spGetAllInstitutionByCustomer (
	@CustomerId INT 
) AS 
BEGIN
	SET NOCOUNT ON;

	SET @CustomerId = 1;

	SELECT 
			i.Id
		,	i.Name
	FROM 
		portal.Institutions i 
		JOIN portal.CustomerInstitutions ci ON i.ID = ci.InstitutionID
		JOIN portal.Customers c ON ci.CustomerID = c.ID
	WHERE 
		c.id = @CustomerId;

END
GO

