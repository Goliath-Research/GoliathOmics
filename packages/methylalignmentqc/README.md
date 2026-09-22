# methylalignmentqc

This package is a QC and reporting component of the MethylPipeline monorepo.

It covers initial alignment/QC metrics extraction and normalization from upstream reports (including WGBS Parabricks metrics JSON inputs) into structured per-sample JSON outputs.

**V1 vs V2.1 JSON:** `methyl-qc` writes a slim **V2.1 guardrail summary** (`export_kind: guardrail_summary`). Picard histograms stay in the sample-dir Parabricks `{id}.json` / `{id}.qc-metrics.tar`. Use `methyl-qc-convert-v1-to-v2` to migrate older **V1** or fat **V2.0** exports (see [docs/USAGE.md](docs/USAGE.md)). Analysis reads `guardrails.details` only.

## High-Level Functionality
Please refer to the root [README.md](../../README.md) for the functional placement of this package in the broader MethylPipeline workflow.

## Documentation

The canonical mathematical and statistical reference is the theory chapters at [../../docs/theory/README.md](../../docs/theory/README.md). This package also keeps local docs for quick navigation:

- 📖 **[THEORY.md](docs/THEORY.md)**: Theoretical foundation, including high-quality formulas, statistical models, and references.
- ⚙️ **[IMPLEMENTATION.md](docs/IMPLEMENTATION.md)**: Implementation details, code design, architecture, and memory organization.
- 🚀 **[USAGE.md](docs/USAGE.md)**: Practical usage, CLI commands, Python API snippets, and configuration parameters.

Historical utility note:
- `methyl_alignment_qc/core/wgbs_parabricks_qc.py` is retained as a standalone WGBS Parabricks guardrail checker for initial metrics assessment workflows.

For deployment instructions on supported platforms (Linux/macOS) via virtual environments or Docker, please consult the global [DEPLOYMENT.md](../../docs/DEPLOYMENT.md).
