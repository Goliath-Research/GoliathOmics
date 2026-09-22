# MethylAlignmentQC Theoretical Foundation

The canonical mathematical and statistical reference for this package is the theory chapter [`docs/theory/chapters/09-methylalignmentqc.md`](../../../docs/theory/chapters/09-methylalignmentqc.md).

## Scope

`methylalignmentqc` performs initial extraction/parsing and normalization of alignment QC metrics produced by external tools such as Picard and Parabricks.

## Method Status

- **Deterministic**: metrics parsing, schema normalization, JSON export.
- **Heuristic**: progress and ETA extraction from subprocess logs.
- **External-tool-backed**: the upstream QC metrics themselves come from external alignment software.

## Key Code Paths

- `methyl_alignment_qc/core/parser.py`
- `methyl_alignment_qc/core/writer.py`
- `methyl_alignment_qc/cli/main.py`
- `methyl_alignment_qc/utils/monitor.py`
- `methyl_alignment_qc/core/wgbs_parabricks_qc.py` (standalone WGBS Parabricks guardrail check utility)
