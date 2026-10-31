# Release, deploy, and worker enrollment

A GoliathOmics release is two GitHub Releases. The first is the MethylPipeline archive for one `v*` tag. The second is the assembled bundle `goliathomics-<releaseVersion>`, which adds GoliathExtractor and the GoliathAlign jammy host runtime. Deploy then promotes that bundle with a target profile or on the runner.

Release **2026.10.27** is this process: tag, assemble, deploy. The component versions (MethylPipeline tag, GoliathExtractor release, GoliathAlign jammy release) are the inputs of the assemble workflow. This repository does not pin those three versions in git.

## 1. Tag the MethylPipeline archive

Push a tag `v<semver>` on this repository. The workflow **GoliathOmics release** (`.github/workflows/release.yml`) runs `scripts/build_release.sh --version <semver> --with-gpu-reqs`.

The archive contains:

- First-party wheels from `scripts/packages.list`, including the `methyl-gateway` wheel built from GoliathApp `main` by `scripts/build_gateway_wheel.sh`
- `goliathapp-sql/` (the GoliathApp `sql_pg` scripts, `manifest.sha256`, `source.json`)
- `runtime-bundle/` (schemas, scripts, deploy files, domain fixtures)
- Third-party wheels, the venv bootstrap lock, and `system-deps/` derived from `deploy/targets/*.json`

The job checks that the gateway wheel exposes the `methyl-gateway` console script and that `goliathapp-sql/manifest.sha256` matches. It publishes GitHub Release `v<semver>` with asset `methyl-pipeline-release-<semver>.tar.gz`.

```bash
git tag v2026.10.27
git push origin v2026.10.27
```

Use a MethylPipeline version the archive should carry. The assembled product version in the next step can be the same semver. They are separate inputs.

## 2. Assemble the product bundle

Run **GoliathOmics release assemble** (`.github/workflows/release-assemble.yml`) with `workflow_dispatch`:

| Input | Role |
|-------|------|
| `releaseVersion` | SemVer of the product bundle. For this release, `2026.10.27`. |
| `methylPipelineVersion` | Tag of step 1 without the leading `v` |
| `methylExtractorVersion` | GoliathExtractor GitHub Release version |
| `goliathAlignVersion` | GoliathAlign jammy host-runtime GitHub Release version |

`scripts/assemble_release.sh` copies the MethylPipeline tree, downloads the GoliathExtractor and GoliathAlign tarballs, bundles GoliathExtractor's `libhts` with `patchelf`, checks `goliathapp-sql/manifest.sha256`, and writes `manifest.json` with `components.methyl_pipeline`, `components.methyl_extractor`, and `components.goliath_align`.

The job publishes GitHub Release `goliathomics-<releaseVersion>` with asset `goliathomics-release-<releaseVersion>.tar.gz`. The tag is the workflow's checkout, so `deploy/runpod/goliath-worker-bootstrap.sh`, `goliath-worker-start.sh`, `keepalive.sh`, `scripts/start_shared_worker.sh`, `scripts/check_local_scratch.sh`, `scripts/detect_platform.sh`, and `scripts/platform_matrix.env` must exist on that commit. The RunPod template reads those paths from the tag.

Local equivalent, after the MethylPipeline archive is unpacked into `mp-release/`:

```bash
bash scripts/assemble_release.sh \
  --release-version 2026.10.27 \
  --methyl-pipeline-version 2026.10.27 \
  --goliath-extractor-version <goliath-extractor-release> \
  --goliath-align-version <goliath-align-jammy-release> \
  --methyl-pipeline-dir mp-release \
  --output goliathomics-release
```

## 3. Deploy

Run **GoliathOmics release deploy** (`.github/workflows/release-deploy.yml`) in the GitHub environment `goliath-test`.

| Input | Default | Role |
|-------|---------|------|
| `releaseVersion` | required | Must match the assemble tag, without the `goliathomics-` prefix |
| `target` | empty | `deploy/targets/<name>.json`. Empty promotes on the runner. |
| `goliathRoot` | `/work/goliath` | Shared root for the runner-local promote |
| `pullParabricks` | false | Runner-local aarch64 promote pulls Clara when true. Profile deploys use the profile's `runtime.pull_parabricks`. |
| `runE2eVerify` | false | Runner-local path also runs `verify_e2e_node.sh` |
| `forceSchema` | false | Re-apply GoliathApp SQL even when `wf.schema_release` already records this release |

The job downloads `goliathomics-<releaseVersion>`. It reports Rollouts service `goliathomics-runtime` for the whole job. When `target` is set, the environment name is the profile's `rollouts_environment`. When `target` is empty, or the profile has no usable name, the report uses `production-work`. A failed report does not fail the job.

### Target profile

`scripts/deploy_target.sh --target <name> --component all` runs schema, then gateway, then runtime, then workers. Profiles and the field list are in [Deploy targets](../deploy-targets.md). `deploy/targets/goliath-test.json` is the committed test profile. `goliath-test.example.json` is the shape example. The driver refuses to apply an `*.example.json` profile unless `--dry-run` is set and the real file is absent.

Secrets in the profile are `secret:NAME` references. The workflow maps these GitHub environment secrets: `CURSOR_API_KEY`, `NEON_TEST_PASSWORD`, `GOLIATH_GATEWAY_DB_PASSWORD`, `GOLIATH_PORTAL_DB_PASSWORD`, `GOLIATH_DEPLOY_SSH_KEY`, `GOLIATH_GATEWAY_KNOWN_HOSTS`, `B2_ACCESS_KEY_ID`, `B2_SECRET_ACCESS_KEY`, `LONGTERM_ACCESS_KEY_ID`, `LONGTERM_SECRET_ACCESS_KEY`. When `target` is set, `GOLIATH_GATEWAY_KNOWN_HOSTS` is written to `~/.ssh/known_hosts` before SSH. The job does not run `ssh-keyscan` and does not disable `StrictHostKeyChecking`. An empty known-hosts secret fails the job.

Schema apply runs `scripts/apply_goliathapp_schema.sh` against the release's `goliathapp-sql/` once per release, recorded in `wf.schema_release`, then `scripts/apply_omics_schema.sh` for Omics domain SQL. When the profile sets gateway or portal role passwords, `scripts/apply_gateway_db_roles.sh` runs next. Catalog sync seeds the merged ActionSpec catalog with `workflow_engine/sql_pg/seed_action_catalog.py --use-db` before DomainProgram deploy. Legacy databases upgrading to organization tenancy run `workflow_engine/sql_pg/migrations/20261023_semantic_p1b_organization_cutover.sql` once before the first P1b apply. See [Semantic layer integration](../architecture/semantic-layer.md). `--force-schema` / `forceSchema` applies GoliathApp SQL again. Pods do not run SQL.

Gateway install copies a subset wheel to the host and runs `install_gateway_systemd.sh` and `setup_gateway_nginx.sh`. The unit name is `methyl-gateway.service`. nginx proxies public `/v1/` to `127.0.0.1:8080`.

Workers with `self_provision: true` are not installed over SSH. The driver prints the pool and returns success. The pool boots itself and enrolls, below. SSH workers receive `worker.env` and `install_worker_systemd.sh`.

Runtime on an SSH host runs `scripts/promote_release.sh`. A profile whose workers are all self-provisioned skips that SSH promote. Those workers boot from `/work/goliath/current` or from the GitHub release bundle.

### Runner-local promote

When `target` is empty, the job rsyncs the bundle to `<goliathRoot>/releases/<releaseVersion>/` and runs `promote_release.sh` twice: once for `aarch64` (with `--pull-parabricks` only when `pullParabricks` is true) and once for `amd64` with `--skip-docker-pull`. Promote installs GoliathExtractor on local disk, installs the worker venv on local disk, optionally pulls Clara, flips `current`, and writes `worker.env`. It then runs `verify_setup.sh --runtime-bundle`.

```bash
bash scripts/promote_release.sh \
  --root /work/goliath \
  --release /work/goliath/releases/2026.10.27 \
  --arch amd64 \
  --skip-docker-pull
```

## Worker enrollment

Enrollment is `POST {WORKER_API_BASE}/workers/enroll` on the GoliathApp gateway. `WORKER_API_BASE` ends in `/v1` (the goliath-test profile sets `https://<gateway>/v1`). The client in `workers/methyl_worker/client.py` sends:

```json
{
  "cluster_key": "goliath",
  "external_worker_key": "<hostname -s>",
  "capabilities": ["..."]
}
```

`arc_resource_id` is sent when `ARC_RESOURCE_ID` or `/etc/methyl/arc.env` is set. A 403 whose body says the portal IP or key is not preregistered fails closed. There is no SQL fallback inside the worker.

The operator preregisters the worker IP and key in the database before enroll succeeds:

```sql
SELECT * FROM portal.sp_upsert_worker_enrollment('<cluster>', '<public_ip>', '<external_worker_key>');
```

`scripts/provision_worker_node.sh` prints that statement. It does not insert the row. `--register-worker` exits 2. `--enroll-worker` runs `python -m methyl_worker enroll` and retries while the gateway returns the preregistration 403, bounded by `GOLIATH_ENROLL_MAX_WAIT_SECS`.

```bash
python -m methyl_worker enroll \
  --api-base "$WORKER_API_BASE" \
  --cluster goliath \
  --key "$(hostname -s)"
```

The CLI writes `WORKER_ID` and `WORKER_TOKEN` to `/etc/methyl/worker-token` (mode 0600) unless `--token-file` is set. `WORKER_ID` is the numeric id from the gateway. Do not append those two variables to the shared `/work/goliath/env/worker.env`. `scripts/write_worker_env.sh` writes the shared file for non-secret runtime settings and leaves the credential lines commented. systemd loads `worker.env` and then `/etc/methyl/worker-token`, so the token file wins on that machine.

A RunPod pool (`self_provision: true`) starts from `deploy/runpod/goliath-worker-start.sh`, which runs `scripts/start_shared_worker.sh`, which runs `provision_worker_node.sh --join-mode join --enroll-worker`. Boot checks local scratch before it installs GoliathExtractor, GoliathAlign, and the venv. Details of the pod template are in [deploy/runpod/README.md](../../deploy/runpod/README.md).

After enroll, the unit runs `methyl-worker` poll against the same API base (`/workers/tasks/request` and `/workers/tasks/{id}/submit`).

## Process packs on a new database

After schema apply, load Omics catalog rows:

```bash
export GOLIATH_RELEASE_SRC=/work/goliath/releases/2026.10.27
./scripts/deploy_process_pack_catalog.sh --backend postgres --sync
```

PostgreSQL apply uses GoliathApp SQL from `GOLIATHAPP_SQL_DIR` or from `$GOLIATH_RELEASE_SRC/goliathapp-sql/sql_pg` when those files are present, then applies this repository's `workflow_engine/sql_pg/cfg_analyte_catalog.sql`. `--sync` runs `scripts/sync_cfg_profiles_and_action_catalog.py --backend postgres`, which upserts `cfg.analyte`, `cfg.pipeline_profile`, and `cfg.assay_procedure` from `workflow_engine/domain/`.

Pack layout is in [Process packs](process-packs.md).

## Related

- [Deploy targets](../deploy-targets.md)
- [Rollouts service ids](../rollouts.md)
- [Start a SamplePrep instance](start-sample-prep.md)
