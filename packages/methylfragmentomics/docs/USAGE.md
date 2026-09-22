# methyl-fragmentomics usage

Run after external alignment (BAM available). Typical order:

1. Parabricks → BAM + `*.qc-metrics.tar`
2. `methyl-qc --project …` (alignment_qc; enable `fragmentomics` for insert-size cfDNA QC)
3. `methyl-fragmentomics --project …` (optional regional WPS + end motifs)
4. MethylDackel / HDF5 → centroid → detector → mapper → enricher (CIS-BP TF)

## Enable via profile/site actionConfig

Set fragmentomics and alignment QC under **profile or site `actionConfig`** (merged into instance `context_json`). The study manifest carries cohort paths and top-level `regulatory.primary_analyte` only.

```json
"actionConfig": {
  "alignment_qc": {
    "fragmentomics": { "enabled": true, "profile": "cfdna" },
    "auto_profile_from_analyte": true
  },
  "fragmentomics": {
    "enabled": true,
    "modes": ["wps", "end_motifs"],
    "max_reads_per_sample": 2000000
  },
  "validation": {
    "regulatory": {
      "primary_analyte": "cfdna",
      "sample_type": "Plasma cfDNA"
    }
  }
}
```

With `auto_profile_from_analyte`, alignment_qc enables cfDNA fragment-length guardrails when `primary_analyte` is `cfdna` even if `alignment_qc.fragmentomics` is omitted.

## Freeze / validation

`methyl-validation` biological readiness checks fragmentomics artifacts when `primary_analyte` is `cfdna`. Grok advisory payloads include summarized fragmentomics metrics.
