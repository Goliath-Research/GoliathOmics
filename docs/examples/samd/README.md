# SaMD example study manifests

Placeholder manifests illustrating `validation_partitions` for the SaMD profile ladder,
plus committed **application pack** examples (config overlays on the methylation process).
Sample IDs are fake — do not run as production studies.

## Shape stubs

| File | Shape |
|------|-------|
| `project_Healthy_vs_Disease_Example.json` | Binary healthy vs disease |
| `project_Healthy_vs_Disease_Stages_Example.json` | Healthy vs 3 stages |

## Application pack examples

| Directory | Kind | Guide |
|-----------|------|-------|
| [`alzheimer-cfdna/`](alzheimer-cfdna/README.md) | Disease application (Control → MCI → AD, cfDNA) | [Usage ch.21](../../usage/21-alzheimer-cfdna-pack.md) |
| [`plant-abiotic-stress/`](plant-abiotic-stress/README.md) | Trait application (Arabidopsis Control vs Drought) | [Usage ch.23](../../usage/23-plant-abiotic-stress-pack.md) |

Generic pattern and checklist: [Usage ch.24 Methylation application packs](../../usage/24-methylation-application-packs.md).
Operator SaMD ladder: [Usage ch.18](../../usage/18-samd-study-lifecycle.md). Scaffold: `methyl-study-init`.
After pivotal runs, copy an evidence package into `docs/regulatory/validation-evidence-index.md`.
