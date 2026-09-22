# methyl-fragmentomics

Optional MethylPipeline step for **cfDNA fragmentomics** from aligned BAMs:

- **wps**: binned fragment midpoint counts (simplified WPS-style signal)
- **end_motifs**: 5' k-mer frequencies at fragment starts

Phase 1 fragment-length QC lives in **methylalignmentqc** (`actionConfig.alignment_qc.fragmentomics` on profile/site).

## Requirements

- Python 3.10-3.14
- `pysam` and indexed BAM per sample
- `methylutils` (project JSON resolver)

## Profile / site actionConfig

Tool parameters belong in **profile or site `actionConfig`**, not the study manifest:

```json
{
  "actionConfig": {
    "fragmentomics": {
      "enabled": true,
      "modes": ["wps", "end_motifs"],
      "end_motif_k": 4,
      "wps_bin_bp": 1000
    },
    "validation": {
      "regulatory": {
        "primary_analyte": "cfdna",
        "sample_type": "Plasma cfDNA WGBS"
      }
    }
  }
}
```

BAM resolution (in order):

1. `sample_bam_paths`: `{ "sampleA": "/path/sampleA.bam" }`
2. `{sample_dir}/{sample_dir.name}.bam`
3. Single `*.bam` in the sample directory

## CLI

```bash
methyl-fragmentomics --project /path/to/project.json
```

Outputs under `{project_root}/fragmentomics/{sample_id}/`:

- `sample_features.json`
- `wps_bins.tsv` (if wps mode)
- `end_motifs.tsv` (if end_motifs mode)

See [docs/USAGE.md](docs/USAGE.md) and [configs/cfdna_fragmentomics.example.json](configs/cfdna_fragmentomics.example.json).
