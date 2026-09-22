"""Drain cfg.study_start_request rows: bake once, then portal.sp_create_and_start_instance.

Portal never finalizes. This ops path loads site/profile/procedure from materialized
``/work`` (``methyl-cfg materialize`` must have run) and calls
``finalize_instance_context`` once.
"""

from __future__ import annotations

import json
import logging
import socket
from typing import Any, Dict, Optional

from ops._paths import ensure_import_paths

logger = logging.getLogger(__name__)


def _parse_json(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        text = value.strip()
        return json.loads(text) if text else {}
    return value


def _claim(db: Any, claimed_by: str, lease_seconds: int) -> Optional[Dict[str, Any]]:
    if db.backend == "mssql":
        row = db._fetch_one(
            "EXEC portal.sp_claim_study_start_request @claimed_by=?, @lease_seconds=?",
            (claimed_by, lease_seconds),
        )
    else:
        row = db._fetch_one(
            "SELECT * FROM portal.sp_claim_study_start_request(%s, %s)",
            (claimed_by, lease_seconds),
        )
    return row


def _complete(db: Any, request_id: int, instance_id: int) -> None:
    if db.backend == "mssql":
        db._exec_proc(
            "EXEC portal.sp_complete_study_start_request "
            "@request_id=?, @workflow_instance_id=?",
            (request_id, instance_id),
        )
    else:
        db._exec_proc(
            "SELECT * FROM portal.sp_complete_study_start_request(%s, %s)",
            (request_id, instance_id),
        )


def _fail(db: Any, request_id: int, error_message: str) -> None:
    msg = (error_message or "failed")[:4000]
    if db.backend == "mssql":
        db._exec_proc(
            "EXEC portal.sp_fail_study_start_request @request_id=?, @error_message=?",
            (request_id, msg),
        )
    else:
        db._exec_proc(
            "SELECT * FROM portal.sp_fail_study_start_request(%s, %s)",
            (request_id, msg),
        )


def _overlay(db: Any, study_row_id: int) -> Dict[str, Any]:
    if db.backend == "mssql":
        row = db._fetch_one(
            "EXEC portal.sp_get_study_action_config_overlay @study_row_id=?",
            (study_row_id,),
        )
    else:
        row = db._fetch_one(
            "SELECT * FROM portal.sp_get_study_action_config_overlay(%s)",
            (study_row_id,),
        )
    if not row:
        return {}
    raw = row.get("action_config_overlay")
    parsed = _parse_json(raw)
    return parsed if isinstance(parsed, dict) else {}


def _create_and_start(
    db: Any,
    *,
    workflow_version_id: int,
    context: Dict[str, Any],
    study_row_id: int,
    pipeline_profile_id: Optional[int],
    assay_procedure_id: Optional[int],
    site_id: Optional[int],
    storage_profile_id: Optional[int],
) -> int:
    if db.backend == "mssql":
        from rest.db.mssql import _declare_json, _json_text, _json_var

        row = db._fetch_one(
            f"{_declare_json('context')}"
            "EXEC portal.sp_create_and_start_instance "
            f"@workflow_version_id=?, @context_json={_json_var('context')}, "
            "@scope_id=?, @study_row_id=?, @domain_program_id=?, "
            "@pipeline_profile_id=?, @site_id=?, @storage_profile_id=?, "
            "@assay_procedure_id=?",
            (
                _json_text(context),
                workflow_version_id,
                None,
                study_row_id,
                None,
                pipeline_profile_id,
                site_id,
                storage_profile_id,
                assay_procedure_id,
            ),
        )
    else:
        row = db._fetch_one(
            "SELECT id, workflow_version_id, status "
            "FROM portal.sp_create_and_start_instance("
            "%s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s)",
            (
                workflow_version_id,
                json.dumps(context),
                None,
                study_row_id,
                None,
                pipeline_profile_id,
                site_id,
                storage_profile_id,
                assay_procedure_id,
            ),
        )
    if not row:
        raise RuntimeError("portal.sp_create_and_start_instance returned no row")
    instance_id = row.get("id") or row.get("workflow_instance_id")
    if instance_id is None:
        raise RuntimeError("portal.sp_create_and_start_instance returned no id")
    return int(instance_id)


def _build_body(row: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    intent = _parse_json(row.get("request_json"))
    if not isinstance(intent, dict):
        intent = {}
    body = dict(intent)
    body["workflow_version_id"] = int(row["workflow_version_id"])
    body["cfgStudyRowId"] = int(row["study_row_id"])
    if overlay:
        body["actionConfig"] = overlay
    if not body.get("projectPath"):
        raise ValueError("request_json.projectPath is required (materialize study first)")
    return body


def process_one_request(db: Any, row: Dict[str, Any]) -> Dict[str, Any]:
    """Bake + create/start/link one claimed row. Caller owns claim/complete/fail."""
    ensure_import_paths()
    from ops.sample_lifecycle import plan_sample_prep_instance_context
    from ops.study_lifecycle import plan_study_validation_instance_context

    study_row_id = int(row["study_row_id"])
    overlay = _overlay(db, study_row_id)
    body = _build_body(row, overlay)
    stage = str(row["stage"])
    if stage == "sample_prep":
        context = plan_sample_prep_instance_context(db, body)
    elif stage == "study_validation":
        context = plan_study_validation_instance_context(body)
    else:
        raise ValueError(f"unsupported stage: {stage}")

    instance_id = _create_and_start(
        db,
        workflow_version_id=int(row["workflow_version_id"]),
        context=context,
        study_row_id=study_row_id,
        pipeline_profile_id=row.get("pipeline_profile_id"),
        assay_procedure_id=row.get("assay_procedure_id"),
        site_id=row.get("site_id"),
        storage_profile_id=row.get("storage_profile_id"),
    )
    return {
        "request_id": int(row.get("request_id") or row.get("id")),
        "instance_id": instance_id,
        "stage": stage,
        "workflow_version_id": int(row["workflow_version_id"]),
    }


def drain_requests(
    db: Any,
    *,
    claimed_by: Optional[str] = None,
    lease_seconds: int = 600,
    limit: int = 1,
) -> list[Dict[str, Any]]:
    owner = claimed_by or f"methyl-study-start@{socket.gethostname()}"
    results: list[Dict[str, Any]] = []
    for _ in range(max(1, limit)):
        row = _claim(db, owner, lease_seconds)
        if not row:
            break
        request_id = int(row.get("request_id") or row.get("id"))
        try:
            payload = process_one_request(db, row)
            _complete(db, request_id, int(payload["instance_id"]))
            results.append(payload)
        except Exception as exc:
            logger.exception("study start request %s failed", request_id)
            try:
                _fail(db, request_id, str(exc))
            except Exception:
                logger.exception("failed to mark request %s as failed", request_id)
            results.append(
                {
                    "request_id": request_id,
                    "error": str(exc),
                    "status": "failed",
                }
            )
    return results
