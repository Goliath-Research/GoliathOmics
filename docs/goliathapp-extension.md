# GoliathApp extension (GoliathOmics operators)

GoliathOmics specializes the neutral GoliathApp platform. During phase **D** decoupling, runtime env vars and paths are renamed in the platform first; this repository accepts **both** names for one release cycle.

## Environment variables (preferred → legacy)

| Preferred (platform) | Legacy (still read) | Used for |
| -------------------- | ------------------- | -------- |
| `GOLIATH_DATABASE_URL` | `METHYLPIPELINE_DB` | Full PostgreSQL URL override (gateway, admin CLI, schema scripts) |
| `WF_DATABASE_URL` | (same as above, second preferred key) | Same as `GOLIATH_DATABASE_URL` |
| `GOLIATH_API_BASE` | `METHYL_API_BASE` | Gateway REST base URL on workers (`WORKER_API_BASE` still wins when set) |
| `GOLIATH_WORK_ROOT` | `METHYL_WORK_ROOT` | Shared `/work` mount root |
| `GOLIATH_ARC_ENV` | `METHYL_ARC_ENV` | Azure Arc attestation env file (`ARC_RESOURCE_ID`) |
| `GOLIATH_CFG_STORE` | `METHYL_CFG_STORE` | Optional cfg store directory under work root |
| `GOLIATH_REQUIRE_MOJO_LINEAR` | `METHYL_REQUIRE_MOJO_LINEAR` | Reference-selection guard for Mojo linear packs |

Worker implementation: [`workers/methyl_worker/platform_env.py`](../workers/methyl_worker/platform_env.py) (paired D6).

## Site manifest paths

| Preferred | Legacy |
| --------- | ------ |
| `/work/site/site.json` | `/work/site/methyl_site.json` |

Templates: GoliathApp `scripts/default_site.json` (with legacy copy one release).

## Deploy profiles

Omics-owned distributed targets live under [`deploy/targets/`](../deploy/targets/). The **`goliath-test`** profile (`deploy/targets/goliath-test.json`) is documented in [Deploy targets](../docs/deploy-targets.md#goliath-test). Platform deploy bundles no longer carry omics-target narrative (GoliathApp D7).

## Workflow contracts and profiles

Capability markdown, domain types, DomainProgram fixtures, and pipeline profile JSON are owned in this repository:

- [`workflow_engine/contract/`](../workflow_engine/contract/)
- [`workflow_engine/domain/profiles/`](../workflow_engine/domain/profiles/)
- [`workflow_engine/domain/pipeline_profiles.py`](../workflow_engine/domain/pipeline_profiles.py)

Paired plan: [GoliathApp `docs/decoupling-plan.md`](https://github.com/Goliath-Research/GoliathApp/blob/main/docs/decoupling-plan.md) phases D5–D7.

## Production renames (D8 — approved)

Gateway install root, systemd units, and nginx site templates use **`goliath-*`** names (`/opt/goliath-gateway`, `goliath-gateway.service`, `deploy/nginx/goliath-gateway.conf`). Installers symlink legacy **`methyl-*`** unit names and accept legacy console scripts for **one release**.

The Neon database name **`GoliathOmics`** on omics test/prod is unchanged. Legacy `methylpipeline` host/db names on older Azure stacks are unrelated to this cutover.

Operator cutover: redeploy gateway with `scripts/deploy_target.sh --target goliath-test --component gateway` (or `scripts/provision_gateway_node.sh` on the host). Existing TLS under `/etc/ssl/methyl-gateway` is reused automatically until reissued under `/etc/ssl/goliath-gateway`.
