CREATE TABLE dbo.Samples (
  ID INT IDENTITY
 ,species_id INT NOT NULL
 ,CONSTRAINT PK_Sample PRIMARY KEY CLUSTERED (ID)
)
GO

CREATE INDEX IX_Sample_sampleid_speciesid
ON dbo.Samples (ID, species_id)
GO

ALTER TABLE dbo.Samples
ADD CONSTRAINT FK_Sample_species_species_id FOREIGN KEY (species_id) REFERENCES items.species (species_id)
GO