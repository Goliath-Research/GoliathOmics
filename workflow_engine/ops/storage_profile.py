"""Expand cfg.storage_profile into planner wire locations.

Runs on the gateway admin CLI (``methyl-study-start``). Workers never open
the database. Secrets are copied into the instance context by
``cfg.storage_expand`` so task payloads stay provider-neutral and the worker
does not hold a DB credential.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional

from cfg.storage_expand import expand_storage_profile
from cfg.store import ConfigRecord, ConfigStore

from methyl_domain.platform_storage import DEFAULT_ARCHIVE_PROFILE_KEY

_WIRE_KEYS = ("type", "bucket", "basePath", "endpointUrl", "container", "accountUrl")


def storage_location_is_wire(value: Any) -> bool:
    """True when ``value`` is already an expanded storage location."""
    return isinstance(value, dict) and any(key in value for key in _WIRE_KEYS)


def _json_object(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        parsed = json.loads(value)
        return dict(parsed) if isinstance(parsed, dict) else {}
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _cell(row: Any, key: str, index: int) -> Any:
    if isinstance(row, Mapping):
        return row[key]
    return row[index]


class GatewayConfigStore(ConfigStore):
    """Read-only ``ConfigStore`` over ``cfg.cfg_repo_get`` on a gateway DB handle.

    Credential secrets come from ``cfg.cfg_repo_get_credential_secret`` (published
    rows only). This is the PostgreSQL cfg registry used by the gateway. It is
    not a worker-side reader.
    """

    def __init__(self, db: Any) -> None:
        backend = getattr(db, "backend", None)
        if backend not in (None, "postgres"):
            raise RuntimeError(
                "storage profile expansion uses the gateway PostgreSQL cfg registry "
                f"(backend={backend})"
            )
        self._db = db

    def _one(self, sql: str, params: tuple[Any, ...]):
        with self._db._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
        return row

    def get(
        self,
        kind: str,
        name: str,
        *,
        version: Optional[str] = None,
        published_only: bool = False,
        include_secret: bool = False,
    ) -> Optional[ConfigRecord]:
        if kind == "credential" and include_secret:
            row = self._one(
                "SELECT id, name, version, provider, auth_mode, secret_json "
                "FROM cfg.cfg_repo_get_credential_secret(%s, %s)",
                (name, version),
            )
            if row is None:
                return None
            provider = _cell(row, "provider", 3)
            auth_mode = _cell(row, "auth_mode", 4)
            return ConfigRecord(
                kind="credential",
                name=str(_cell(row, "name", 1)),
                version=str(_cell(row, "version", 2)),
                status="published",
                content_hash="",
                document={"provider": provider, "authMode": auth_mode},
                secret=_json_object(_cell(row, "secret_json", 5)),
                extra={"provider": provider, "authMode": auth_mode},
                id=_cell(row, "id", 0),
            )

        row = self._one(
            "SELECT id, name, version, status, content_hash, document_json, "
            "secret_redacted, extra "
            "FROM cfg.cfg_repo_get(%s, %s, %s, %s)",
            (kind, name, version, published_only),
        )
        if row is None:
            return None
        extra = _json_object(_cell(row, "extra", 7))
        return ConfigRecord(
            kind=kind,  # type: ignore[arg-type]
            name=str(_cell(row, "name", 1)),
            version=str(_cell(row, "version", 2)),
            status=str(_cell(row, "status", 3)),
            content_hash=str(_cell(row, "content_hash", 4) or ""),
            document=_json_object(_cell(row, "document_json", 5)),
            secret=None,
            extra=extra,
            id=_cell(row, "id", 0),
        )


def apply_storage_to_planner_payload(
    db: Any,
    body: Mapping[str, Any],
    *,
    store: Optional[ConfigStore] = None,
) -> dict[str, Any]:
    """Keep wire-format locations from the request, else expand a cfg profile.

    ``disableArchive`` drops sample/H5 destinations. ``fastqStorage`` is still
    expanded when the request did not already carry a wire location.
    """
    out = dict(body)
    disable_archive = out.get("disableArchive") is True
    if disable_archive:
        out.pop("sampleStorage", None)
        out.pop("h5Storage", None)

    has_fastq = storage_location_is_wire(out.get("fastqStorage"))
    has_sample = storage_location_is_wire(out.get("sampleStorage")) or storage_location_is_wire(
        out.get("h5Storage")
    )
    if has_fastq and (disable_archive or has_sample):
        return out

    profile_key = str(
        out.get("archiveProfileKey")
        or out.get("archiveStorageKey")
        or out.get("storageKey")
        or DEFAULT_ARCHIVE_PROFILE_KEY
    )
    cfg_store = store if store is not None else GatewayConfigStore(db)
    try:
        expanded = expand_storage_profile(cfg_store, profile_key)
    except KeyError as exc:
        raise KeyError(f"storage_profile not found: {profile_key}") from exc

    if not has_fastq:
        fastq = expanded.get("fastqStorage")
        if not storage_location_is_wire(fastq):
            raise KeyError(f"storage_profile {profile_key} has no fastqStorage")
        out["fastqStorage"] = fastq
    if not disable_archive and not has_sample:
        sample = expanded.get("sampleStorage")
        if storage_location_is_wire(sample):
            out["sampleStorage"] = sample
    return out
