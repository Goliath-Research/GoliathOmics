"""omics/SequencingRun and omics/AlignmentResult writers (database/schema/22_omics_run_alignment_objects.sql).

PostgreSQL integration: the engine hook records one object per succeeded source step, links it to
its run, sample and study, writes through the Meta API (ObjValuesHistory rows) and never fails the
engine; the official backfill derives objects from existing rows only and is a no-op when rerun.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest

from test_omics_pg_schema_apply import REPO_ROOT, _psql, pg_schema_applied  # noqa: F401

SCHEMA_FILE = REPO_ROOT / "database" / "schema" / "22_omics_run_alignment_objects.sql"
BACKFILL = REPO_ROOT / "scripts" / "omics_run_alignment_backfill.sh"
HOOK = "trg_omics_node_execution_meta_objects"

FASTQ_OUT = {
    "status": "ok",
    "sampleId": None,
    "fastqFiles": ["/work/samples/{s}/SRR90000001_1.fastq.gz", "/work/samples/{s}/SRR90000001_2.fastq.gz"],
}


# --- unit ---------------------------------------------------------------------------------


def test_schema_file_is_in_the_apply_order() -> None:
    order = (REPO_ROOT / "scripts" / "domain_schema_order.sh").read_text(encoding="utf-8")
    assert "22_omics_run_alignment_objects.sql" in order
    assert "omics_run_alignment_objects.sql" in (REPO_ROOT / "workflow_engine" / "sql_pg" / "apply_schema.sh").read_text(
        encoding="utf-8"
    )
    link = REPO_ROOT / "workflow_engine" / "sql_pg" / "omics_run_alignment_objects.sql"
    assert link.is_symlink() and link.resolve() == SCHEMA_FILE.resolve()


def test_writers_use_the_meta_api_and_no_utc_wall_clock() -> None:
    text = SCHEMA_FILE.read_text(encoding="utf-8")
    assert '"Meta".sp_obj_insert' in text and '"Meta".sp_obj_update' in text
    assert 'INSERT INTO "Meta"."ObjValues"' not in text
    assert "now() AT TIME ZONE" not in text


def test_backfill_script_refuses_unknown_arguments() -> None:
    proc = subprocess.run(["bash", str(BACKFILL), "--force"], capture_output=True, text=True, check=False)
    assert proc.returncode == 2 and "usage" in proc.stderr


# --- PostgreSQL ---------------------------------------------------------------------------


def _q(env: dict[str, str], sql: str) -> str:
    return _psql(sql, env)


@pytest.fixture(scope="module")
def run_env(pg_schema_applied: dict[str, str]) -> dict[str, object]:  # noqa: F811
    env = pg_schema_applied
    suffix = uuid.uuid4().hex[:8]
    for action in ("sample.download_fastq", "sample.parabricks_fq2bam", "sample.methyl_qc"):
        _q(env, f"INSERT INTO wf.workflow_action (action_name) SELECT '{action}' "
                f"WHERE NOT EXISTS (SELECT 1 FROM wf.workflow_action WHERE action_name = '{action}')")
    graph = {
        "name": f"RunAlignTest-{suffix}",
        "root_node_key": "root",
        "nodes": [
            {"node_key": "root", "node_type": "SEQUENCE"},
            {"node_key": "fetch", "node_type": "ACTION", "action_name": "sample.download_fastq"},
            {"node_key": "align", "node_type": "ACTION", "action_name": "sample.parabricks_fq2bam"},
            {"node_key": "qc", "node_type": "ACTION", "action_name": "sample.methyl_qc"},
        ],
        "edges": [
            {"parent_node_key": "root", "child_node_key": "fetch", "child_order": 0, "branch_kind": "SEQUENCE"},
            {"parent_node_key": "root", "child_node_key": "align", "child_order": 1, "branch_kind": "SEQUENCE"},
            {"parent_node_key": "root", "child_node_key": "qc", "child_order": 2, "branch_kind": "SEQUENCE"},
        ],
    }
    created = json.loads(_q(env, f"SELECT wf.wf_repo_create_workflow_graph('{json.dumps(graph)}'::jsonb)"))
    version_id = created["workflow_version_id"]
    nodes = {
        key: int(_q(env, f"SELECT id FROM wf.workflow_node WHERE workflow_version_id = {version_id} AND node_key = '{key}'"))
        for key in ("fetch", "align", "qc")
    }
    _org, scope_id = _q(env, f"SELECT * FROM portal.sp_organization_create('RunAlign org {suffix}')").split("|")
    patient = _q(env, f"""INSERT INTO portal."Patients" ("Name", "ExternalID") VALUES ('test-patient-{suffix}', 'TP-{suffix}') RETURNING "ID" """)
    sample_name = f"test-sample-{suffix}"
    sample_id = int(_q(env, f"""INSERT INTO portal."Samples" ("PatientID", "SampleName") VALUES ({patient}, '{sample_name}') RETURNING "ID" """))
    study_id = int(_q(env, f"""INSERT INTO cfg.study (name, content_hash, document_json)
                              VALUES ('RunAlign study {suffix}', md5('{suffix}'), '{{}}'::jsonb) RETURNING id"""))
    _q(env, f"SELECT omics_clinical.sp_meta_mirror_study({study_id}, {scope_id})")
    return {"env": env, "version_id": version_id, "nodes": nodes, "scope_id": int(scope_id),
            "sample_name": sample_name, "sample_id": sample_id, "study_id": study_id}


def _instance(ctx: dict[str, object], *, link_study: bool = True) -> int:
    env = ctx["env"]
    inst = int(_q(env, f"""INSERT INTO wf.workflow_instance (workflow_version_id, status, context_json, started_at_utc)
                         VALUES ({ctx['version_id']}, 'RUNNING', '{{}}'::jsonb, now()) RETURNING id"""))
    if link_study:
        _q(env, f"INSERT INTO cfg.study_instance_link (study_row_id, workflow_instance_id) VALUES ({ctx['study_id']}, {inst})")
    return inst


def _step(ctx: dict[str, object], inst: int, node: str, output: dict | None, *, status: str = "SUCCEEDED",
          input_json: dict | None = None) -> int:
    """Insert a RUNNING node execution, then finish it the way the engine does (one UPDATE)."""
    env = ctx["env"]
    inp = json.dumps({"sampleId": ctx["sample_name"]} if input_json is None else input_json)
    ne = int(_q(env, f"""INSERT INTO wf.node_execution (workflow_instance_id, workflow_node_id, status, input_json, started_at_utc)
                       VALUES ({inst}, {ctx['nodes'][node]}, 'RUNNING', '{inp}'::jsonb, now()) RETURNING id"""))
    out = json.dumps(output) if output is not None else "null"
    _q(env, f"""UPDATE wf.node_execution SET status = '{status}', output_json = '{out}'::jsonb, ended_at_utc = now()
               WHERE id = {ne}""")
    return ne


def _fastq_out(ctx: dict[str, object]) -> dict:
    s = ctx["sample_name"]
    return {"status": "ok", "sampleId": s, "fastqFiles": [f.format(s=s) for f in FASTQ_OUT["fastqFiles"]]}


def _align_out(ctx: dict[str, object], inst: int) -> dict:
    s = ctx["sample_name"]
    return {"status": "ok", "sampleId": s, "bamPath": f"/work/samples/{s}/run{inst}/{s}.bam",
            "qcMetricsTar": f"/work/samples/{s}/run{inst}/{s}.qc-metrics.tar",
            "domainSample": {"sampleDir": f"/work/samples/{s}/align.linear.parabricks"}}


def _obj(env: dict[str, str], ne: int, cls: str) -> int | None:
    v = _q(env, f"SELECT meta_obj_id FROM omics_clinical.wf_meta_object_link WHERE source_node_execution_id = {ne} AND class_name = '{cls}'")
    return int(v) if v else None


def _doc(env: dict[str, str], obj: int) -> dict:
    return json.loads(_q(env, f"""SELECT jsonb_object_agg(k, v) FROM "Meta"."ObjValues" ov, jsonb_each(ov."Data") AS e(k, v)
                                  WHERE ov."ObjID" = {obj}"""))


def _history(env: dict[str, str], obj: int) -> int:
    return int(_q(env, f'SELECT count(*) FROM "Meta"."ObjValuesHistory" WHERE "ObjID" = {obj}'))


def test_engine_hook_records_a_run_and_its_alignment_once(run_env: dict[str, object]) -> None:
    ctx = run_env
    env = dict(ctx["env"], PGTZ="America/New_York")  # a session far from UTC
    ctx = {**ctx, "env": env}
    inst = _instance(ctx)
    fetch = _step(ctx, inst, "fetch", _fastq_out(ctx))
    align = _step(ctx, inst, "align", _align_out(ctx, inst),
                  input_json={"sampleId": ctx["sample_name"], "resolvedConfig": {"engine": "mojo"}})
    qc = _step(ctx, inst, "qc", {"status": "ok", "sampleId": ctx["sample_name"]})

    run_obj = _obj(env, fetch, "omics/SequencingRun")
    aln_obj = _obj(env, align, "omics/AlignmentResult")
    assert run_obj and aln_obj
    assert _q(env, f"SELECT count(*) FROM omics_clinical.wf_meta_object_link WHERE source_node_execution_id = {qc}") == "0"
    assert _q(env, f"""SELECT string_agg(c."Name", ',' ORDER BY o."ID") FROM "Meta"."Objs" o JOIN "Meta"."Classes" c ON c."ID" = o."ClassID"
                       WHERE o."ID" IN ({run_obj}, {aln_obj})""") == "omics/SequencingRun,omics/AlignmentResult"

    run = _doc(env, run_obj)
    assert run["source_node_execution_id"] == fetch and run["workflow_instance_id"] == inst
    assert run["run_accession"] == "SRR90000001" and run["fastq_count"] == 2
    assert run["sample_id"] == ctx["sample_id"] and run["study_id"] == ctx["study_id"]
    ended = _q(env, f"""SELECT to_char(ended_at_utc AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') FROM wf.node_execution WHERE id = {fetch}""")
    assert run["ingested_at_utc"] == ended

    aln = _doc(env, aln_obj)
    assert aln["sequencing_run_id"] == fetch
    assert aln["aligner_action"] == "sample.parabricks_fq2bam" and aln["align_engine"] == "mojo"
    assert aln["bam_path"].endswith(".bam") and aln["qc_metrics_path"].endswith(".qc-metrics.tar")
    assert aln["sample_id"] == ctx["sample_id"] and aln["study_id"] == ctx["study_id"]
    assert aln["completed_at_utc"].endswith("Z")

    # Scoped like the study, and the Meta history recorded the creation.
    for obj in (run_obj, aln_obj):
        assert _q(env, f'SELECT "ScopeID" FROM portal."ScopedMetaObjects" WHERE "ObjID" = {obj}') == str(ctx["scope_id"])
        kinds = _q(env, f"""SELECT string_agg(DISTINCT "ChangeKind", ',') FROM "Meta"."ObjValuesHistory" WHERE "ObjID" = {obj}""")
        assert {"object.create", "values.insert", "object.scope"} <= set(kinds.split(","))

    # The engine touching the same row again changes nothing (no new object, no history row).
    before = (_history(env, run_obj), _history(env, aln_obj))
    _q(env, f"UPDATE wf.node_execution SET status = 'SUCCEEDED' WHERE id IN ({fetch}, {align})")
    _q(env, f"SELECT omics_clinical.sp_alignment_result_record({align})")
    assert (_history(env, run_obj), _history(env, aln_obj)) == before
    assert _q(env, f"SELECT count(*) FROM omics_clinical.wf_meta_object_link WHERE source_node_execution_id IN ({fetch}, {align})") == "2"
    assert _q(env, "SELECT count(*) FROM portal.fn_ledger_verify_all()") == "0"


def test_failed_alignment_records_nothing(run_env: dict[str, object]) -> None:
    ctx = run_env
    inst = _instance(ctx)
    align = _step(ctx, inst, "align", {"status": "error"}, status="FAILED")
    assert _obj(ctx["env"], align, "omics/AlignmentResult") is None
    with pytest.raises(RuntimeError, match="not a succeeded alignment step"):
        _q(ctx["env"], f"SELECT omics_clinical.sp_alignment_result_record({align})")


def test_backfill_derives_objects_from_existing_rows_and_rerun_is_a_noop(run_env: dict[str, object]) -> None:
    ctx = run_env
    env = ctx["env"]
    # Rows finished before the writers existed: the hook is off while they are written.
    _q(env, f"ALTER TABLE wf.node_execution DISABLE TRIGGER {HOOK}")
    try:
        inst = _instance(ctx, link_study=False)
        fetch = _step(ctx, inst, "fetch", _fastq_out(ctx))
        align = _step(ctx, inst, "align", _align_out(ctx, inst))
    finally:
        _q(env, f"ALTER TABLE wf.node_execution ENABLE TRIGGER {HOOK}")
    pending = _q(env, f"SELECT string_agg(class_name, ',' ORDER BY class_name) FROM omics_clinical.v_run_alignment_pending "
                      f"WHERE node_execution_id IN ({fetch}, {align})")
    assert pending == "omics/AlignmentResult,omics/SequencingRun"

    dry = subprocess.run(["bash", str(BACKFILL)], env=env, capture_output=True, text=True, check=False)
    assert dry.returncode == 0, dry.stderr
    assert "dry run" in dry.stdout
    assert _obj(env, fetch, "omics/SequencingRun") is None

    first = subprocess.run(["bash", str(BACKFILL), "--apply"], env=env, capture_output=True, text=True, check=False)
    assert first.returncode == 0, first.stderr
    assert "omics/SequencingRun: source_rows=" in first.stdout and "pending after: 0" in first.stdout
    run_obj = _obj(env, fetch, "omics/SequencingRun")
    aln_obj = _obj(env, align, "omics/AlignmentResult")
    assert run_obj and aln_obj
    assert _doc(env, aln_obj)["sequencing_run_id"] == fetch
    assert "study_id" not in _doc(env, run_obj)  # no study link on this instance: nothing invented
    assert _history(env, run_obj) > 0 and _history(env, aln_obj) > 0

    counts = _q(env, "SELECT count(*) FROM omics_clinical.wf_meta_object_link")
    history = _q(env, 'SELECT count(*) FROM "Meta"."ObjValuesHistory"')
    second = subprocess.run(["bash", str(BACKFILL), "--apply"], env=env, capture_output=True, text=True, check=False)
    assert second.returncode == 0, second.stderr
    for line in second.stdout.splitlines():
        if "source_rows=" in line:
            assert "created=0 updated=0" in line, line
    assert _q(env, "SELECT count(*) FROM omics_clinical.wf_meta_object_link") == counts
    assert _q(env, 'SELECT count(*) FROM "Meta"."ObjValuesHistory"') == history
    # Every source row has exactly one object of its class.
    assert _q(env, """SELECT count(*) FROM wf.node_execution ne
                      JOIN wf.workflow_node wn ON wn.id = ne.workflow_node_id
                      JOIN wf.workflow_action wa ON wa.id = wn.workflow_action_id
                      JOIN omics_clinical.wf_meta_object_source s ON s.action_name = wa.action_name
                      WHERE ne.status = 'SUCCEEDED'""") == counts
    assert _q(env, "SELECT count(*) FROM portal.fn_ledger_verify_all()") == "0"
    assert _q(env, "SELECT count(*) FROM omics_clinical.fn_utc_timestamp_violations()") == "0"
    link_history = _q(env, "SELECT count(*) FROM omics_clinical.record_history WHERE table_name = 'omics_clinical.wf_meta_object_link'")
    assert int(link_history) >= int(counts)


def test_a_writer_error_never_fails_the_engine(run_env: dict[str, object]) -> None:
    ctx = run_env
    env = ctx["env"]
    inst = _instance(ctx)
    # A succeeded ingest without a sampleId cannot be recorded; the engine's update still commits.
    ne = _step(ctx, inst, "fetch", {"status": "ok"}, input_json={})
    assert _q(env, f"SELECT status FROM wf.node_execution WHERE id = {ne}") == "SUCCEEDED"
    assert _q(env, f"SELECT count(*) FROM omics_clinical.v_run_alignment_pending WHERE node_execution_id = {ne}") == "1"
    # Leave the shared database without an unrecordable row.
    _q(env, f"UPDATE wf.node_execution SET status = 'FAILED' WHERE id = {ne}")
