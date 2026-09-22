# CI smoke fixture: plant abiotic stress pack

Minimal binary (Control vs Drought) multi-context (CG/CHG/CHH) methylation manifests
used to regression-test the plant abiotic stress application pack: binary comparison
resolution, `plant_tissue` analyte defaults (plant-safe QC, no fragmentomics), the
`plant-stress-core` enrichment preset, a lifecycle program without cell deconvolution,
crop site recipes (soybean / maize / wheat), and the offline `plant_traits` mapper prior.
Placeholder sample IDs only; single chromosome per smoke manifest (wheat uses `1A`).

| Manifest | Species |
|----------|---------|
| `configs/project_Control_vs_Drought_smoke.json` | Arabidopsis (TAIR10) |
| `configs/project_Control_vs_Drought_soybean_smoke.json` | Soybean Wm82 |
| `configs/project_Control_vs_Drought_maize_smoke.json` | Maize B73 |
| `configs/project_Control_vs_Drought_wheat_smoke.json` | Wheat IWGSC |

Consumed by [`workflow_engine/tests/test_plant_abiotic_stress_pack.py`](../../../tests/test_plant_abiotic_stress_pack.py).
The runnable/operator example (overlays, site pins, plant trait TSV, run instructions)
lives under [`docs/examples/samd/plant-abiotic-stress/`](../../../../docs/examples/samd/plant-abiotic-stress/README.md).
