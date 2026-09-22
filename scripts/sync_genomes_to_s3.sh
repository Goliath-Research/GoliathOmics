#!/usr/bin/env bash
# Sync /work/genomes <-> myQNAPcloud S3 (linear / annotation / pangenome).
#
# myQNAPcloud is S3-compatible object storage — use aws s3 sync (not rsync).
# Credentials must come from the environment; never pass keys on the CLI or
# commit them to git.
#
# Canonical tree under genomes/:
#   linear/GRCh38/ensembl-116/       # default site pin (114 remains published)
#   linear/GRCh38/ensembl-114/       # historical BAMs
#   annotation/gencode/v50/          # default site pin
#   annotation/gencode/v49/
#   rna/GRCh38/star/ensembl-116/     # STAR (Clara rna_fq2bam); out of band for site roles
#   rna/GRCh38/kallisto/             # kallisto + tx2gene (GENCODE v50)
#   pangenome/GRCh38/d9/1.70/       # stock HPRC Giraffe indexes
#   pangenome/GRCh38/d9-bs/1.70/    # methylGrapher C2T+G2A BS bundle
#   pangenome/canary/gse261315/...  # public HPRC/methylGrapher WGBS canary FASTQs
#
# Operator usage (upload local → QNAP):
#   export AWS_ACCESS_KEY_ID=...
#   export AWS_SECRET_ACCESS_KEY=...
#   scripts/sync_genomes_to_s3.sh --dry-run
#   scripts/sync_genomes_to_s3.sh
#   scripts/sync_genomes_to_s3.sh --only pangenome
#   scripts/sync_genomes_to_s3.sh --only pangenome/GRCh38/d9-bs/1.70
#
# Operator usage (download QNAP → local, fill missing files):
#   scripts/sync_genomes_to_s3.sh --download --dry-run
#   scripts/sync_genomes_to_s3.sh --download
#   scripts/sync_genomes_to_s3.sh --download --only pangenome/GRCh38/d9-bs/1.70
#
# Optional env overrides:
#   GENOMES_SRC=/work/genomes
#   S3_ENDPOINT_URL=https://s3.us-east-1.myqnapcloud.io
#   S3_BUCKET=goliath
#   S3_PREFIX=genomes
#   AWS_DEFAULT_REGION=us-east-1
#   AWS_REQUEST_CHECKSUM_CALCULATION=when_required   # if QNAP rejects newer checksums
#
# Verify after sync:
#   aws s3 ls s3://goliath/genomes/ --endpoint-url https://s3.us-east-1.myqnapcloud.io
#   aws s3 ls s3://goliath/genomes/linear/GRCh38/ensembl-116/ \
#     --endpoint-url https://s3.us-east-1.myqnapcloud.io
#   aws s3 ls s3://goliath/genomes/annotation/gencode/v50/ \
#     --endpoint-url https://s3.us-east-1.myqnapcloud.io
#   aws s3 ls s3://goliath/genomes/rna/GRCh38/ \
#     --endpoint-url https://s3.us-east-1.myqnapcloud.io
#   aws s3 ls s3://goliath/genomes/pangenome/GRCh38/d9/1.70/ \
#     --endpoint-url https://s3.us-east-1.myqnapcloud.io
#   aws s3 ls s3://goliath/genomes/pangenome/GRCh38/d9-bs/1.70/ \
#     --endpoint-url https://s3.us-east-1.myqnapcloud.io
#   aws s3 ls s3://goliath/genomes/pangenome/canary/gse261315/SRR28293403/ \
#     --endpoint-url https://s3.us-east-1.myqnapcloud.io

set -euo pipefail

GENOMES_SRC="${GENOMES_SRC:-/work/genomes}"
S3_ENDPOINT_URL="${S3_ENDPOINT_URL:-https://s3.us-east-1.myqnapcloud.io}"
S3_BUCKET="${S3_BUCKET:-goliath}"
S3_PREFIX="${S3_PREFIX:-genomes}"
AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
export AWS_DEFAULT_REGION

DRY_RUN=0
DO_DELETE=0
ONLY=""
DIRECTION="upload"  # upload = local→S3, download = S3→local

usage() {
  cat <<'EOF'
Usage: scripts/sync_genomes_to_s3.sh [options]

Sync genomes between local /work/genomes and myQNAPcloud (S3-compatible).

aws s3 sync is recursive. Without --only, the entire genomes/ tree is mirrored.
Use --only to limit to a role root or a relative inventory path under genomes/.

Options:
  --download             Sync S3 → local (fill missing/outdated under /work/genomes)
  --dry-run              Pass --dryrun to aws s3 sync (no transfers)
  --delete               Pass --delete (remove destination extras absent on source; off by default)
  --only PATH            Sync only a subtree under genomes/:
                           role roots: linear | annotation | pangenome | rna
                           or a relative path, e.g.:
                             linear/GRCh38/ensembl-116
                             annotation/gencode/v50
                             rna/GRCh38/star/ensembl-116
                             pangenome/GRCh38/d9/1.70
                             pangenome/GRCh38/d9-bs/1.70
                             pangenome/canary
  -h, --help             Show this help

Required env:
  AWS_ACCESS_KEY_ID
  AWS_SECRET_ACCESS_KEY

Optional env: GENOMES_SRC, S3_ENDPOINT_URL, S3_BUCKET, S3_PREFIX, AWS_DEFAULT_REGION
EOF
}

# Normalize --only to a relative path under genomes/ (no leading slash, no ..).
normalize_only_path() {
  local raw="$1"
  local path="${raw#/}"
  path="${path#./}"
  # Strip accidental genomes/ prefix if the operator pasted a full relative inventory path.
  if [[ "$path" == genomes/* ]]; then
    path="${path#genomes/}"
  fi
  if [[ -z "$path" ]]; then
    echo "ERROR: --only path is empty" >&2
    return 2
  fi
  if [[ "$path" == *".."* ]]; then
    echo "ERROR: --only path must not contain '..' (got: $raw)" >&2
    return 2
  fi
  case "$path" in
    linear|annotation|pangenome|rna|linear/*|annotation/*|pangenome/*|rna/*) ;;
    *)
      echo "ERROR: --only must be under linear/, annotation/, pangenome/, or rna/ (got: $raw)" >&2
      return 2
      ;;
  esac
  printf '%s\n' "$path"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --download) DIRECTION="download"; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --delete) DO_DELETE=1; shift ;;
    --only)
      ONLY="${2:-}"
      if [[ -z "$ONLY" ]]; then
        echo "ERROR: --only requires a role or relative path under genomes/" >&2
        exit 2
      fi
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! command -v aws >/dev/null 2>&1; then
  echo "ERROR: aws CLI not found on PATH" >&2
  exit 1
fi

if [[ -z "${AWS_ACCESS_KEY_ID:-}" || -z "${AWS_SECRET_ACCESS_KEY:-}" ]]; then
  echo "ERROR: set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in the environment" >&2
  exit 1
fi

LOCAL="$GENOMES_SRC"
REMOTE_KEY="$S3_PREFIX"
if [[ -n "$ONLY" ]]; then
  ONLY="$(normalize_only_path "$ONLY")"
  LOCAL="${GENOMES_SRC}/${ONLY}"
  REMOTE_KEY="${S3_PREFIX}/${ONLY}"
fi

if [[ "$DIRECTION" == "upload" && ! -d "$LOCAL" ]]; then
  echo "ERROR: genomes source not found: $LOCAL" >&2
  exit 1
fi

# Ensure local dest exists for downloads
if [[ "$DIRECTION" == "download" ]]; then
  mkdir -p "$LOCAL"
fi

LOCAL_SYNC="${LOCAL%/}/"
REMOTE_URI="s3://${S3_BUCKET}/${REMOTE_KEY%/}/"

if [[ "$DIRECTION" == "download" ]]; then
  SRC_SYNC="$REMOTE_URI"
  DEST_SYNC="$LOCAL_SYNC"
else
  SRC_SYNC="$LOCAL_SYNC"
  DEST_SYNC="$REMOTE_URI"
fi

echo "Direction:   $DIRECTION"
echo "Source:      $SRC_SYNC"
echo "Destination: $DEST_SYNC"
echo "Endpoint:    $S3_ENDPOINT_URL"
echo "Region:      $AWS_DEFAULT_REGION"
if [[ -n "$ONLY" ]]; then
  echo "Only:        $ONLY"
fi
if [[ -d "$LOCAL" ]]; then
  du -sh "$LOCAL" 2>/dev/null || true
fi

# QNAP / custom S3 often needs path-style addressing (do not mutate ~/.aws)
AWS_CONFIG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/aws-genomes-sync.XXXXXX")"
cleanup_aws_config() { rm -rf "$AWS_CONFIG_DIR"; }
trap cleanup_aws_config EXIT
cat >"${AWS_CONFIG_DIR}/config" <<EOF
[default]
region = ${AWS_DEFAULT_REGION}
s3 =
    addressing_style = path
EOF
export AWS_CONFIG_FILE="${AWS_CONFIG_DIR}/config"
# Prefer env credentials over any shared credentials file
export AWS_SHARED_CREDENTIALS_FILE="${AWS_CONFIG_DIR}/empty_credentials"
: >"${AWS_SHARED_CREDENTIALS_FILE}"

AWS_ARGS=(
  s3 sync "$SRC_SYNC" "$DEST_SYNC"
  --endpoint-url "$S3_ENDPOINT_URL"
  --region "$AWS_DEFAULT_REGION"
)

if [[ "$DRY_RUN" -eq 1 ]]; then
  AWS_ARGS+=(--dryrun)
  echo "Mode:        dry-run"
fi
if [[ "$DO_DELETE" -eq 1 ]]; then
  AWS_ARGS+=(--delete)
  echo "Mode:        delete destination extras"
fi

echo "Running: aws ${AWS_ARGS[*]}"
aws "${AWS_ARGS[@]}"
echo "Done."
