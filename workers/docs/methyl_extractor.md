# GoliathExtractor on sample-prep workers

Sample prep extraction (`sample.methyl_extract`) runs the **native GoliathExtractor binary** on the **linear / stock pangenome** path. GoliathExtractor is the focused MethylDackel fork in the sibling repository (formerly MethylExtractor). For `pangenome_wgbs`, SamplePrep uses `sample.methylgrapher_wgbs_extract` (GoliathAlign MethylCall/MergeCpG) instead — see [`workers/docker/methylgrapher/README.md`](../docker/methylgrapher/README.md). Implementation: [`methyl_worker/extract_runner.py`](../methyl_worker/extract_runner.py).

## Provisioning

GoliathExtractor is maintained in [GoliathExtractor](https://github.com/Goliath-Research/GoliathExtractor). On each worker machine, build that checkout and install the binary (production workers extract the release tarball to local disk at `/opt/goliath/goliath-extractor-<arch>/`):

```bash
# From a GoliathExtractor checkout (sibling of GoliathOmics)
make && make install
```

Production workers use the binary under `GOLIATH_EXTRACTOR_HOME` (legacy alias `METHYL_EXTRACTOR_HOME`; default `/opt/goliath/goliath-extractor-<arch>/`, extracted on every boot by `scripts/methyl_extractor_local.sh`; the shared `/work` volume only holds the tarball because it cannot run binaries). `make install` also copies `goliath-extractor` to `/usr/local/bin/` for a machine image. That install is provisioning, not per-task JSON.

Shared libraries: the binary needs `libhts.so.3` (htslib). Release tarballs carry it in `lib/` (with `libdeflate.so.0` and `libhtscodecs.so.2`), added at assemble time by `scripts/bundle_methyl_extractor_libs.sh` because older release layouts did not ship it; the binary's RUNPATH is `$ORIGIN/../lib`, so it needs no `LD_LIBRARY_PATH`. `methyl_extractor_local.sh` refuses a binary that does not run without one, and nothing (its `env.sh`, `worker.env`) puts `<home>/lib` on `LD_LIBRARY_PATH`: those Ubuntu 22.04 libraries (libcurl, libssl, libhts 1.13, ...) would shadow the host's for every other program, which broke `/usr/bin/curl` (`undefined symbol: curl_global_trace`). If `env -u LD_LIBRARY_PATH ldd $GOLIATH_EXTRACTOR_BIN` shows `not found`, re-run `scripts/bundle_methyl_extractor_libs.sh --tarball <tarball> --require-patchelf` and refresh the manifest sha256.

Release tarballs on shared storage:

```text
/work/goliath/releases/<version>/goliath-extractor-linux-{amd64,aarch64}.tar.gz
```

Docs: [goliath-research.github.io/GoliathExtractor/](https://goliath-research.github.io/GoliathExtractor/)

## Smoke test

```bash
./scripts/verify_methyl_extractor.sh
```

Optional dev env: `GOLIATH_EXTRACTOR_BIN` (falls back to `METHYL_EXTRACTOR_BIN`) if the binary is not on default PATH.

## Artifacts

| Artifact | Path under `sampleDir` | Notes |
|----------|------------------------|-------|
| Per-chromosome HDF5 | `{chrom}-{CG\|CHG\|CHH}.h5` | Zstd-compressed methylation matrices |
| Extraction manifest | `{sampleId}.extraction_manifest.json` | **Preserve-or-synthesize:** keep a complete native `goliath_extractor.extraction_manifest` (legacy `methylextractor.extraction_manifest` still accepted; including `read_filtering`); synthesize stubs/incomplete files from sidecars; overlay `h5_files` / `pattern_files` / provenance when missing |

When `read_level` is enabled (and the installed binary supports `--read-level` / `--tile-size`), GoliathExtractor also writes `{chrom}-{ctx}.patterns.h5` files. Schema:

```text
goliath_extractor.*
```

(see the GoliathExtractor repo).

GoliathExtractor writes `{sampleId}.timing.json` next to the extraction manifest (phase elapsed-ms).
