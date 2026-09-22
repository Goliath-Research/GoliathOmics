"""FOREACH iteration-bundle CAAS: short-circuit whole BODY fan-out on content hit.

Bundle keys hash the FOREACH node identity, the bound iteration item, child
action revisions, and — for nested FOREACH — a fingerprint of ancestor FOREACH
bindings (``extra_input_fingerprint`` / ``__foreach_ancestry__``). Without the
parent chain, identical leaf items under different parents collide (e.g. context
``CG`` under control vs disease ``centroidSeedGroups``), and later parents are
falsely skipped. On hit, the local (or DB) scheduler marks the BODY as skipped
without claiming leaf ACTIONs. On miss, normal fan-out runs; callers commit a
bundle pointing at child content keys.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .action_result import utc_now

logger = logging.getLogger(__name__)

BUNDLE_ACTION_SAFE = "foreach_bundle"

# Scope key: stack of ancestor FOREACH bindings so nested identical leaf items
# (e.g. context="CG" under control vs disease seed groups) do not collide.
FOREACH_ANCESTRY_SCOPE_KEY = "__foreach_ancestry__"


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fingerprint_foreach_ancestry(ancestry: Sequence[Any] | None) -> Optional[str]:
    """Hash parent FOREACH chain for nested iteration-bundle keys."""
    if not ancestry:
        return None
    return _sha256_text(_canonical_json(list(ancestry)))


def foreach_ancestry_from_scope(scope_values: Mapping[str, Any] | None) -> List[Any]:
    """Return a copy of the parent FOREACH ancestry stack from scope."""
    if not scope_values:
        return []
    raw = scope_values.get(FOREACH_ANCESTRY_SCOPE_KEY)
    if not isinstance(raw, list):
        return []
    return list(raw)


def extend_foreach_ancestry(
    parent_ancestry: Sequence[Any],
    *,
    foreach_node_key: str,
    collection_var: str,
    iteration_index: int,
    item_payload: Any,
) -> List[Any]:
    """Append the current FOREACH frame for nested children to close over."""
    return list(parent_ancestry) + [
        {
            "foreach_node_key": foreach_node_key,
            "collection_var": collection_var,
            "iteration_index": int(iteration_index),
            "item": item_payload,
        }
    ]


def compute_iteration_bundle_key(
    *,
    foreach_node_key: str,
    collection_var: str,
    iteration_index: int,
    item_payload: Any,
    child_action_revisions: Sequence[str] = (),
    extra_input_fingerprint: Optional[str] = None,
) -> str:
    """Content key for one FOREACH BODY iteration.

    ``extra_input_fingerprint`` must carry parent FOREACH identity for nested
    loops whose leaf item alone is not unique (identical ``contexts`` / chromosomes
    under different ``centroidSeedGroups`` / ``centroidGroups``).
    """
    payload = {
        "foreach_node_key": foreach_node_key,
        "collection_var": collection_var,
        "iteration_index": int(iteration_index),
        "item": item_payload,
        "child_action_revisions": list(child_action_revisions),
    }
    if extra_input_fingerprint:
        payload["extra_input_fingerprint"] = extra_input_fingerprint
    return _sha256_text(_canonical_json(payload))


def bundle_entry_dir(project_root: Path, content_key: str) -> Path:
    return Path(project_root) / ".caas" / BUNDLE_ACTION_SAFE / content_key


def read_iteration_bundle(
    project_root: Path, content_key: str
) -> Optional[Dict[str, Any]]:
    manifest = bundle_entry_dir(project_root, content_key) / "manifest.json"
    if not manifest.is_file():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("content_key") != content_key:
        return None
    return data


def commit_iteration_bundle(
    project_root: Path,
    content_key: str,
    *,
    foreach_node_key: str,
    collection_var: str,
    iteration_index: int,
    item_payload: Any,
    child_content_keys: Optional[Mapping[str, str]] = None,
    status: str = "completed",
) -> Path:
    """Write an iteration-bundle manifest under ``.caas/foreach_bundle/{key}/``."""
    entry = bundle_entry_dir(project_root, content_key)
    entry.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": "foreach_bundle_v1",
        "content_key": content_key,
        "foreach_node_key": foreach_node_key,
        "collection_var": collection_var,
        "iteration_index": int(iteration_index),
        "item": item_payload,
        "child_content_keys": dict(child_content_keys or {}),
        "status": status,
        "committed_at_utc": utc_now().isoformat().replace("+00:00", "Z"),
    }
    path = entry / "manifest.json"
    tmp = entry / "manifest.json.tmp"
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def try_short_circuit_foreach_iteration(
    project_root: Optional[Path],
    *,
    foreach_node_key: str,
    collection_var: str,
    iteration_index: int,
    item_payload: Any,
    child_action_revisions: Sequence[str] = (),
    extra_input_fingerprint: Optional[str] = None,
    enabled: bool = True,
) -> Optional[Dict[str, Any]]:
    """Return bundle manifest when this iteration can be skipped; else None."""
    if not enabled or project_root is None:
        return None
    root = Path(project_root)
    if not root.is_dir():
        return None
    key = compute_iteration_bundle_key(
        foreach_node_key=foreach_node_key,
        collection_var=collection_var,
        iteration_index=iteration_index,
        item_payload=item_payload,
        child_action_revisions=child_action_revisions,
        extra_input_fingerprint=extra_input_fingerprint,
    )
    hit = read_iteration_bundle(root, key)
    if hit is None:
        return None
    logger.info(
        "FOREACH CAAS short-circuit node=%s iter=%s key=%s",
        foreach_node_key,
        iteration_index,
        key[:12],
    )
    return hit


def resolve_study_root_from_scope(scope_values: Mapping[str, Any]) -> Optional[Path]:
    """Best-effort study root (``output_base/project_name``) from FOREACH scope."""
    for key in ("projectPath", "project", "monteCarloRunsRoot", "outputDir", "runDir"):
        raw = scope_values.get(key)
        if not raw:
            continue
        path = Path(str(raw)).expanduser()
        try:
            path = path.resolve()
        except OSError:
            continue
        if path.is_file() and path.name.endswith(".json"):
            # study manifest or per-run project.json
            parent = path.parent
            if parent.name.startswith("run_") or parent.name == "production":
                # .../monte_carlo_runs/run_XXXX → study root two up from run, three from file
                mc = parent.parent if parent.name.startswith("run_") else parent.parent
                if mc.name == "monte_carlo_runs":
                    return mc.parent
            # configs/project_*.json living outside study tree — try load
            try:
                from methyl_utils import load_project

                cfg = load_project(str(path))
                return Path(cfg.output_base) / cfg.project_name
            except Exception:
                return parent
        if path.is_dir():
            if path.name == "monte_carlo_runs":
                return path.parent
            if (path / "monte_carlo_runs").is_dir():
                return path
            if path.name.startswith("run_"):
                return path.parent.parent
    return None
