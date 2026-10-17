# Worker providers

`deploy/workers/create_worker.sh --provider <name>` is the only way to create a
GoliathOmics worker. It owns everything that is the same on every cloud:

- option parsing (`--name`, `--template`, `--volume`, `--mount-path`,
  `--allow-nonstandard-path`, `--gpu`, `--gpu-count`, `--cloud`, `--wait`,
  `--dry-run`, `--yes`);
- picking the lowest free `goliath-worker-N` name and refusing a duplicate `--name`;
- the shared-storage policy: the shared volume must be mounted at `/work`
  (anything else needs `--allow-nonstandard-path`), and the request is checked
  before it is sent;
- the price display, `--dry-run` (print the request, create nothing) and the
  `--yes` gate (creating is billable);
- verifying after creation that the shared storage really is at `/work`. If not,
  it prints how to terminate the worker and exits 3. It never deletes anything;
- the `key=value` summary: `pod_id`, `name`, `status`, `mount`, `ssh`, `public_ip`.

Everything cloud-specific (credentials, API URLs, request and response shapes,
names of images/volumes/instance types) lives in one file per provider:
`providers/<name>.sh`.

## The interface

[`_interface.sh`](_interface.sh) is the abstract base. It declares every hook as
a stub that fails with `<hook> not implemented by provider <name>`.
`create_worker.sh` sources it first and then `providers/<name>.sh`, whose
functions override the stubs. `scripts/test_create_worker.sh` fails if a
provider file does not define every hook, or if generic code mentions a
provider, a URL or an API path.

| Hook | Must do |
| --- | --- |
| `provider_defaults` | Set `PROVIDER_DEFAULT_TEMPLATE`, `PROVIDER_DEFAULT_STORAGE`, `PROVIDER_DEFAULT_GPU`, `PROVIDER_DEFAULT_CLOUD`. |
| `provider_require_env` | Check CLIs and credentials, validate `OPT_*` (e.g. `--cloud`). No network. Never print secrets. |
| `provider_resolve_template` | Resolve `OPT_TEMPLATE` (name or id) to `TEMPLATE_ID`; die if missing or ambiguous. |
| `provider_resolve_storage` | Resolve `OPT_STORAGE` to `STORAGE_ID`, the shared filesystem every worker mounts at `/work`. |
| `provider_list_worker_names` | Print the names of existing workers/instances, one per line. |
| `provider_price` | Log the list price for `OPT_GPU` x `OPT_GPU_COUNT` (informational). |
| `provider_build_request FILE` | Write the create request (JSON) to `FILE`, attaching `STORAGE_ID` at `MOUNT_PATH`. |
| `provider_check_request FILE` | Return 0 only if `FILE` attaches `STORAGE_ID` at `MOUNT_PATH`. |
| `provider_request_target` | Print where the request goes (shown by `--dry-run`). |
| `provider_create FILE` | Create the worker (billable) and set `WORKER_ID`. |
| `provider_describe ID` | Read the worker; return non-zero if unreadable. Set `W_NAME`, `W_STATUS`, `W_SSH`, `W_PUBLIC_IP`, `W_MOUNTS` (lines of `storage_id:path` as the provider actually reports them) and `W_MOUNTS_RAW` (raw text for error messages). |
| `provider_wait_ready ID` | Poll `provider_describe` until SSH and a public IP are known or `WAIT_SECS` pass. |
| `provider_terminate_hint ID` | Print how a human terminates the worker (console path and CLI/API call). |

Inputs available to hooks: `PROVIDER`, `CW_TMP` (private scratch dir, removed on
exit), `WORKER_NAME`, `OPT_TEMPLATE`, `OPT_STORAGE`, `OPT_GPU`, `OPT_GPU_COUNT`,
`OPT_CLOUD`, `MOUNT_PATH`, `WAIT_SECS`, `POLL_SECS`, and the helpers `log` and
`die`. Keep provider-private helpers and variables prefixed with the provider
name (`_runpod_api`, `RUNPOD_RESP`).

## Adding a provider

1. Copy `runpod.sh` to `providers/<name>.sh` and replace every hook.
2. Pass secrets to CLIs/curl through files or env, never on the command line.
3. Make `provider_describe` report the mount **as the cloud sees it** (not what
   you asked for), so the generic verification is meaningful.
4. Add a mocked test next to `scripts/test_create_worker_pod.sh`, add the files
   to the shellcheck list in `.github/workflows/pr.yml`.

What each hook would map to (not implemented):

| Hook | Lambda.ai | Nebius | AWS | Azure | Google Cloud |
| --- | --- | --- | --- | --- | --- |
| template | instance type + image (`GET /api/v1/instance-types`) | boot disk image | AMI id / launch template | VM image / gallery image | image family / instance template |
| storage | persistent filesystem (`/api/v1/file-systems`), mounted at `/lambda/nfs/<name>`, so bind or symlink to `/work` in cloud-init | shared filesystem attached to the instance | EFS file system + mount target, mounted via user data | Azure Files share / NetApp volume | Filestore instance |
| list names | `GET /api/v1/instances` | `nebius compute instance list` | `ec2 describe-instances` tag `Name` | `az vm list` | `gcloud compute instances list` |
| price | instance type `price_cents_per_hour` | pricing API | Pricing API / spot price history | Retail Prices API | Cloud Billing catalog |
| create | `POST /api/v1/instance-operations/launch` | `nebius compute instance create` | `ec2 run-instances` | `az vm create` | `gcloud compute instances create` |
| describe / mounts | `GET /api/v1/instances/{id}` (`file_system_names`) | instance get | `describe-instances` + SSM check of `/work` | `az vm show` + run-command | `instances describe` + guest attribute |
| terminate hint | `POST /api/v1/instance-operations/terminate` | instance delete | `ec2 terminate-instances` | `az vm delete` | `gcloud compute instances delete` |

When the cloud API cannot report the mount path itself (most VMs), mount the
share at `/work` in cloud-init/user data and have `provider_describe` confirm it
(for example via SSH, SSM or a guest attribute) before reporting it in `W_MOUNTS`.
