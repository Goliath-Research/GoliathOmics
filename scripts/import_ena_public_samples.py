#!/usr/bin/env python3
"""Create omics Institution/Patient/Sample rows for public ENA/SRA runs already in this database.

The committed manifest schemas/import/ena_public_samples.json records every field
and its public source URL. This script does not invent demographic values.
It only imports accessions that are already referenced by cfg.study,
wf.workflow_instance, wf.instance_extension, or wf.scope_variable.

Institution is upserted by name (ENA). Patients are upserted by ExternalID
(the public individual id, e.g. HG00621). Samples are upserted by SampleName
(the run's existing sample id, e.g. canary-SRR28293403-linear-subset).

Official write path: omics_clinical.fn_semantic_institution_create,
fn_semantic_patient_create / fn_semantic_patient_update,
fn_semantic_sample_create. Customer is left null (public archive data).
Cutover flags are not changed. After --apply, run official backfill:

  ./scripts/omics_meta_backfill.sh institution
  ./scripts/omics_meta_backfill.sh patient
  ./scripts/omics_meta_backfill.sh sample

Dry run by default (one transaction, rolled back). Pass --apply to commit.

Requires PGHOST, PGDATABASE, PGUSER, and PGPASSWORD or PGPASSFILE.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "schemas/import/ena_public_samples.json"
ACCESSION_RE = r"(SRR|ERR|DRR|ERS|SRS|SAMN|SAMEA|SAMD|SAME[0-9]|PRJEB|PRJNA|PRJDA|GSM|GSE)[0-9]+"


def _connect():
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("psycopg is required") from exc
    for key in ("PGHOST", "PGDATABASE", "PGUSER"):
        if not os.environ.get(key):
            raise SystemExit(f"Set {key}")
    kwargs: dict[str, Any] = {
        "host": os.environ["PGHOST"],
        "port": os.environ.get("PGPORT", "5432"),
        "dbname": os.environ["PGDATABASE"],
        "user": os.environ["PGUSER"],
        "sslmode": os.environ.get("PGSSLMODE", "prefer"),
    }
    if os.environ.get("PGPASSWORD"):
        kwargs["password"] = os.environ["PGPASSWORD"]
    if os.environ.get("PGHOSTADDR"):
        kwargs["hostaddr"] = os.environ["PGHOSTADDR"]
    return psycopg.connect(**kwargs)


def load_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not data.get("institution", {}).get("name"):
        raise SystemExit("manifest institution.name is required")
    samples = data.get("samples") or []
    if not isinstance(samples, list) or not samples:
        raise SystemExit("manifest samples list is required")
    for sample in samples:
        if not sample.get("run_accession") or not sample.get("sample_name"):
            raise SystemExit("each sample needs run_accession and sample_name")
        patient = sample.get("patient") or {}
        if not patient.get("external_id") or not patient.get("name"):
            raise SystemExit("each patient needs name and external_id")
        for field in ("race", "ethnicity", "year_born"):
            if patient.get(field) not in (None, ""):
                src = (patient.get("field_sources") or {}).get(field) or {}
                if src.get("value") not in (None, ""):
                    continue
                raise SystemExit(
                    f"{field} is set on {patient.get('external_id')} without a sourced value"
                )
    return data


def discover_accessions(cur) -> set[str]:
    cur.execute(
        """
        WITH blobs AS (
          SELECT value_json::text AS t FROM wf.scope_variable
          UNION ALL
          SELECT coalesce(document_json::text, '') || ' ' || coalesce(name, '') || ' '
                 || coalesce(study_id, '') FROM cfg.study
          UNION ALL
          SELECT coalesce(context_json::text, '') FROM wf.workflow_instance
          UNION ALL
          SELECT coalesce(data_json::text, '') FROM wf.instance_extension
        )
        SELECT DISTINCT (regexp_matches(t, %(pat)s, 'g'))[1]
        FROM blobs
        """,
        {"pat": ACCESSION_RE},
    )
    return {row[0] for row in cur.fetchall() if row[0]}


def _one(cur, sql: str, params: dict[str, Any] | None = None):
    cur.execute(sql, params or {})
    return cur.fetchone()


def resolve_user_id(cur, explicit: int | None) -> int:
    if explicit is not None:
        row = _one(cur, 'SELECT "ID" FROM "RBAC"."Users" WHERE "ID" = %(id)s AND "Active" = 1', {"id": explicit})
        if not row:
            raise SystemExit(f"user_id {explicit} is not an active RBAC user")
        return int(row[0])
    row = _one(cur, 'SELECT "ID" FROM "RBAC"."Users" WHERE "Active" = 1 ORDER BY "ID" LIMIT 1')
    if not row:
        raise SystemExit("no active RBAC user; pass --user-id")
    return int(row[0])


def upsert_institution(cur, user_id: int, name: str) -> tuple[int, str]:
    row = _one(cur, 'SELECT "ID", "Name" FROM portal."Institutions" WHERE "Name" = %(name)s', {"name": name})
    if row:
        return int(row[0]), "existing"
    row = _one(
        cur,
        "SELECT institution_id, name FROM omics_clinical.fn_semantic_institution_create(%(user)s, %(payload)s::jsonb)",
        {"user": user_id, "payload": json.dumps({"name": name})},
    )
    if not row:
        raise SystemExit("institution create returned no row")
    return int(row[0]), "created"


def upsert_patient(cur, user_id: int, institution_id: int, patient: dict[str, Any]) -> tuple[int, str]:
    ext = patient["external_id"]
    payload = {
        "name": patient["name"],
        "institution_id": institution_id,
        "sex": patient.get("sex") or None,
        "year_born": patient.get("year_born"),
        "race": patient.get("race") or None,
        "ethnicity": patient.get("ethnicity") or None,
        "external_id": ext,
    }
    row = _one(
        cur,
        'SELECT "ID" FROM portal."Patients" WHERE "ExternalID" = %(ext)s ORDER BY "ID" LIMIT 1',
        {"ext": ext},
    )
    if row:
        payload["patient_id"] = int(row[0])
        out = _one(
            cur,
            "SELECT patient_id FROM omics_clinical.fn_semantic_patient_update(%(user)s, %(payload)s::jsonb)",
            {"user": user_id, "payload": json.dumps(payload)},
        )
        return int(out[0]), "updated"
    out = _one(
        cur,
        "SELECT patient_id FROM omics_clinical.fn_semantic_patient_create(%(user)s, %(payload)s::jsonb)",
        {"user": user_id, "payload": json.dumps(payload)},
    )
    return int(out[0]), "created"


def upsert_sample(
    cur,
    user_id: int,
    patient_id: int,
    sample: dict[str, Any],
    provenance: dict[str, Any],
) -> tuple[int, str]:
    name = sample["sample_name"]
    row = _one(
        cur,
        'SELECT "ID" FROM portal."Samples" WHERE "SampleName" = %(name)s ORDER BY "ID" LIMIT 1',
        {"name": name},
    )
    created = "existing"
    if not row:
        out = _one(
            cur,
            "SELECT sample_id FROM omics_clinical.fn_semantic_sample_create(%(user)s, %(payload)s::jsonb)",
            {
                "user": user_id,
                "payload": json.dumps({"patient_id": patient_id, "sample_name": name}),
            },
        )
        sample_id = int(out[0])
        created = "created"
    else:
        sample_id = int(row[0])
        cur.execute(
            'UPDATE portal."Samples" SET "PatientID" = %(pid)s WHERE "ID" = %(sid)s AND "PatientID" IS DISTINCT FROM %(pid)s',
            {"pid": patient_id, "sid": sample_id},
        )
    cur.execute(
        'UPDATE portal."Samples" SET "Extras" = COALESCE("Extras", \'{}\'::jsonb) || %(prov)s::jsonb WHERE "ID" = %(sid)s',
        {"prov": json.dumps({"ena_public_provenance": provenance}), "sid": sample_id},
    )
    return sample_id, created


def study_run_links(cur, sample_name: str) -> dict[str, Any]:
    cur.execute(
        """
        SELECT id, name, study_id, document_json->'sample_ids' AS sample_ids
        FROM cfg.study
        WHERE document_json @> jsonb_build_array(%(name)s)
           OR document_json->'sample_ids' @> to_jsonb(%(name)s::text)
        """,
        {"name": sample_name},
    )
    studies = [dict(zip(("id", "name", "study_id", "sample_ids"), r, strict=True)) for r in cur.fetchall()]
    cur.execute(
        """
        SELECT i.id
        FROM wf.workflow_instance i
        LEFT JOIN wf.instance_extension e
          ON e.workflow_instance_id = i.id AND e.extension_key = 'legacy.migration.source'
        WHERE i.context_json::text LIKE %(pat)s
           OR e.data_json::text LIKE %(pat)s
        ORDER BY i.id
        """,
        {"pat": f"%{sample_name}%"},
    )
    instance_ids = [int(r[0]) for r in cur.fetchall()]
    return {"studies": studies, "workflow_instance_ids": instance_ids}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--user-id", type=int, help="Active RBAC user id for official semantic writes (default: lowest active id)")
    ap.add_argument("--apply", action="store_true", help="commit (default: dry run, rolled back)")
    args = ap.parse_args(argv)

    manifest = load_manifest(args.manifest)
    report: dict[str, Any] = {
        "dry_run": not args.apply,
        "manifest": str(args.manifest),
        "institution": None,
        "patients": [],
        "samples": [],
        "unmapped_accessions_in_db": [],
    }
    with _connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
        if not args.apply:
            # Open a transaction that will be rolled back.
            pass
        with conn.cursor() as cur:
            user_id = resolve_user_id(cur, args.user_id)
            report["user_id"] = user_id
            db_acc = discover_accessions(cur)
            manifest_acc: set[str] = set()
            for sample in manifest["samples"]:
                manifest_acc.add(sample["run_accession"])
                for key in (
                    "ena_sample_accession",
                    "ena_secondary_sample_accession",
                    "geo_sample",
                    "geo_series",
                    "ena_study_accession",
                ):
                    if sample.get(key):
                        manifest_acc.add(sample[key])
            # Import only samples whose run accession is already in this database.
            unmapped = sorted(a for a in db_acc if a not in manifest_acc)
            report["unmapped_accessions_in_db"] = unmapped
            if unmapped:
                print(json.dumps(report, indent=2, default=str), file=sys.stderr)
                raise SystemExit(
                    "database references accessions not in the manifest; fetch public metadata and update the manifest before importing"
                )
            inst_name = manifest["institution"]["name"]
            inst_id, inst_status = upsert_institution(cur, user_id, inst_name)
            report["institution"] = {
                "institution_id": inst_id,
                "name": inst_name,
                "official_name": manifest["institution"].get("official_name"),
                "status": inst_status,
                "source_url": manifest["institution"].get("source_url"),
            }
            for sample in manifest["samples"]:
                if sample["run_accession"] not in db_acc:
                    report["samples"].append(
                        {
                            "run_accession": sample["run_accession"],
                            "status": "skipped_not_referenced_in_database",
                        }
                    )
                    continue
                patient = sample["patient"]
                # Refuse to write unsourced demographics.
                for field in ("race", "ethnicity", "year_born"):
                    if patient.get(field) not in (None, ""):
                        raise SystemExit(f"refusing to write unsourced or unexpected {field}")
                patient_id, pstatus = upsert_patient(cur, user_id, inst_id, patient)
                report["patients"].append(
                    {
                        "patient_id": patient_id,
                        "name": patient["name"],
                        "external_id": patient["external_id"],
                        "sex": patient.get("sex"),
                        "year_born": patient.get("year_born"),
                        "race": patient.get("race"),
                        "ethnicity": patient.get("ethnicity"),
                        "institution_id": inst_id,
                        "status": pstatus,
                        "field_sources": patient.get("field_sources"),
                    }
                )
                provenance = {
                    "manifest": "schemas/import/ena_public_samples.json",
                    "fetched_at_utc": manifest.get("fetched_at_utc"),
                    "run_accession": sample["run_accession"],
                    "ena_sample_title": sample.get("ena_sample_title"),
                    "sources": sample.get("sources"),
                    "patient_external_id": patient["external_id"],
                    "field_sources": patient.get("field_sources"),
                    "igsr_population": patient.get("igsr_population"),
                }
                sample_id, sstatus = upsert_sample(cur, user_id, patient_id, sample, provenance)
                links = study_run_links(cur, sample["sample_name"])
                report["samples"].append(
                    {
                        "sample_id": sample_id,
                        "sample_name": sample["sample_name"],
                        "run_accession": sample["run_accession"],
                        "patient_id": patient_id,
                        "status": sstatus,
                        "links": links,
                    }
                )
        if args.apply:
            conn.commit()
            report["committed"] = True
        else:
            conn.rollback()
            report["committed"] = False
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
