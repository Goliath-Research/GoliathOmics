# GoliathExtractor migration (from MethylExtractor)

On **2026-10-30** the extraction product was renamed **MethylExtractor → GoliathExtractor** ([Goliath-Research/GoliathExtractor](https://github.com/Goliath-Research/GoliathExtractor)). Poetry package and CLI: `goliath-extractor`; Python import: `goliath_extractor`; Goliath product id: `goliathextractor` (no hyphen).

GoliathOmics keeps **one release** of backward compatibility for deployed workers, then removes legacy names.

## Environment variables (workers and CI)

Prefer the `GOLIATH_EXTRACTOR_*` names. If a new name is unset, scripts and `methyl_utils.goliath_extractor` still read the old name and log a **deprecation warning**:

| New (use this) | Legacy (fallback) |
|----------------|-------------------|
| `GOLIATH_EXTRACTOR_HOME` | `METHYL_EXTRACTOR_HOME` |
| `GOLIATH_EXTRACTOR_BIN` | `METHYL_EXTRACTOR_BIN` |
| `GOLIATH_EXTRACTOR_URL` | `METHYL_EXTRACTOR_URL` |
| `GOLIATH_EXTRACTOR_ROOT` | `METHYL_EXTRACTOR_ROOT` |
| `GOLIATH_EXTRACTOR_FEED` | `METHYL_EXTRACTOR_FEED` |
| `GOLIATH_EXTRACTOR_REPO` | `METHYL_EXTRACTOR_REPO` |
| `GOLIATH_EXTRACTOR_BUNDLE_SUITE` | `METHYL_EXTRACTOR_BUNDLE_SUITE` |
| `GOLIATH_EXTRACTOR_BUNDLE_DEBS` | `METHYL_EXTRACTOR_BUNDLE_DEBS` |
| `GOLIATH_EXTRACTOR_BUNDLE_LIBS` | `METHYL_EXTRACTOR_BUNDLE_LIBS` |
| `GOLIATH_EXTRACTOR_BUNDLE_LIB_DIR` | `METHYL_EXTRACTOR_BUNDLE_LIB_DIR` |
| `GOLIATH_EXTRACTOR_REQUIRE_PATCHELF` | `METHYL_EXTRACTOR_REQUIRE_PATCHELF` |
| `SKIP_GOLIATH_EXTRACTOR` (bootstrap) | `SKIP_METHYL_EXTRACTOR` |
| `GOLIATH_EXTRACTOR_ARCH_aarch64` / `GOLIATH_EXTRACTOR_ARCH_x86_64` in `scripts/platform_matrix.env` | `METHYL_EXTRACTOR_ARCH_*` |

`scripts/write_worker_env.sh` still writes **both** `GOLIATH_*` and `METHYL_*` lines to `worker.env` for this release so older supervisor snippets keep working.

## Script paths

New canonical scripts live under `scripts/*goliath_extractor*`. Thin wrappers at the old `scripts/*methyl_extractor*` paths exec or source the new scripts and print a deprecation message.

Examples:

- `scripts/goliath_extractor_local.sh` (was `methyl_extractor_local.sh`)
- `scripts/bundle_goliath_extractor_libs.sh`
- `scripts/download_goliath_extractor_artifacts.sh`
- `scripts/install_goliath_extractor_tarball.sh`
- `scripts/package_goliath_extractor.sh`
- `scripts/verify_goliath_extractor.sh`

Worker doc: `workers/docs/goliath_extractor.md`.

## Release / assemble CLI

- `scripts/assemble_release.sh --goliath-extractor-version` (legacy: `--methyl-extractor-version`)
- `--skip-goliath-extractor-download` (legacy: `--skip-methyl-extractor-download`)
- `manifest.json` uses `components.goliath_extractor` and artifact key `goliath_extractor` (legacy manifest keys may still appear in very old bundles).

GitHub Actions **GoliathOmics release assemble** workflow input: `goliathExtractorVersion` (was `methylExtractorVersion`).

## Deploy profiles and GitHub secrets

Rename operator secrets and deploy-target env entries to match the table above. Typical renames:

- GitHub Environment / org secrets used for assemble or worker bootstrap that were named `METHYL_EXTRACTOR_*` → `GOLIATH_EXTRACTOR_*`
- RunPod / worker profile env in `deploy/targets/*.json` (or provider-specific config) that set `METHYL_EXTRACTOR_HOME` / `METHYL_EXTRACTOR_BIN`

No change to **methylation science** names: `methyl_worker`, `methylutils`, `MethylDackel`, `methylgrapher`, `METHYL_LOCAL_TMP`, etc.

## What we did not rename

Historical material under `docs/research/` (except where it listed the product as a current dependency) and `docs/refresh-plan.md` were left unchanged.

Legacy **binary** name `MethylExtractor` and tarball prefix `methyl-extractor-linux-*` are still accepted when downloading or installing old GitHub Release assets.
