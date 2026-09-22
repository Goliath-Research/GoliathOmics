# Prerequisites and Environment

## Purpose

Set up a reproducible execution environment for all package CLIs and workflow stages.

## Required tools

- Python environment in repo-local `.venv`
- Installed package CLIs (`methyl-*`)
- Optional: Chromium/Playwright for PDF export (`make docs-pdf`)

## Environment bootstrap

From repo root (canonical):

```bash
bash scripts/setup_host.sh --system-deps --with-deps
source .venv/bin/activate
```

Or manual venv:

```bash
python3.14 -m venv .venv
source .venv/bin/activate
bash scripts/install_all.sh
```

Optional dependency sets:

```bash
bash scripts/install_all.sh --pipeline-reqs
bash scripts/install_all.sh --gpu-reqs
```

## Verify CLI availability

```bash
source .venv/bin/activate
methyl-workflow-run --help
methyl-validation --help
methyl-centroid --help
methyl-detector --help
methyl-mapper --help
methyl-enricher --help
methyl-predictor --help
```

## Runtime assumptions

- CPU host development supports Python 3.10-3.14; Python 3.14 is preferred.
- RAPIDS GPU conda/Docker environments remain on Python 3.12, and the bundled
  `mojo-align` runtime remains on Python 3.13.
- Activate `.venv` before every run.
- Use absolute paths in project configs where possible.
- Keep `samples_base_path` consistent across stages.

## Success checks

- `.venv` activates without import errors.
- `methyl-workflow-run --help` and `methyl-validation --help` print options.
- Project JSON path resolves on the execution host.

## Common failures and fixes

- **`command not found`**: activate `.venv` and reinstall with `scripts/install_all.sh`.
- **module import errors**: reinstall editable packages from repo root.
- **path mismatch between hosts**: set profile `actionConfig.validation.path_remap` or `--path-remap OLD=NEW`.

## See also

- `README.md`
- `docs/DEPLOYMENT.md`
- Interactive doc hubs: [`docs/canvas/`](../canvas/README.md) — run `bash scripts/sync_cursor_canvases.sh` after clone to open canvases in Cursor
