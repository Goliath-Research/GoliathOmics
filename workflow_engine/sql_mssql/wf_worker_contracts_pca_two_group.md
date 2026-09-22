# PCa Two-Group Worker JSON Contracts

Workers poll `wf.sp_worker_request_task` with capability `methyl-centroid` or `methyl-detector`, receive resolved `input_json`, and submit results via `wf.sp_worker_submit_result`.

---

## Instance globals (`workflow_instance.context_json` → scope 0)

Seeded by `wf.wf_init_instance_scope_from_context` at `sp_start_workflow_instance`.

| Variable | Type | Example | Description |
|----------|------|---------|-------------|
| `projectPath` | string | `/home/ubuntu/Work/prostate-cancer/configs/project_PCa3.json` | Pipeline project JSON |
| `context` | string | `"CG"` | Methylation context |
| `centroid1Dir` | string | `/work/projects/prostate-cancer/PCa3/centroids/controls/healthy/all` | Control group centroid output |
| `centroid2Dir` | string | `/work/projects/prostate-cancer/PCa3/centroids/diseases/cancer/PCa_Low` | Disease group centroid output |
| `detectOutDir` | string | `/work/projects/prostate-cancer/PCa3/detections/all/PCa_Low` | Detection output for comparison |
| `group1Label` | string | `"group1"` | `methyl-centroid --group` for control |
| `group2Label` | string | `"group2"` | `methyl-centroid --group` for disease |

Example:

```json
{
  "projectPath": "/home/ubuntu/Work/prostate-cancer/configs/project_PCa3.json",
  "context": "CG",
  "centroid1Dir": "/work/projects/prostate-cancer/PCa3/centroids/controls/healthy/all",
  "centroid2Dir": "/work/projects/prostate-cancer/PCa3/centroids/diseases/cancer/PCa_Low",
  "detectOutDir": "/work/projects/prostate-cancer/PCa3/detections/all/PCa_Low",
  "group1Label": "group1",
  "group2Label": "group2"
}
```

---

## Action: `pca.centroid` (capability `methyl-centroid`)

### Input template (per chromosome node)

```json
{
  "tool": "MethylCentroid",
  "project": "${var.projectPath}",
  "group": "group1",
  "chromosome": "1",
  "context": "${var.context}",
  "outputDir": "${var.centroid1Dir}"
}
```

(`group` / `outputDir` differ for `centroid_g2_*` nodes.)

### Worker behavior

1. Parse `input_json`.
2. Build a single-chromosome batch config or `--step-override` limiting `chromosomes` to `[chromosome]`.
3. Run:

   ```bash
   source .venv/bin/activate
   methyl-centroid --project "<project>" --group <group> \
     --step-override /tmp/centroid_chr_override.json
   ```

   Override example:

   ```json
   { "chromosomes": ["1"], "contexts": ["CG"] }
   ```

4. Verify output: `{outputDir}/{chromosome}-{context}.h5`.

### Output JSON (success)

```json
{
  "ok": true,
  "h5": "/work/projects/prostate-cancer/PCa3/centroids/controls/healthy/all/1-CG.h5",
  "chromosome": "1",
  "context": "CG"
}
```

### Result code

| Code | Meaning |
|------|---------|
| `0` | Success |
| `< 0` | Failure (fails workflow instance) |

---

## Action: `pca.detector` (capability `methyl-detector`)

### Input template (per chromosome node)

```json
{
  "tool": "MethylDetector",
  "project": "${var.projectPath}",
  "chromosome": "1",
  "context": "${var.context}",
  "centroid1Dir": "${var.centroid1Dir}",
  "centroid2Dir": "${var.centroid2Dir}",
  "outputDir": "${var.detectOutDir}"
}
```

### Worker behavior

1. Parse `input_json`.
2. Write `step-override` JSON (detector has no `--chromosome` CLI flag):

   ```json
   {
     "chromosome": ["1"],
     "contexts": ["CG"],
     "centroid1_dir": "<centroid1Dir>",
     "centroid2_dir": "<centroid2Dir>",
     "output_dir": "<detectOutDir>"
   }
   ```

3. Run:

   ```bash
   methyl-detector --project "<project>" --step-override /tmp/detector_chr_override.json
   ```

4. Verify output: `{detectOutDir}/dmps-{chromosome}.csv` (or dual-export variants).

### Output JSON (success)

```json
{
  "ok": true,
  "dmpsCsv": "/work/projects/prostate-cancer/PCa3/detections/all/PCa_Low/dmps-1.csv",
  "chromosome": "1",
  "nDmps": 42
}
```

### Result code

Same as centroid: `0` success, negative failure.

---

## Optional output bindings

For downstream scope variables (after `wf_sql_scope_writepath_parity.sql`):

| Node pattern | var_name | source_kind | source_json_path |
|--------------|----------|-------------|------------------|
| `detect_*` | `lastDetectN` | `output_path` | `nDmps` |

---

## Dependency graph (per chromosome)

```text
centroid_g1_{chr}  ──┐
                     ├──► detect_{chr}
centroid_g2_{chr}  ──┘
```

Enforced by workflow tree: `SEQUENCE chr_{chr}` → `PARALLEL cent_{chr}` then `ACTION detect_{chr}`.
