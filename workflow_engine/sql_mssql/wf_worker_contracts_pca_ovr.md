# PCa OvR Worker JSON Contracts

> **DEPRECATED.** **PCaOvrFlow** static seed is legacy. Use DomainProgram workflows and [`workflow_engine/contract/sample_prep_capabilities.md`](../contract/sample_prep_capabilities.md) for current contracts. Legacy seed: [`deprecated/wf_pca_ovr_seed.sql`](deprecated/wf_pca_ovr_seed.sql).

Workers poll `wf.sp_worker_request_task` by capability and submit via `wf.sp_worker_submit_result`.

Workflow definition (legacy): **PCaOvrFlow** ([deprecated/wf_pca_ovr_seed.sql](deprecated/wf_pca_ovr_seed.sql)).

---

## Instance globals (scope 0)

| Variable | Example | Description |
|----------|---------|-------------|
| `projectPath` | `.../project_PCa3.json` | Pipeline project JSON |
| `context` | `"CG"` | Methylation context |
| `centroid1Dir` | `.../centroids/controls/healthy/all` | Shared control centroids |
| `group1Label` | `"group1"` | Centroid CLI group for control |
| `orderedComparisonLabels` | `["PCa_Low","PCa_High"]` | Progression stage order |

## Per-comparison scope (`cmp_{label}` node defaults)

| Variable | PCa_Low example |
|----------|-----------------|
| `centroid2Dir` | `.../centroids/diseases/cancer/PCa_Low` |
| `detectOutDir` | `.../detections/all/PCa_Low` |
| `group2Label` | `"group2"` |
| `comparisonLabel` | `"PCa_Low"` |

---

## Comparison phase (parallel)

Same contracts as [wf_worker_contracts_pca_two_group.md](wf_worker_contracts_pca_two_group.md), with an extra `comparison` field for worker logging:

### Centroid (`methyl-centroid`)

```json
{
  "tool": "MethylCentroid",
  "project": "${var.projectPath}",
  "group": "group1",
  "chromosome": "1",
  "context": "CG",
  "comparison": "PCa_Low",
  "outputDir": "/work/.../centroids/controls/healthy/all"
}
```

Use `--step-override` to limit `chromosomes` / `contexts` to the single task chromosome.

### Detector (`methyl-detector`)

```json
{
  "tool": "MethylDetector",
  "project": "${var.projectPath}",
  "chromosome": "1",
  "context": "CG",
  "comparison": "PCa_Low",
  "centroid1Dir": "...",
  "centroid2Dir": "...",
  "outputDir": ".../detections/all/PCa_Low"
}
```

`step-override` must include `"chromosome": ["1"]` (no `--chromosome` CLI flag).

---

## Post-comparison phase (sequential, after all comparisons)

Runs only when both `cmp_PCa_Low` and `cmp_PCa_High` subtrees are `SUCCEEDED`.

### Mapper (`methyl-mapper`)

```json
{"tool":"MethylMapper","project":"/path/to/project_PCa3.json"}
```

Worker resolves `detections/*/*/dmps-*.csv` from the project layout and writes mapper outputs per comparison directory.

### Enricher (`methyl-enricher`)

```json
{"tool":"MethylEnricher","project":"/path/to/project_PCa3.json"}
```

Runs after mapper; consumes mapper gene lists per comparison.

### Disease progression (`methyl-disease-progression`)

```json
{
  "tool": "MethylDiseaseProgression",
  "project": "/path/to/project_PCa3.json",
  "orderedComparisonLabels": ["PCa_Low", "PCa_High"]
}
```

Maps to:

```bash
methyl-disease-progression --project <p> \
  --ordered-comparison-labels PCa_Low,PCa_High
```

Uses mapper + enricher outputs across stages (see `step_config.progression` in project JSON).

---

## Task ordering summary

```text
[PCa_Low: 48 centroids + 24 detects]  ──┐
                                         ├──► mapper → enricher → progression
[PCa_High: 48 centroids + 24 detects] ──┘
```

Within each comparison branch, ordering matches milestone 1 (centroids before detect per chromosome).
