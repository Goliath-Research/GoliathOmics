# Import public ENA/SRA samples into Institution, Patient, and Sample

Use this when workflow runs already reference a public ENA/SRA accession (for example the SamplePrep canary `canary-SRR28293403-linear-subset` / `SRR28293403`) and you want matching `omics/Institution`, `omics/Patient`, and `omics/Sample` rows.

The script does **not** invent clinical facts. Every written field is recorded with its public source URL in [`schemas/import/ena_public_samples.json`](../../schemas/import/ena_public_samples.json). Race, ethnicity, and year of birth stay empty unless a source states those fields. IGSR population (for example "Han Chinese South") is stored only in sample provenance, not in `Patients.Race` or `Patients.Ethnicity`.

## What it writes

| Row | Idempotent key | Official write |
|---|---|---|
| Institution named `ENA` (European Nucleotide Archive) | `portal."Institutions"."Name"` | `omics_clinical.fn_semantic_institution_create` |
| Patient (the donor, e.g. `HG00621`) | `portal."Patients"."ExternalID"` | `fn_semantic_patient_create` / `fn_semantic_patient_update` |
| Sample (the existing run sample id) | `portal."Samples"."SampleName"` | `fn_semantic_sample_create`; `Samples.Extras` holds provenance |

`Samples.CustomerID` is left null (public archive data; there is no commercial customer). The patient's `InstitutionID` points at ENA. The study `SamplePrep_e2e_clara_linear_canary` and its runs already store the sample id string; the new `portal."Samples"` row uses that same name.

The script only imports a manifest sample when its run accession is already present in `cfg.study`, `wf.workflow_instance`, `wf.instance_extension`, or `wf.scope_variable`. If the database mentions an accession that is not in the manifest, the script exits without writing and you must fetch public metadata first.

## Run

Take a Neon backup branch of `GoliathOmics_v2` first. Then, after domain schema apply (so `fn_semantic_patient_create` is present):

```bash
export PGHOST=... PGDATABASE=GoliathOmics_v2 PGUSER=... PGPASSWORD=...
python scripts/import_ena_public_samples.py              # dry run, rolled back
python scripts/import_ena_public_samples.py --apply      # commit
./scripts/omics_meta_backfill.sh institution
./scripts/omics_meta_backfill.sh patient
./scripts/omics_meta_backfill.sh sample
```

Optional `--user-id` selects the active RBAC user passed to the official functions (default: the lowest active `RBAC.Users.ID`). Cutover flags are not changed. Reruns are idempotent on the public keys above.
