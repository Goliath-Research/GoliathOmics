CREATE TABLE dbo.sample_dmps (
  sample_id INT NOT NULL
 ,chromosome VARCHAR(10) NOT NULL
 ,context VARCHAR(3) NOT NULL
 ,position INT NOT NULL
 ,p_value FLOAT NOT NULL
 ,weight FLOAT NOT NULL
 ,direction INT NOT NULL
 ,CONSTRAINT PK_sample_dmps PRIMARY KEY CLUSTERED (position, context, chromosome, sample_id)
)
GO

CREATE INDEX IX_sample_dmps_key
ON dbo.sample_dmps (sample_id, chromosome)
INCLUDE (position, p_value, weight, direction);
GO