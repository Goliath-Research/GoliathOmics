# Prerequisites and Environment

## Purpose

Set up a checkout for package CLIs and for reading the operator guides. A production gateway and workers come from a release bundle, not from this checkout. See [Release](../operators/release.md).

## Required tools

- Python. `pyproject.toml` requires `>=3.10,<3.15`. The PR workflow and the release workflow use Python 3.12.
- A repo-local `.venv`
- Package CLIs installed from this repo (`methyl-centroid`, `methyl-validation`, `methyl-worker`, and the other scripts declared under `packages/*/pyproject.toml` and `workers/pyproject.toml`)

`methyl-gateway`, `methyl-workflow-run`, and `methyl-cfg` are installed with the methyl-gateway wheel built from GoliathApp. `scripts/build_gateway_wheel.sh` builds that wheel. They are not console scripts of this repository's science packages.

## Environment bootstrap

From the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
bash scripts/install_packages.sh --with-deps
```

`scripts/install_packages.sh` installs each entry in `scripts/packages.list` in editable mode, then `workers/` when that project is present. `--with-deps` installs declared dependencies. The script reinstalls `methylutils` and `methyldomain` editable at the end so path dependencies stay on this checkout.

GPU requirement files in the repo root are `requirements-gpu.txt` and `requirements-gpu-cuda12.txt`. The release build passes `--with-gpu-reqs` to `scripts/build_release.sh`. They are not installed by `install_packages.sh`.

## Verify CLI availability

```bash
source .venv/bin/activate
methyl-validation --help
methyl-centroid --help
methyl-worker --help
```

`methyl-study-start` appears after the gateway wheel is installed into the same environment. The implementation is `python workflow_engine/admin/study_start.py`.

## Runtime assumptions

- Activate `.venv` before every local CLI run.
- Use absolute paths in project configs where possible.
- Keep `samples_base_path` consistent across stages.
- Worker scratch is `METHYL_LOCAL_TMP` (default `$TMPDIR/methyl-pb` or `/tmp/methyl-pb`). `/work` is the shared mount.

## Docs site

```bash
python -m pip install -r requirements-docs.txt
mkdocs serve
```

The site is optional. CI builds it and does not publish it.

## Common failures and fixes

- **`command not found` for a science CLI**: activate `.venv` and rerun `scripts/install_packages.sh --with-deps`.
- **`command not found` for `methyl-gateway` or `methyl-workflow-run`**: those scripts come from the GoliathApp wheel, not from `install_packages.sh`.
- **module import errors**: reinstall editable packages from the repo root.
- **path mismatch between hosts**: set profile `actionConfig.validation.path_remap`.

## See also

- [README](../../README.md)
- [Release](../operators/release.md)
- [Dependency direction](../architecture/dependencies.md)
