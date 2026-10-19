#!/usr/bin/env bash
# Variables are shared between create_worker.sh and the sourced provider hooks.
# shellcheck disable=SC2034
# Abstract provider interface for deploy/workers/create_worker.sh.
#
# Sourced by create_worker.sh BEFORE providers/<name>.sh. Every hook below is a
# stub that fails with "not implemented by provider <name>"; a provider file
# overrides each one (like overriding a virtual method). create_worker.sh never
# talks to a cloud itself, it only calls these hooks.
#
# Inputs set by create_worker.sh before the hooks run (read-only for providers):
#   PROVIDER          provider name (--provider)
#   CW_TMP            private (0700) scratch directory, removed on exit
#   WORKER_NAME       worker name (set before provider_build_request)
#   OPT_TEMPLATE      --template value (or the provider default)
#   OPT_IMAGE         --image value (optional container image override)
#   OPT_REGISTRY_AUTH --registry-auth value (optional private registry credential id)
#   OPT_STORAGE       --volume value (or the provider default)
#   MOUNT_PATH        shared-storage mount path (normally /work)
#   OPT_GPU           --gpu value (or the provider default)
#   OPT_GPU_COUNT     --gpu-count value (positive integer)
#   OPT_CLOUD         --cloud value (provider-defined tier; may be empty)
#   WAIT_SECS POLL_SECS  readiness wait budget and poll interval
# Helpers available to providers: log MSG, die MSG.
#
# Hooks (outputs are shell variables unless noted):
#   provider_defaults           set PROVIDER_DEFAULT_TEMPLATE, PROVIDER_DEFAULT_STORAGE,
#                               PROVIDER_DEFAULT_GPU, PROVIDER_DEFAULT_CLOUD (may be empty)
#   provider_require_env        check tools/credentials and validate OPT_*; no network.
#                               Must never print secrets.
#   provider_resolve_template   resolve OPT_TEMPLATE -> TEMPLATE_ID (die if missing/ambiguous)
#   provider_resolve_storage    resolve OPT_STORAGE  -> STORAGE_ID  (die if missing/ambiguous)
#   provider_list_worker_names  print existing worker/instance names, one per line
#   provider_price              log the list price (informational; may warn)
#   provider_build_request FILE write the create request to FILE; it MUST attach
#                               STORAGE_ID at MOUNT_PATH
#   provider_check_request FILE return 0 only if FILE attaches STORAGE_ID at MOUNT_PATH
#   provider_request_target     print a one-line description of where the request goes
#   provider_create FILE        create the worker (billable); set WORKER_ID
#   provider_describe ID        read the worker; return non-zero if unreadable. Set the
#                               normalized fields:
#                                 W_NAME W_STATUS W_SSH W_PUBLIC_IP
#                                 W_MOUNTS   newline-separated "storage_id:path" entries
#                                 W_MOUNTS_RAW  provider's raw mount description (for errors)
#   provider_wait_ready ID      poll (provider_describe) until SSH and public IP are known
#                               or WAIT_SECS elapse; leaves W_* current. Never fails hard.
#   provider_terminate_hint ID  print (stdout) how a human terminates the worker. The
#                               generic code never deletes anything itself.

_provider_not_implemented() {
  die "$1 not implemented by provider ${PROVIDER:-?}"
}

provider_defaults()          { _provider_not_implemented provider_defaults; }
provider_require_env()       { _provider_not_implemented provider_require_env; }
provider_resolve_template()  { _provider_not_implemented provider_resolve_template; }
provider_resolve_storage()   { _provider_not_implemented provider_resolve_storage; }
provider_list_worker_names() { _provider_not_implemented provider_list_worker_names; }
provider_price()             { _provider_not_implemented provider_price; }
provider_build_request()     { _provider_not_implemented provider_build_request; }
provider_check_request()     { _provider_not_implemented provider_check_request; }
provider_request_target()    { _provider_not_implemented provider_request_target; }
provider_create()            { _provider_not_implemented provider_create; }
provider_describe()          { _provider_not_implemented provider_describe; }
provider_wait_ready()        { _provider_not_implemented provider_wait_ready; }
provider_terminate_hint()    { _provider_not_implemented provider_terminate_hint; }

PROVIDER_HOOKS=(provider_defaults provider_require_env provider_resolve_template
  provider_resolve_storage provider_list_worker_names provider_price
  provider_build_request provider_check_request provider_request_target
  provider_create provider_describe provider_wait_ready provider_terminate_hint)
