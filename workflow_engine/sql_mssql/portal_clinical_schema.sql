-- Auto-extracted by scripts/extract_legacy_schema.py from MethylPipeline.sql
-- Idempotent incremental deploy (IF OBJECT_ID / CREATE OR ALTER).
-- Do not edit by hand unless also updating the twin under sql_pg/.

IF SCHEMA_ID(N'portal') IS NULL EXEC(N'CREATE SCHEMA [portal]');
GO

IF OBJECT_ID(N'portal.Objs', N'U') IS NULL
BEGIN
CREATE TABLE portal.Objs (
  ObjID int IDENTITY,
  MetaObjID int NOT NULL,
  CONSTRAINT PK_Objs PRIMARY KEY CLUSTERED (ObjID, MetaObjID)
)
END
GO

IF OBJECT_ID(N'FK_Objs_Objs_ID', N'F') IS NULL AND NOT EXISTS (
  SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_Objs_Objs_ID'
)
BEGIN
  ALTER TABLE portal.Objs
  ADD CONSTRAINT FK_Objs_Objs_ID FOREIGN KEY (MetaObjID) REFERENCES Meta.Objs (ID) ON DELETE CASCADE ON UPDATE CASCADE
END
GO

IF OBJECT_ID(N'portal.NodeInfos', N'U') IS NULL
BEGIN
CREATE TABLE portal.NodeInfos (
  Id int IDENTITY,
  Name varchar(128) NOT NULL,
  Singleton bit NOT NULL DEFAULT (1),
  TypeId tinyint NULL CONSTRAINT DC_NodeInfos_TypeId DEFAULT (1),
  FrameKey nvarchar(100) NULL,
  CONSTRAINT PK_NodeInfos_Id PRIMARY KEY CLUSTERED (Id)
)
END
GO

IF OBJECT_ID(N'portal.NavTree', N'U') IS NULL
BEGIN
CREATE TABLE portal.NavTree (
  ID int IDENTITY,
  ParentID int NULL,
  Caption varchar(50) NOT NULL,
  Seq smallint NOT NULL,
  InfoID int NULL,
  NodePath varchar(2048) NULL,
  SortPath varchar(2048) NULL,
  CONSTRAINT PK_NavTree PRIMARY KEY CLUSTERED (ID)
)
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_NavTree_SortPath' AND object_id IS NOT NULL)
BEGIN
  CREATE INDEX IX_NavTree_SortPath
  ON portal.NavTree (SortPath)
  WHERE ([SortPath] IS NOT NULL)
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'UX_NavTree_NodePath' AND object_id IS NOT NULL)
BEGIN
  CREATE UNIQUE INDEX UX_NavTree_NodePath
  ON portal.NavTree (NodePath)
  WHERE ([NodePath] IS NOT NULL)
END
GO

IF OBJECT_ID(N'portal.Labs', N'U') IS NULL
BEGIN
CREATE TABLE portal.Labs (
  ID int IDENTITY,
  Name varchar(128) NOT NULL,
  CONSTRAINT PK_Labs_1 PRIMARY KEY CLUSTERED (ID)
)
END
GO

IF OBJECT_ID(N'portal.Batches', N'U') IS NULL
BEGIN
CREATE TABLE portal.Batches (
  ID int IDENTITY,
  Name varchar(128) NULL,
  LabID int NULL,
  CONSTRAINT PK_Batches PRIMARY KEY CLUSTERED (ID)
)
END
GO

IF OBJECT_ID(N'FK_Batches_Labs_ID', N'F') IS NULL AND NOT EXISTS (
  SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_Batches_Labs_ID'
)
BEGIN
  ALTER TABLE portal.Batches
  ADD CONSTRAINT FK_Batches_Labs_ID FOREIGN KEY (LabID) REFERENCES portal.Labs (ID) ON DELETE CASCADE ON UPDATE CASCADE
END
GO

IF OBJECT_ID(N'portal.Institutions', N'U') IS NULL
BEGIN
CREATE TABLE portal.Institutions (
  ID int IDENTITY,
  Name varchar(128) NOT NULL,
  CONSTRAINT PK_Institutions_1 PRIMARY KEY CLUSTERED (ID)
)
END
GO

IF OBJECT_ID(N'portal.Role2Node', N'U') IS NULL
BEGIN
CREATE TABLE portal.Role2Node (
  RoleId int NOT NULL,
  NodeId int NOT NULL,
  ScopeId int NOT NULL,
  CONSTRAINT PK_Role2Node PRIMARY KEY CLUSTERED (ScopeId, RoleId, NodeId)
)
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_Role2Node_RoleNode' AND object_id IS NOT NULL)
BEGIN
  CREATE INDEX IX_Role2Node_RoleNode
  ON portal.Role2Node (RoleId, NodeId, ScopeId)
END
GO

IF OBJECT_ID(N'FK_Role2Node_Roles_ID', N'F') IS NULL AND NOT EXISTS (
  SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_Role2Node_Roles_ID'
)
BEGIN
  ALTER TABLE portal.Role2Node
  ADD CONSTRAINT FK_Role2Node_Roles_ID FOREIGN KEY (RoleId) REFERENCES RBAC.Roles (ID) ON DELETE CASCADE ON UPDATE CASCADE
END
GO

IF OBJECT_ID(N'FK_Role2Node_Scopes_ScopeID', N'F') IS NULL AND NOT EXISTS (
  SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_Role2Node_Scopes_ScopeID'
)
BEGIN
  ALTER TABLE portal.Role2Node
  ADD CONSTRAINT FK_Role2Node_Scopes_ScopeID FOREIGN KEY (ScopeId) REFERENCES RBAC.Scopes (ScopeID) ON DELETE CASCADE ON UPDATE CASCADE
END
GO

IF OBJECT_ID(N'portal.Patients', N'U') IS NULL
BEGIN
CREATE TABLE portal.Patients (
  ID int IDENTITY,
  InstitutionID int NULL,
  Name varchar(50) NOT NULL,
  Sex char(1) NOT NULL,
  YearBorn int NULL,
  Race varchar(50) NULL,
  Ethnicity varchar(50) NULL,
  CONSTRAINT PK_Patients_1 PRIMARY KEY CLUSTERED (ID)
)
END
GO

IF OBJECT_ID(N'FK_Patients_Institutions', N'F') IS NULL AND NOT EXISTS (
  SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_Patients_Institutions'
)
BEGIN
  ALTER TABLE portal.Patients
  ADD CONSTRAINT FK_Patients_Institutions FOREIGN KEY (InstitutionID) REFERENCES portal.Institutions (ID) ON DELETE SET NULL ON UPDATE CASCADE
END
GO

IF OBJECT_ID(N'portal.InstitutionLabs', N'U') IS NULL
BEGIN
CREATE TABLE portal.InstitutionLabs (
  InstitutionID int IDENTITY,
  LabID int NOT NULL,
  CONSTRAINT PK_InstitutionLabs PRIMARY KEY CLUSTERED (InstitutionID, LabID)
)
END
GO

IF OBJECT_ID(N'FK_InstitutionLabs', N'F') IS NULL AND NOT EXISTS (
  SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_InstitutionLabs'
)
BEGIN
  ALTER TABLE portal.InstitutionLabs
  ADD CONSTRAINT FK_InstitutionLabs FOREIGN KEY (InstitutionID) REFERENCES portal.Institutions (ID)
END
GO

IF OBJECT_ID(N'FK_InstitutionLabs_Labs_ID', N'F') IS NULL AND NOT EXISTS (
  SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_InstitutionLabs_Labs_ID'
)
BEGIN
  ALTER TABLE portal.InstitutionLabs
  ADD CONSTRAINT FK_InstitutionLabs_Labs_ID FOREIGN KEY (LabID) REFERENCES portal.Labs (ID)
END
GO

IF OBJECT_ID(N'portal.Diseases', N'U') IS NULL
BEGIN
CREATE TABLE portal.Diseases (
  ID int IDENTITY,
  Name varchar(128) NOT NULL,
  ParentID int NULL,
  CONSTRAINT PK_Diseases_1 PRIMARY KEY CLUSTERED (ID)
)
END
GO

IF OBJECT_ID(N'portal.Customers', N'U') IS NULL
BEGIN
CREATE TABLE portal.Customers (
  ID int IDENTITY,
  Name varchar(100) NOT NULL,
  CONSTRAINT PK_Customers_ID PRIMARY KEY CLUSTERED (ID)
)
END
GO

IF OBJECT_ID(N'portal.Samples', N'U') IS NULL
BEGIN
CREATE TABLE portal.Samples (
  ID int IDENTITY,
  PatientID int NOT NULL,
  CustomerID int NULL,
  ParticipantID varchar(128) NULL,
  Age int NULL,
  Height int NULL,
  Weight int NULL,
  BMI float NULL,
  DiseaseID int NULL,
  Stage varchar(50) NULL,
  CONSTRAINT PK_Samples_1 PRIMARY KEY CLUSTERED (ID)
)
END
GO

IF OBJECT_ID(N'FK_Samples_Customers_ID', N'F') IS NULL AND NOT EXISTS (
  SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_Samples_Customers_ID'
)
BEGIN
  ALTER TABLE portal.Samples
  ADD CONSTRAINT FK_Samples_Customers_ID FOREIGN KEY (CustomerID) REFERENCES portal.Customers (ID) ON DELETE CASCADE ON UPDATE CASCADE
END
GO

IF OBJECT_ID(N'FK_Samples_Diseases_ID', N'F') IS NULL AND NOT EXISTS (
  SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_Samples_Diseases_ID'
)
BEGIN
  ALTER TABLE portal.Samples
  ADD CONSTRAINT FK_Samples_Diseases_ID FOREIGN KEY (DiseaseID) REFERENCES portal.Diseases (ID)
END
GO

IF OBJECT_ID(N'FK_Samples_Patients', N'F') IS NULL AND NOT EXISTS (
  SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_Samples_Patients'
)
BEGIN
  ALTER TABLE portal.Samples
  ADD CONSTRAINT FK_Samples_Patients FOREIGN KEY (PatientID) REFERENCES portal.Patients (ID) ON DELETE CASCADE ON UPDATE CASCADE
END
GO

IF OBJECT_ID(N'portal.SamplesProstateCancer', N'U') IS NULL
BEGIN
CREATE TABLE portal.SamplesProstateCancer (
  ID int NOT NULL,
  PSA float NULL,
  DRE bit NULL,
  GleasonScore varchar(10) NULL,
  CONSTRAINT PK_SamplesProstateCancer PRIMARY KEY CLUSTERED (ID)
)
END
GO

IF OBJECT_ID(N'FK_SamplesProstateCancer_Samples', N'F') IS NULL AND NOT EXISTS (
  SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_SamplesProstateCancer_Samples'
)
BEGIN
  ALTER TABLE portal.SamplesProstateCancer
  ADD CONSTRAINT FK_SamplesProstateCancer_Samples FOREIGN KEY (ID) REFERENCES portal.Samples (ID) ON DELETE CASCADE ON UPDATE CASCADE
END
GO

IF OBJECT_ID(N'portal.SamplesBreastCancer', N'U') IS NULL
BEGIN
CREATE TABLE portal.SamplesBreastCancer (
  ID int NOT NULL,
  ER_Status bit NULL,
  PR_Status bit NULL,
  HER2_Status bit NULL,
  CONSTRAINT PK_SamplesBreastCancer PRIMARY KEY CLUSTERED (ID)
)
END
GO

IF OBJECT_ID(N'FK_SamplesBreastCancer_Samples', N'F') IS NULL AND NOT EXISTS (
  SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_SamplesBreastCancer_Samples'
)
BEGIN
  ALTER TABLE portal.SamplesBreastCancer
  ADD CONSTRAINT FK_SamplesBreastCancer_Samples FOREIGN KEY (ID) REFERENCES portal.Samples (ID) ON DELETE CASCADE ON UPDATE CASCADE
END
GO

IF OBJECT_ID(N'portal.LabSamples', N'U') IS NULL
BEGIN
CREATE TABLE portal.LabSamples (
  ID int IDENTITY,
  SampleID int NOT NULL,
  BatchID int NULL,
  Sample varchar(128) NULL,
  AlignmentQC json NULL,
  CONSTRAINT PK_LabSamples_ID PRIMARY KEY CLUSTERED (ID)
)
END
GO

IF OBJECT_ID(N'FK_LabSamples_Batches_ID', N'F') IS NULL AND NOT EXISTS (
  SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_LabSamples_Batches_ID'
)
BEGIN
  ALTER TABLE portal.LabSamples
  ADD CONSTRAINT FK_LabSamples_Batches_ID FOREIGN KEY (BatchID) REFERENCES portal.Batches (ID) ON DELETE CASCADE ON UPDATE CASCADE
END
GO

IF OBJECT_ID(N'FK_LabSamples_Samples', N'F') IS NULL AND NOT EXISTS (
  SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_LabSamples_Samples'
)
BEGIN
  ALTER TABLE portal.LabSamples
  ADD CONSTRAINT FK_LabSamples_Samples FOREIGN KEY (SampleID) REFERENCES portal.Samples (ID) ON DELETE CASCADE ON UPDATE CASCADE
END
GO

IF OBJECT_ID(N'portal.Groups', N'U') IS NULL
BEGIN
CREATE TABLE portal.Groups (
  ID int IDENTITY,
  Name varchar(128) NOT NULL,
  Description varchar(256) NULL,
  CustomerID int NULL,
  CONSTRAINT PK_Groups PRIMARY KEY CLUSTERED (ID),
  CONSTRAINT UQ_Groups_CustomerID_Name UNIQUE (CustomerID, Name)
)
END
GO

IF OBJECT_ID(N'FK_Groups_Customers_ID', N'F') IS NULL AND NOT EXISTS (
  SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_Groups_Customers_ID'
)
BEGIN
  ALTER TABLE portal.Groups
  ADD CONSTRAINT FK_Groups_Customers_ID FOREIGN KEY (CustomerID) REFERENCES portal.Customers (ID) ON DELETE CASCADE ON UPDATE CASCADE
END
GO

IF OBJECT_ID(N'portal.GroupSamples', N'U') IS NULL
BEGIN
CREATE TABLE portal.GroupSamples (
  GroupID int NOT NULL,
  SampleID int NOT NULL,
  CONSTRAINT PK_GroupSamples PRIMARY KEY CLUSTERED (GroupID, SampleID)
)
END
GO

IF OBJECT_ID(N'FK_GroupSamples_Groups', N'F') IS NULL AND NOT EXISTS (
  SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_GroupSamples_Groups'
)
BEGIN
  ALTER TABLE portal.GroupSamples
  ADD CONSTRAINT FK_GroupSamples_Groups FOREIGN KEY (GroupID) REFERENCES portal.Groups (ID) ON DELETE CASCADE ON UPDATE CASCADE
END
GO

IF OBJECT_ID(N'FK_GroupSamples_Samples', N'F') IS NULL AND NOT EXISTS (
  SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_GroupSamples_Samples'
)
BEGIN
  ALTER TABLE portal.GroupSamples
  ADD CONSTRAINT FK_GroupSamples_Samples FOREIGN KEY (SampleID) REFERENCES portal.Samples (ID)
END
GO

IF OBJECT_ID(N'portal.CustomerInstitutions', N'U') IS NULL
BEGIN
CREATE TABLE portal.CustomerInstitutions (
  CustomerID int NOT NULL,
  InstitutionID int NOT NULL,
  CONSTRAINT PK_CustomerInstitutions PRIMARY KEY CLUSTERED (CustomerID, InstitutionID)
)
END
GO

IF OBJECT_ID(N'FK_CustomerInstitutions_Customers_ID', N'F') IS NULL AND NOT EXISTS (
  SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_CustomerInstitutions_Customers_ID'
)
BEGIN
  ALTER TABLE portal.CustomerInstitutions
  ADD CONSTRAINT FK_CustomerInstitutions_Customers_ID FOREIGN KEY (CustomerID) REFERENCES portal.Customers (ID) ON DELETE CASCADE ON UPDATE CASCADE
END
GO

IF OBJECT_ID(N'FK_CustomerInstitutions_Institutions_ID', N'F') IS NULL AND NOT EXISTS (
  SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_CustomerInstitutions_Institutions_ID'
)
BEGIN
  ALTER TABLE portal.CustomerInstitutions
  ADD CONSTRAINT FK_CustomerInstitutions_Institutions_ID FOREIGN KEY (InstitutionID) REFERENCES portal.Institutions (ID) ON DELETE CASCADE ON UPDATE CASCADE
END
GO

IF OBJECT_ID(N'portal.DiseaseDataSource', N'U') IS NULL
BEGIN
  CREATE TABLE [portal].[DiseaseDataSource] (
    [ID] int IDENTITY(1,1) NOT NULL,
    [DiseaseID] int NOT NULL,
    [SourceType] varchar(20) NOT NULL,
    [SourceObject] sysname NOT NULL,
    [SubtypeTable] sysname NULL,
    [KeyField] sysname NOT NULL,
    [CustomerParamName] sysname NOT NULL DEFAULT ('CustomerId'),
    [DiseaseParamName] sysname NOT NULL DEFAULT ('Disease'),
    [IsActive] bit NOT NULL DEFAULT ((1)),
    [CreatedAt] datetime2(7) NOT NULL DEFAULT (sysdatetime()),
    [UpdatedAt] datetime2(7) NOT NULL DEFAULT (sysdatetime()),
    CONSTRAINT [PK_portal_DiseaseDataSource] PRIMARY KEY ([ID])
  );
END
GO

IF OBJECT_ID(N'portal.DiseaseFieldContract', N'U') IS NULL
BEGIN
  CREATE TABLE [portal].[DiseaseFieldContract] (
    [ID] int IDENTITY(1,1) NOT NULL,
    [DiseaseID] int NOT NULL,
    [FieldName] sysname NOT NULL,
    [FieldCaption] nvarchar(128) NOT NULL,
    [DataType] varchar(20) NOT NULL,
    [IsBaseField] bit NOT NULL DEFAULT ((0)),
    [DisplayOrder] int NOT NULL,
    [ColumnWidth] int NOT NULL DEFAULT ((120)),
    [IsVisible] bit NOT NULL DEFAULT ((1)),
    [IsFilterable] bit NOT NULL DEFAULT ((1)),
    [FilterOperatorDefault] varchar(20) NOT NULL DEFAULT ('AUTO'),
    [FilterEditorKind] varchar(20) NOT NULL DEFAULT ('TEXT'),
    [IsSortable] bit NOT NULL DEFAULT ((1)),
    [IsEditable] bit NOT NULL DEFAULT ((0)),
    [IsRequired] bit NOT NULL DEFAULT ((0)),
    [ValidationJson] nvarchar(max) NULL,
    [LookupQuery] nvarchar(max) NULL,
    [TargetEntity] varchar(20) NOT NULL DEFAULT ('SAMPLE'),
    [TargetColumn] sysname NULL,
    [CollectionName] varchar(200) NULL,
    [ValueStoreMode] varchar(20) NULL,
    [MergeStrategy] varchar(20) NULL,
    [IsActive] bit NOT NULL DEFAULT ((1)),
    [CreatedAt] datetime2(7) NOT NULL DEFAULT (sysdatetime()),
    [UpdatedAt] datetime2(7) NOT NULL DEFAULT (sysdatetime()),
    CONSTRAINT [PK_portal_DiseaseFieldContract] PRIMARY KEY ([ID])
  );
END
GO

IF OBJECT_ID(N'portal.project', N'U') IS NULL
BEGIN
  CREATE TABLE [portal].[project] (
    [project_id] int IDENTITY(1,1) NOT NULL,
    [customer_id] int NOT NULL,
    [project_key] nvarchar(64) NOT NULL,
    [display_name] nvarchar(128) NOT NULL,
    [project_path] nvarchar(512) NOT NULL,
    [archive_profile_key] nvarchar(64) NOT NULL,
    [status] varchar(32) NOT NULL DEFAULT ('ACTIVE'),
    [created_at_utc] datetime2(3) NOT NULL DEFAULT (sysutcdatetime()),
    [updated_at_utc] datetime2(3) NULL,
    CONSTRAINT [PK_portal_project] PRIMARY KEY ([project_id])
  );
END
GO

IF OBJECT_ID(N'portal.SampleImportBatch', N'U') IS NULL
BEGIN
  CREATE TABLE [portal].[SampleImportBatch] (
    [ID] int IDENTITY(1,1) NOT NULL,
    [DiseaseID] int NOT NULL,
    [CustomerID] int NOT NULL,
    [InstitutionID] int NOT NULL,
    [CreatedBy] int NULL,
    [FileName] nvarchar(260) NULL,
    [FileHash] varbinary(32) NULL,
    [Status] varchar(20) NOT NULL DEFAULT ('STAGED'),
    [Mode] varchar(20) NOT NULL DEFAULT ('INSERT'),
    [MappingJson] nvarchar(max) NULL,
    [RowCount] int NOT NULL DEFAULT ((0)),
    [CreatedAt] datetime2(7) NOT NULL DEFAULT (sysdatetime()),
    [UpdatedAt] datetime2(7) NOT NULL DEFAULT (sysdatetime()),
    CONSTRAINT [PK_portal_SampleImportBatch] PRIMARY KEY ([ID])
  );
END
GO

IF OBJECT_ID(N'portal.SampleImportRow', N'U') IS NULL
BEGIN
  CREATE TABLE [portal].[SampleImportRow] (
    [ID] int IDENTITY(1,1) NOT NULL,
    [BatchID] int NOT NULL,
    [RowNo] int NOT NULL,
    [RawJson] nvarchar(max) NULL,
    [CanonicalJson] nvarchar(max) NULL,
    [Status] varchar(20) NOT NULL DEFAULT ('STAGED'),
    [ErrorsJson] nvarchar(max) NULL,
    [ConflictsJson] nvarchar(max) NULL,
    [Action] varchar(20) NULL,
    [CreatedPatientID] int NULL,
    [CreatedSampleID] int NULL,
    CONSTRAINT [PK_portal_SampleImportRow] PRIMARY KEY ([ID])
  );
END
GO

