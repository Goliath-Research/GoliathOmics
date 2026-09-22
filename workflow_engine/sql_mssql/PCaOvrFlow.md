# PCaOvrFlow — OvR scale-up for project_PCa3.json

> **DEPRECATED.** This static SQL-seed workflow is superseded by **DomainProgram** definitions compiled and deployed via `scripts/deploy_workflow_definitions.sh`. See [`docs/reference/domain-program-language.md`](../../docs/reference/domain-program-language.md) and [`workflow_engine/domain/fixtures/`](../domain/fixtures/). Legacy seed: [`deprecated/wf_pca_ovr_seed.sql`](deprecated/wf_pca_ovr_seed.sql).

Milestone 2 static workflow: **parallel comparisons** (`control_vs_each_disease`), then **sequential** mapper → enricher → disease progression.

## Tree

```text
SEQUENCE pca_ovr
├─ PARALLEL by_comparison                    (PCa_Low ∥ PCa_High)
│  ├─ SEQUENCE cmp_PCa_Low
│  │  └─ PARALLEL by_chrom_PCa_Low           (24 chromosomes)
│  │     └─ SEQUENCE chr_PCa_Low_{chr}
│  │        ├─ PARALLEL cent_{label}_{chr}   → centroid_g1, centroid_g2
│  │        └─ ACTION detect_{label}_{chr}
│  └─ SEQUENCE cmp_PCa_High
│     └─ (same per-chromosome fan-out)
└─ SEQUENCE post_ovr                         (runs after both comparisons)
   ├─ ACTION mapper_all
   ├─ ACTION enricher_all
   └─ ACTION progression_all
```

**Dependency semantics**

- `PCa_Low` and `PCa_High` comparison subtrees run **concurrently** under `by_comparison`.
- Within each comparison, all 24 chromosomes run **concurrently** (same as milestone 1).
- Within each chromosome, detection waits for **both** centroids.
- `post_ovr` starts only after **both** comparison subtrees complete (mapper needs all `detections/all/*` DMP CSVs).

**Node count (approx.)**

| Component | Nodes |
|-----------|------:|
| Root + parallel/sequence shells | 5 |
| 2 comparisons × (cmp + by_chrom + 24×chr tree) | 2 × 122 = 244 |
| Post mapper/enricher/progression | 3 |
| **Total ACTION nodes** | **147** (72 per comparison + 3 post) |

## Deploy

```sql
-- once
:r wf_sp_delete_workflow_def.sql
:r wf_pca_ovr_seed.sql

-- rebuild after seed changes
EXEC wf.sp_delete_workflow_def @workflow_name = N'PCaOvrFlow';
:r wf_pca_ovr_seed.sql
```

## Instance `context_json`

Globals at scope 0 (override seed defaults at instance start):

```json
{
  "projectPath": "/work/projects/prostate-cancer/configs/project_PCa3.json",
  "context": "CG",
  "centroid1Dir": "/work/projects/prostate-cancer/PCa3/centroids/controls/healthy/all",
  "group1Label": "group1",
  "orderedComparisonLabels": ["PCa_Low", "PCa_High"]
}
```

Per-comparison paths (`centroid2Dir`, `detectOutDir`, `comparisonLabel`) are seeded as **`node_scope_default`** on each `cmp_{label}` node. Override at instance time by adding matching keys to `context_json` only if you change the generator defaults.

Paths follow `ProjectConfig` layout:

- Centroids: `{project_root}/centroids/controls/healthy/all` and `.../diseases/cancer/{stage}`
- Detections: `{project_root}/detections/all/{stage}` for `control_vs_each_disease`

## Run

```sql
INSERT INTO wf.workflow_instance (workflow_version_id, status, context_json)
SELECT TOP (1) wv.id, N'CREATED', CAST(N'{ ... }' AS json)
FROM wf.workflow_version AS wv
INNER JOIN wf.workflow_def AS wd ON wd.id = wv.workflow_def_id
WHERE wd.name = N'PCaOvrFlow'
ORDER BY wv.id DESC;

INSERT INTO wf.instance_cursor (workflow_instance_id, notes)
VALUES (SCOPE_IDENTITY(), N'PCaOvr production run');

EXEC wf.sp_start_workflow_instance @workflow_instance_id = SCOPE_IDENTITY();
-- workers poll sp_worker_request_task / submit sp_worker_submit_result
```

## vs milestone 1 (PCaTwoGroupFlow)

| | PCaTwoGroupFlow | PCaOvrFlow |
|--|-----------------|------------|
| Comparisons | 1 (e.g. PCa_Low) | 2 parallel (PCa_Low, PCa_High) |
| Post steps | none | mapper, enricher, progression |
| Matches `project_PCa3.json` | partial | `control_vs_each_disease` + progression block |

## Future: FOREACH (milestone 3)

For arbitrary group/comparison counts without static node explosion, see [wf_foreach_design.md](wf_foreach_design.md). This seed intentionally uses **build-time T-SQL loops** only — no engine changes required.

## Related

- Milestone 1: [wf_pca_two_group_seed.sql](wf_pca_two_group_seed.sql), [PCaTwoGroupFlow.md](PCaTwoGroupFlow.md)
- Worker contracts: [wf_worker_contracts_pca_ovr.md](wf_worker_contracts_pca_ovr.md)
- Capability check: [../CAPABILITY_CHECK.md](../CAPABILITY_CHECK.md)
