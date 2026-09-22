CREATE TABLE dbo.params (
  ID INT IDENTITY
 ,sample_id INT NOT NULL
 ,chromosome VARCHAR(10) NOT NULL
 ,context VARCHAR(3) NOT NULL
 ,upstream_size INT NOT NULL
 ,downstream_size INT NOT NULL
 ,min_intron_size INT NOT NULL
 ,w_promoter FLOAT NOT NULL
 ,w_terminator FLOAT NOT NULL
 ,w_gene_body FLOAT NOT NULL
 ,w_exon FLOAT NOT NULL
 ,w_intron FLOAT NOT NULL
 ,w_unknown FLOAT NOT NULL
 ,max_gap INT NOT NULL
 ,lambda FLOAT NOT NULL
 ,PRIMARY KEY CLUSTERED (ID)
)