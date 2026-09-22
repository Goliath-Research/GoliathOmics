CREATE TABLE dbo.sample_genes (
  sample_id INT NOT NULL
 ,chromosome VARCHAR(10) NOT NULL
 ,gene_id VARCHAR(50) NOT NULL
 ,gene_name VARCHAR(50) NOT NULL
 ,p_value FLOAT NOT NULL
 ,q_value FLOAT NOT NULL
 ,direction INT NULL
 ,strand CHAR(10) NULL
 ,CONSTRAINT PK_sample_genes PRIMARY KEY CLUSTERED (sample_id, chromosome, gene_id)
)