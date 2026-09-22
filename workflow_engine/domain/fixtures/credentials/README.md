# Credential fixtures (examples only)

Files named `*.example.json` are **not** imported by `methyl-cfg import-fs`.
They document the shape for local/dev bootstrap.

```bash
# Copy, replace REPLACE_WITH_* placeholders, then upsert:
cp workflow_engine/domain/fixtures/credentials/goliath-archive-keys.example.json /tmp/keys.json
# edit /tmp/keys.json
methyl-cfg upsert credential --file /tmp/keys.json --publish --provider s3
```

Production: replace secrets via EpiPortal `portal.sp_*` (same credential name seeds from `portal_resource_profile.sql`).

Shell sync (`scripts/sync_genomes_to_s3.sh`) may use `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` env instead of the cfg store.

See [reference-inventory-qnap.md](../../../../docs/deployment/reference-inventory-qnap.md).
