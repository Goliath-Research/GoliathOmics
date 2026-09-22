"""Signature-based idempotent skip/replay for workflow ACTION execution."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from methyl_domain.action_result import (
    ActionExecutionRecord,
    ArtifactRef,
    action_results_dir,
    artifact_ref_for,
    atomic_write_action_result,
    manifest_path_for,
    read_action_result,
    utc_now,
)
from methyl_domain.content_store import (
    append_instance_ledger,
    caas_enabled,
    commit_artifacts_to_store,
    link_entry_into_place,
    read_caas_entry,
    resolve_caas_root,
    verify_entry_artifacts,
)
from pydantic import BaseModel

from .action_catalog import ActionCatalogEntry, idempotency_enabled_for
from .action_execution import (
    ActionExecutionResult,
    execution_result_from_output,
    finalize_output,
    load_output_model,
    validate_input,
)
from .collectors import _run_key
from .task_schema_registry import resolve_task_schema_spec
from .task_validation import strip_runtime_input

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CATALOG_JSON = _REPO_ROOT / "schemas" / "actions" / "catalog.json"
_TASK_SCHEMAS = _REPO_ROOT / "schemas" / "tasks"

_PATH_SUFFIXES = (".json", ".csv", ".tsv", ".h5", ".hdf5", ".bam", ".txt", ".md")

# Dual NFS mounts that must hash to the same content keys across workers.
_DUAL_MOUNT_PREFIXES = (
    "/lambda/nfs/Work",
    "/lambda/nfs/work",
    "/Work",
)


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _file_content_fingerprint(path: Path, *, max_bytes: int = 1_048_576) -> str:
    """Stable content digest: full sha256 for small files, size+prefix/suffix for large."""
    try:
        st = path.stat()
    except OSError:
        return "missing"
    size = int(st.st_size)
    if size <= max_bytes:
        h = hashlib.sha256()
        try:
            with path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    h.update(chunk)
            return f"{size}:{h.hexdigest()[:16]}"
        except OSError:
            return f"{size}:unreadable"
    # Large files: size + first/last 64 KiB (avoids mtime-only NFS remount forks).
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            head = fh.read(65536)
            h.update(head)
            if size > 65536:
                fh.seek(max(0, size - 65536))
                h.update(fh.read(65536))
        return f"{size}:{h.hexdigest()[:16]}"
    except OSError:
        return f"{size}:unreadable"


def _load_path_remap(input_json: Mapping[str, Any]) -> Dict[str, str]:
    """Study ``path_remap`` from resolvedConfig / regulatory / project JSON."""
    for container in (
        input_json.get("resolvedConfig"),
        input_json.get("actionConfig"),
    ):
        if isinstance(container, Mapping):
            remap = container.get("path_remap") or container.get("pathRemap")
            if isinstance(remap, Mapping) and remap:
                return {str(k): str(v) for k, v in remap.items()}
    project = input_json.get("projectPath") or input_json.get("project")
    if not project:
        return {}
    path = Path(str(project))
    if not path.is_file():
        return {}
    try:
        from methyl_utils import load_project

        cfg = load_project(str(path))
        raw = getattr(cfg, "path_remap", None) or {}
        if isinstance(raw, Mapping):
            return {str(k): str(v) for k, v in raw.items()}
    except Exception:
        pass
    return {}


def _canonicalize_abs_path(path_str: str, path_remap: Mapping[str, str]) -> str:
    """Remap dual mounts / study path_remap, then resolve to a stable absolute form."""
    text = path_str
    if path_remap:
        try:
            from methyl_validation.path_remap import remap_path_string

            text = remap_path_string(text, dict(path_remap))
        except Exception:
            pass
    try:
        resolved = str(Path(text).expanduser().resolve())
    except OSError:
        resolved = text
    # Collapse known dual mounts onto /work/... so workers share content keys.
    for prefix in _DUAL_MOUNT_PREFIXES:
        if resolved == prefix or resolved.startswith(prefix + "/"):
            rel = resolved[len(prefix) :].lstrip("/")
            resolved = f"/work/{rel}" if rel else "/work"
            break
    return resolved


def _normalize_path_strings(
    value: Any,
    path_remap: Optional[Mapping[str, str]] = None,
) -> Any:
    remap = dict(path_remap or {})
    if isinstance(value, str):
        if value.startswith("/") or value.startswith("~"):
            return _canonicalize_abs_path(value, remap)
        return value
    if isinstance(value, dict):
        return {k: _normalize_path_strings(v, remap) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize_path_strings(v, remap) for v in value]
    return value


def compute_action_revision(entry: ActionCatalogEntry) -> str:
    parts: List[str] = []
    if _CATALOG_JSON.is_file():
        parts.append(_sha256_text(_CATALOG_JSON.read_text(encoding="utf-8"))[:16])
    spec = resolve_task_schema_spec(entry.action_name, entry.capability)
    if spec is not None:
        for name in (spec.input_filename, spec.output_filename):
            path = _TASK_SCHEMAS / name
            if path.is_file():
                parts.append(_sha256_text(path.read_text(encoding="utf-8"))[:16])
    release = os.environ.get("METHYL_PIPELINE_RELEASE", "").strip()
    if release:
        parts.append(release)
    return "+".join(parts) if parts else "unknown"


def _load_action_config_slice(entry: ActionCatalogEntry, input_json: Mapping[str, Any]) -> Optional[dict]:
    if not entry.action_config_key:
        return None
    from methyl_utils import load_project
    from methyl_utils.action_config_resolver import resolve_from_task_input

    regulatory: Optional[Mapping[str, Any]] = None
    project = input_json.get("projectPath") or input_json.get("project")
    if project:
        path = Path(str(project))
        if path.is_dir():
            candidates = sorted(path.glob("project*.json"))
            path = candidates[0] if candidates else path / "project.json"
        if path.is_file():
            try:
                regulatory = load_project(str(path)).get_regulatory_config()
            except Exception:
                regulatory = None
    slice_cfg = resolve_from_task_input(
        entry.action_config_key,
        input_json,
        regulatory=regulatory,
    )
    return slice_cfg if slice_cfg else None


def _previous_mc_run_dir(run_dir: Path) -> Optional[Path]:
    if not run_dir.name.startswith("run_"):
        return None
    try:
        n = int(run_dir.name.split("_", 1)[1])
    except (IndexError, ValueError):
        return None
    if n <= 1:
        return None
    prev = run_dir.parent / f"run_{n - 1:04d}"
    return prev if prev.is_dir() else None


def _directory_fingerprint(root: Path, *, max_files: int = 500) -> Optional[str]:
    """Content-oriented tree fingerprint (size + content digest; not mtime-only)."""
    if not root.is_dir():
        return None
    entries: List[str] = []
    count = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        count += 1
        if count > max_files:
            entries.append("…truncated")
            break
        rel = path.relative_to(root).as_posix()
        entries.append(f"{rel}:{_file_content_fingerprint(path)}")
    if not entries:
        return None
    return _sha256_text("\n".join(entries))[:16]


def _incremental_centroid_extra(input_json: Mapping[str, Any]) -> Optional[dict]:
    project = input_json.get("projectPath") or input_json.get("project")
    if not project:
        return None
    run_dir = Path(str(project)).resolve().parent
    extra: dict[str, Any] = {}
    prev = _previous_mc_run_dir(run_dir)
    if prev is not None:
        extra["previousRunDir"] = str(prev)
        fp = _directory_fingerprint(prev / "centroids")
        if fp:
            extra["previousCentroidsFingerprint"] = fp
    if input_json.get("addSamples") or input_json.get("removeSamples"):
        extra["addSamples"] = input_json.get("addSamples")
        extra["removeSamples"] = input_json.get("removeSamples")
    seed_dir = input_json.get("centroidSeedDir")
    if seed_dir:
        extra["centroidSeedDir"] = str(seed_dir)
        fp = _directory_fingerprint(Path(str(seed_dir)))
        if fp:
            extra["centroidSeedFingerprint"] = fp
    return extra or None


def _stability_mc_extra(input_json: Mapping[str, Any]) -> Optional[dict]:
    """Fingerprint Monte Carlo run inputs so stability CAAS does not reuse stale panels.

    Config-only signatures previously allowed replaying empty BA-gated stability
    results after discovery CSVs were regenerated under a raw_pool profile.
    """
    mc_root = _resolve_monte_carlo_runs_root(input_json)
    if mc_root is None or not mc_root.is_dir():
        return None
    extra: dict[str, Any] = {"monteCarloRunsRoot": str(mc_root)}
    snapshot = mc_root / "queue" / "mc_config.json"
    if snapshot.is_file() or snapshot.is_symlink():
        try:
            st = snapshot.resolve().stat() if snapshot.is_symlink() else snapshot.stat()
            extra["mcConfig"] = f"{st.st_size}:{int(st.st_mtime)}"
        except OSError:
            extra["mcConfig"] = "missing"
    run_dirs = sorted(p for p in mc_root.glob("run_*") if p.is_dir())
    extra["nRuns"] = len(run_dirs)
    # Cheap per-run discovery presence fingerprint (not full CSV hash).
    discovery_bits: List[str] = []
    for run_dir in run_dirs[:64]:
        n_disc = 0
        newest = 0
        total_bytes = 0
        for csv in run_dir.glob("detections/**/dmps-*-discovery.csv"):
            try:
                st = csv.stat()
            except OSError:
                continue
            n_disc += 1
            newest = max(newest, int(st.st_mtime))
            total_bytes += int(st.st_size)
        discovery_bits.append(f"{run_dir.name}:{n_disc}:{total_bytes}:{newest}")
    if discovery_bits:
        extra["discoveryFingerprint"] = _sha256_text("\n".join(discovery_bits))[:16]
    return extra


def _split_reuse_fingerprint(input_json: Mapping[str, Any]) -> Optional[dict]:
    """Fingerprint upstream MC split CSVs so model-MC CAAS aligns with split-reuse identity."""
    mc_root = _resolve_monte_carlo_runs_root(input_json)
    if mc_root is None or not mc_root.is_dir():
        return None
    extra: dict[str, Any] = {}
    try:
        from methyl_validation.reuse_splits import fingerprint_mc_splits_root

        fp = fingerprint_mc_splits_root(mc_root)
        if fp:
            extra["splitReuseFingerprint"] = fp
    except Exception:
        logger.debug("split reuse fingerprint failed", exc_info=True)
    summary = mc_root / "split_reuse_summary.json"
    if not summary.is_file():
        for candidate in mc_root.glob("**/split_reuse_summary.json"):
            summary = candidate
            break
    if summary.is_file():
        extra["splitReuseSummary"] = _file_content_fingerprint(summary)
    return extra or None


def _model_mc_extra(input_json: Mapping[str, Any]) -> Optional[dict]:
    """Upstream freeze / MC config fingerprints for validation.model_mc signatures."""
    extra: dict[str, Any] = {}
    mc_root = _resolve_monte_carlo_runs_root(input_json)
    if mc_root is not None and mc_root.is_dir():
        extra["monteCarloRunsRoot"] = str(mc_root)
        snapshot = mc_root / "queue" / "mc_config.json"
        if snapshot.is_file() or snapshot.is_symlink():
            try:
                target = snapshot.resolve() if snapshot.is_symlink() else snapshot
                extra["mcConfig"] = _file_content_fingerprint(target)
            except OSError:
                extra["mcConfig"] = "missing"
        prod = mc_root / "production"
        if prod.is_dir():
            fp = _directory_fingerprint(prod)
            if fp:
                extra["productionFingerprint"] = fp
    split_fp = _split_reuse_fingerprint(input_json)
    if split_fp:
        extra.update(split_fp)
    if input_json.get("requireArtifactReuse") is not None:
        extra["requireArtifactReuse"] = bool(input_json.get("requireArtifactReuse"))
    backends = input_json.get("backends")
    if backends is not None:
        extra["backends"] = backends
    return extra or None


def _select_best_model_extra(input_json: Mapping[str, Any]) -> Optional[dict]:
    model_mc = input_json.get("modelMcRoot")
    if not model_mc:
        return None
    root = Path(str(model_mc)).expanduser()
    if not root.is_dir():
        return {"modelMcRoot": str(root)}
    fp = _directory_fingerprint(root)
    return {
        "modelMcRoot": str(root.resolve()),
        "modelMcFingerprint": fp,
        "selectionMetric": input_json.get("selectionMetric"),
        "selectionStat": input_json.get("selectionStat"),
    }


def _methylgrapher_wgbs_extra(input_json: Mapping[str, Any]) -> Optional[dict]:
    """Fingerprint FASTQs + C2T/G2A assets + tool pins for WGBS pangenome CAAS."""
    resolved = dict(input_json.get("resolvedConfig") or {})
    extra: dict[str, Any] = {
        "forceRealign": bool(input_json.get("forceRealign")),
        "directional": resolved.get("directional"),
        "image": resolved.get("image") or resolved.get("image_digest"),
        "methylgrapher_version": resolved.get("methylgrapher_version"),
        "vg_version": resolved.get("vg_version"),
        "contexts": resolved.get("contexts"),
        "read_level": resolved.get("read_level"),
    }
    sample_dir = input_json.get("sampleDir")
    sample_id = input_json.get("sampleId")
    if sample_dir and sample_id:
        root = Path(str(sample_dir))
        for pattern in (
            f"{sample_id}*_R1*.fastq*",
            f"{sample_id}*_1.fastq*",
            f"{sample_id}*.fastq.gz",
        ):
            hits = sorted(root.glob(pattern))
            if hits:
                extra["fastqFingerprints"] = [
                    _file_content_fingerprint(p) for p in hits[:4]
                ]
                break
    try:
        from methyl_worker.methylgrapher_wgbs_runner import (
            fingerprint_wgbs_assets,
            resolve_wgbs_bundle_from_resolved,
        )

        if resolved:
            bundle = resolve_wgbs_bundle_from_resolved(resolved)
            extra["assetFingerprints"] = fingerprint_wgbs_assets(bundle)
    except Exception:
        logger.debug("methylGrapher asset fingerprint skipped", exc_info=True)
    return {k: v for k, v in extra.items() if v is not None} or None


def compute_input_signature(
    entry: ActionCatalogEntry,
    input_json: Mapping[str, Any],
    input_model: BaseModel,
) -> str:
    path_remap = _load_path_remap(input_json)
    payload: dict[str, Any] = {
        "action": entry.action_name,
        "input": _normalize_path_strings(
            input_model.model_dump(mode="json"), path_remap
        ),
    }
    action_slice = _load_action_config_slice(entry, input_json)
    if action_slice is not None:
        payload["action_config"] = _normalize_path_strings(action_slice, path_remap)
    if entry.action_name == "pipeline.centroid":
        extra = _incremental_centroid_extra(input_json)
        if extra:
            payload["incremental"] = _normalize_path_strings(extra, path_remap)
    if entry.action_name == "validation.stability":
        extra = _stability_mc_extra(input_json)
        if extra:
            payload["monteCarlo"] = _normalize_path_strings(extra, path_remap)
    if entry.action_name == "validation.model_mc":
        extra = _model_mc_extra(input_json)
        if extra:
            payload["modelMc"] = _normalize_path_strings(extra, path_remap)
    if entry.action_name == "validation.select_best_model":
        extra = _select_best_model_extra(input_json)
        if extra:
            payload["selectBest"] = _normalize_path_strings(extra, path_remap)
    if entry.action_name == "validation.post_model_validation":
        split_fp = _split_reuse_fingerprint(input_json)
        if split_fp:
            payload["splitReuse"] = split_fp
    if entry.action_name == "validation.plan_iterations":
        split_fp = _split_reuse_fingerprint(input_json)
        if split_fp:
            payload["splitReuse"] = split_fp
    if entry.action_name in (
        "sample.methylgrapher_wgbs_align",
        "sample.methylgrapher_wgbs_extract",
    ):
        extra = _methylgrapher_wgbs_extra(input_json)
        if extra:
            payload["methylgrapherWgbs"] = _normalize_path_strings(extra, path_remap)
    return _sha256_text(_canonical_json(payload))


def _resolve_monte_carlo_runs_root(input_json: Mapping[str, Any]) -> Optional[Path]:
    explicit = input_json.get("monteCarloRunsRoot")
    if explicit:
        return Path(str(explicit)).expanduser().resolve()
    project = input_json.get("projectPath") or input_json.get("project")
    if not project:
        return None
    try:
        from methyl_utils import load_project

        cfg = load_project(str(project))
        return Path(cfg.output_base) / cfg.project_name / "monte_carlo_runs"
    except Exception:
        return None


def resolve_action_output_dir(entry: ActionCatalogEntry, input_json: Mapping[str, Any]) -> Optional[Path]:
    explicit = input_json.get("outputDir")
    if explicit:
        return Path(str(explicit)).expanduser().resolve()

    # Sample-scoped align/prep bind sampleDir, not outputDir. Falling through to
    # projectPath (a study JSON) would treat configs/ as the product root; CAAS
    # commit would then move BAM/FASTQ/H5 out of /work/samples/{id}/ and relink
    # them under the study configs tree, breaking downstream QC and extract.
    sample_dir = input_json.get("sampleDir")
    if sample_dir:
        return Path(str(sample_dir)).expanduser().resolve()

    action = entry.action_name
    if action == "validation.stability":
        mc_root = _resolve_monte_carlo_runs_root(input_json)
        return mc_root / "stability" if mc_root else None

    if action == "validation.plan_iterations":
        return _resolve_monte_carlo_runs_root(input_json)

    if action == "validation.model_mc":
        prod = input_json.get("productionOutputDir")
        if prod:
            return Path(str(prod)).expanduser().resolve()
        mc_root = _resolve_monte_carlo_runs_root(input_json)
        return mc_root / "model_mc" if mc_root else None

    if action == "validation.select_best_model":
        model_mc = input_json.get("modelMcRoot")
        if model_mc:
            return Path(str(model_mc)).expanduser().resolve()
        mc_root = _resolve_monte_carlo_runs_root(input_json)
        return mc_root / "model_mc" if mc_root else None

    if action == "validation.post_model_validation":
        prod = input_json.get("productionOutputDir")
        if prod:
            return Path(str(prod)).expanduser().resolve()
        mc_root = _resolve_monte_carlo_runs_root(input_json)
        return mc_root / "post_model_validation" if mc_root else None

    if action == "validation.model_bundle":
        bundle = input_json.get("bundleDir")
        if bundle:
            return Path(str(bundle)).expanduser().resolve()

    if action in {
        "validation.stability_freeze_readiness",
        "validation.prepare_freeze_project",
    }:
        project = input_json.get("projectPath") or input_json.get("project")
        if not project:
            return None
        try:
            from methyl_utils import load_project

            cfg = load_project(str(project))
            root = Path(cfg.output_base) / cfg.project_name
            if action == "validation.prepare_freeze_project":
                prod = input_json.get("productionOutputDir")
                if prod:
                    return Path(str(prod)).expanduser().resolve()
                return root / "monte_carlo_runs" / "production"
            return root
        except Exception:
            return None

    run_dir = input_json.get("runDir")
    if run_dir:
        return Path(str(run_dir)).expanduser().resolve()

    target = input_json.get("targetRunDir")
    if target:
        return Path(str(target)).expanduser().resolve()

    project = input_json.get("projectPath") or input_json.get("project")
    if project:
        path = Path(str(project)).expanduser().resolve()
        if path.is_file():
            return path.parent
        return path

    return None


def _path_like_output_values(output_dict: Mapping[str, Any]) -> List[Path]:
    paths: List[Path] = []

    def _walk(value: Any) -> None:
        if isinstance(value, str):
            if value.startswith("/") or value.endswith(_PATH_SUFFIXES):
                p = Path(value)
                if p.is_file():
                    paths.append(p)
            return
        if isinstance(value, Mapping):
            for item in value.values():
                _walk(item)
            return
        if isinstance(value, list):
            for item in value:
                _walk(item)

    for val in output_dict.values():
        _walk(val)
    return paths


def _is_action_result_envelope(path: Path) -> bool:
    """Manifest JSON under .action_results/ — not a product artifact."""
    return path.parent.name == ".action_results" and path.suffix == ".json"


def _harvest_centroid_artifacts(output_dict: Mapping[str, Any]) -> List[Path]:
    """Ensure pipeline.centroid commits the HDF5 even when path checks race or miss."""
    paths: List[Path] = []
    h5 = output_dict.get("centroid_h5_path")
    if isinstance(h5, str) and h5:
        p = Path(h5)
        if p.is_file():
            paths.append(p)
    out_dir_raw = output_dict.get("output_dir") or output_dict.get("outputDir")
    if isinstance(out_dir_raw, str) and out_dir_raw:
        out_dir = Path(out_dir_raw)
        if out_dir.is_dir():
            for candidate in sorted(out_dir.glob("*.h5")) + sorted(out_dir.glob("*.hdf5")):
                if candidate.is_file():
                    paths.append(candidate)
    return paths


def artifacts_from_output(
    output_dict: Mapping[str, Any],
    *,
    action_name: Optional[str] = None,
) -> List[ArtifactRef]:
    refs: List[ArtifactRef] = []
    seen: set[str] = set()
    candidates = list(_path_like_output_values(output_dict))
    if action_name == "pipeline.centroid":
        candidates.extend(_harvest_centroid_artifacts(output_dict))
    for path in candidates:
        if _is_action_result_envelope(path):
            continue
        if not path.is_file():
            continue
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in seen:
            continue
        seen.add(key)
        refs.append(artifact_ref_for(path))
    return refs


def compute_output_signature(artifacts: List[ArtifactRef]) -> str:
    if not artifacts:
        return _sha256_text("")
    parts: List[str] = []
    for ref in sorted(artifacts, key=lambda r: r.path):
        fp = f"{ref.path}:{ref.bytes or 0}"
        if ref.sha256:
            fp += f":{ref.sha256}"
        else:
            try:
                st = Path(ref.path).stat()
                fp += f":{int(st.st_mtime)}"
            except OSError:
                fp += ":missing"
        parts.append(fp)
    return _sha256_text("\n".join(parts))


# Actions whose success is meaningless without an on-disk output artifact.
# An empty artifact list for these indicates a false-success manifest (the CLI
# reported exit 0 but wrote nothing at outputDir), which must not be replayed.
_ARTIFACT_REQUIRED_ACTIONS = frozenset({"pipeline.centroid"})


def _requires_output_artifacts(entry: ActionCatalogEntry) -> bool:
    return entry.action_name in _ARTIFACT_REQUIRED_ACTIONS


def verify_artifacts(artifacts: List[ArtifactRef]) -> bool:
    for ref in artifacts:
        path = Path(ref.path)
        if _is_action_result_envelope(path):
            continue
        if not path.is_file():
            return False
        if ref.bytes is not None:
            try:
                if path.stat().st_size != ref.bytes:
                    return False
            except OSError:
                return False
    return True


def _iteration_project_path_candidate(item: Mapping[str, Any]) -> Optional[Path]:
    """Prefer a readable per-run ``project.json`` without requiring CAAS blob paths."""
    candidates: List[Path] = []
    project_path = item.get("projectPath")
    if project_path:
        candidates.append(Path(str(project_path)))
    run_dir = item.get("runDir")
    if run_dir:
        candidates.append(Path(str(run_dir)) / "project.json")
    for candidate in candidates:
        # Exist as real file or live symlink (Path.is_file follows the leaf).
        if candidate.is_file():
            return candidate.parent.resolve() / candidate.name
    return None


def _heal_plan_iteration_product_paths(
    record: ActionExecutionRecord,
) -> ActionExecutionRecord:
    """Rewrite iteration projectPath values to live product paths after CAAS relink."""
    task_output = dict(record.task_output or {})
    iterations = task_output.get("iterations") or []
    if not iterations:
        return record
    healed: List[Any] = []
    changed = False
    for item in iterations:
        if not isinstance(item, Mapping):
            healed.append(item)
            continue
        row = dict(item)
        candidate = _iteration_project_path_candidate(row)
        if candidate is not None:
            new_path = str(candidate)
            if row.get("projectPath") != new_path:
                row["projectPath"] = new_path
                changed = True
            # Keep taskConfig.projectJson aligned when present.
            task_config = row.get("taskConfig")
            if isinstance(task_config, Mapping):
                tc = dict(task_config)
                if tc.get("projectJson") != new_path:
                    tc["projectJson"] = new_path
                    row["taskConfig"] = tc
                    changed = True
        healed.append(row)
    if not changed:
        return record
    task_output["iterations"] = healed
    return record.model_copy(update={"task_output": task_output})


def _plan_iteration_project_paths_ready(record: ActionExecutionRecord) -> bool:
    """True when every planned iteration's ``projectPath`` exists on disk.

    CAAS manifests can retain artifact blobs while ``task_output.iterations[].projectPath``
    still points at a stolen/deleted sibling content-key path. After relink we heal to
    ``runDir/project.json`` when possible; only refuse skip when no readable project
    remains for an iteration.
    """
    iterations = (record.task_output or {}).get("iterations") or []
    if not iterations:
        return False
    for item in iterations:
        if not isinstance(item, Mapping):
            return False
        if _iteration_project_path_candidate(item) is None:
            return False
    return True


def _force_rerun_requested(input_json: Mapping[str, Any]) -> bool:
    if input_json.get("forceRerun") is True:
        return True
    if os.environ.get("METHYL_FORCE_RERUN", "").strip().lower() in {"1", "true", "yes"}:
        return True
    return False


def _read_manifest(manifest_path: Path) -> Optional[ActionExecutionRecord]:
    if not manifest_path.is_file():
        return None
    try:
        return read_action_result(manifest_path, ActionExecutionRecord)
    except Exception:
        logger.debug("could not read action manifest %s", manifest_path, exc_info=True)
        return None


def compute_content_key(action_revision: str, input_signature: str) -> str:
    """Content-addressed key for one action result (cumulative via input_signature)."""
    return _sha256_text(f"{action_revision}|{input_signature}")


def _hyperparam_set_id(input_json: Mapping[str, Any]) -> Optional[str]:
    # Canonical wf field is executionScopeId; hyperparamSetId is the legacy alias.
    value = input_json.get("executionScopeId") or input_json.get("hyperparamSetId")
    return str(value) if value else None


_DOWNLOAD_ACTIONS = frozenset({"sample.download_fastq", "sample.download_msdata"})


def _ensure_download_fastq_arm_links(
    entry: ActionCatalogEntry,
    input_json: Mapping[str, Any],
) -> bool:
    """Re-link restored root FASTQs into arm sampleDir after skip/replay.

    CAAS harvest records canonical paths at sampleRoot. Align discovers pairs
    only under sampleDir. After delete_fastqs, skip must recreate the arm links
    that the live download handler writes.
    """
    if entry.action_name not in _DOWNLOAD_ACTIONS:
        return True
    sample_dir = input_json.get("sampleDir")
    sample_id = input_json.get("sampleId")
    if not sample_dir or not sample_id:
        return True
    try:
        from methyl_domain.sample_content_store import restore_sample_fastq_products

        restore_sample_fastq_products(str(sample_dir), str(sample_id))
    except Exception:
        logger.debug(
            "CAAS FASTQ restore skipped for %s", sample_dir, exc_info=True
        )
    from methyl_utils.sample_arm_layout import ensure_sample_dir_fastq_links

    linked = ensure_sample_dir_fastq_links(
        str(sample_dir),
        sample_id=str(sample_id),
        sample_root=input_json.get("sampleRoot"),
    )
    if linked:
        return True
    logger.info(
        "Not skipping %s: no FASTQs under sampleDir after restore (%s)",
        entry.action_name,
        sample_dir,
    )
    return False


def _maybe_replay_from_caas(
    entry: ActionCatalogEntry,
    input_json: Mapping[str, Any],
    *,
    content_key: str,
    output_dir: Path,
    manifest_path: Path,
    input_model: BaseModel,
) -> Optional[ActionExecutionResult]:
    project_root = resolve_caas_root(input_json, action_name=entry.action_name)
    if project_root is None:
        return None

    record = read_caas_entry(project_root, entry.action_name, content_key)
    if record is None or record.result_code != 0:
        return None

    current_revision = compute_action_revision(entry)
    current_input_sig = compute_input_signature(entry, dict(input_json), input_model)
    if record.action_revision != current_revision:
        return None
    if record.input_signature != current_input_sig:
        return None
    if not verify_entry_artifacts(record):
        return None
    if record.output_signature != compute_output_signature(record.artifacts):
        return None

    product_artifacts = [
        a for a in record.artifacts if not _is_action_result_envelope(Path(a.path))
    ]
    if not product_artifacts and _requires_output_artifacts(entry):
        return None

    linked = link_entry_into_place(
        project_root,
        entry.action_name,
        content_key,
        output_dir=output_dir,
    )
    if linked is None:
        return None

    if not _ensure_download_fastq_arm_links(entry, input_json):
        return None

    if entry.action_name == "validation.plan_iterations":
        linked = _heal_plan_iteration_product_paths(linked)
        if not _plan_iteration_project_paths_ready(linked):
            logger.info(
                "Not skipping %s: CAAS content_key %s has missing iteration projectPath files",
                entry.action_name,
                content_key[:12],
            )
            return None

    try:
        action_results_dir(output_dir).mkdir(parents=True, exist_ok=True)
        atomic_write_action_result(manifest_path, linked)
    except Exception:
        logger.debug("local manifest mirror failed for %s", entry.action_name, exc_info=True)

    hyperparam_set_id = _hyperparam_set_id(input_json)
    if hyperparam_set_id:
        from .collectors import _run_key

        append_instance_ledger(
            project_root,
            hyperparam_set_id,
            action_name=entry.action_name,
            run_key=_run_key(input_json),
            content_key=content_key,
        )

    logger.info(
        "Skipping %s (CAAS content_key %s, manifest %s)",
        entry.action_name,
        content_key[:12],
        manifest_path,
    )
    return _replay_result(entry, linked, manifest_path=manifest_path)


def _enrich_prepare_freeze_replay_paths(task_output: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure CAAS-skipped prepare_freeze still binds production centroid/detect dirs."""
    out = dict(task_output or {})
    project_path = out.get("projectPath") or out.get("productionProject")
    if not project_path:
        return out
    if out.get("centroid1Dir") and out.get("centroid2Dir") and out.get("detectOutDir"):
        return out
    try:
        from methyl_utils import load_project

        from .handler_helpers import production_centroid_detect_dirs

        prod_cfg = load_project(str(project_path))
        c1, c2, det = production_centroid_detect_dirs(prod_cfg)
        if c1 and not out.get("centroid1Dir"):
            out["centroid1Dir"] = c1
        if c2 and not out.get("centroid2Dir"):
            out["centroid2Dir"] = c2
        if det and not out.get("detectOutDir"):
            out["detectOutDir"] = det
    except Exception:
        logger.debug("prepare_freeze replay path enrichment failed", exc_info=True)
    return out


def _replay_result(
    entry: ActionCatalogEntry,
    record: ActionExecutionRecord,
    *,
    manifest_path: Path,
) -> ActionExecutionResult:
    output_model = load_output_model(entry)
    merged = dict(record.task_output)
    if entry.action_name == "validation.prepare_freeze_project":
        merged = _enrich_prepare_freeze_replay_paths(merged)
    merged.setdefault("action_name", entry.action_name)
    merged.setdefault("capability", entry.capability)
    merged["status"] = "skipped"
    merged["manifest_path"] = str(manifest_path)
    merged["started_at_utc"] = utc_now()
    merged["finished_at_utc"] = utc_now()
    merged["duration_ms"] = 0
    if "artifacts" not in merged:
        merged["artifacts"] = [a.model_dump(mode="json") for a in record.artifacts]
    output = output_model.model_validate(merged)
    return execution_result_from_output(output)


def maybe_skip_action(
    entry: ActionCatalogEntry,
    input_json: Mapping[str, Any],
) -> Optional[ActionExecutionResult]:
    if not idempotency_enabled_for(entry, input_json):
        return None
    if _force_rerun_requested(input_json):
        return None

    output_dir = resolve_action_output_dir(entry, input_json)
    if output_dir is None:
        return None

    run_key = _run_key(input_json)
    manifest_path = manifest_path_for(output_dir, entry.action_name, run_key)

    task_input = strip_runtime_input(dict(input_json))
    try:
        input_model = validate_input(entry, task_input)
    except Exception:
        return None

    current_revision = compute_action_revision(entry)
    current_input_sig = compute_input_signature(entry, dict(input_json), input_model)
    content_key = compute_content_key(current_revision, current_input_sig)

    if caas_enabled(input_json):
        caas_skip = _maybe_replay_from_caas(
            entry,
            input_json,
            content_key=content_key,
            output_dir=output_dir,
            manifest_path=manifest_path,
            input_model=input_model,
        )
        if caas_skip is not None:
            return caas_skip

    record = _read_manifest(manifest_path)
    if record is None or record.result_code != 0:
        return None

    if record.action_revision != current_revision:
        return None
    if record.input_signature != current_input_sig:
        return None
    if not verify_artifacts(record.artifacts):
        return None
    if record.output_signature != compute_output_signature(record.artifacts):
        return None
    product_artifacts = [
        a for a in record.artifacts if not _is_action_result_envelope(Path(a.path))
    ]
    if not product_artifacts and _requires_output_artifacts(entry):
        logger.info(
            "Not skipping %s: manifest %s recorded zero output artifacts (cannot verify success)",
            entry.action_name,
            manifest_path,
        )
        return None

    if entry.action_name == "validation.plan_iterations":
        if not _plan_iteration_project_paths_ready(record):
            logger.info(
                "Not skipping %s: iteration projectPath files missing (manifest %s)",
                entry.action_name,
                manifest_path,
            )
            return None
        if output_dir is not None:
            try:
                from methyl_validation.project_gen import monte_carlo_runs_have_legacy_projects

                if monte_carlo_runs_have_legacy_projects(output_dir):
                    logger.info(
                        "Not skipping %s: legacy step_config found under %s",
                        entry.action_name,
                        output_dir,
                    )
                    return None
            except Exception:
                logger.debug("legacy MC run scan failed for %s", output_dir, exc_info=True)

    if not _ensure_download_fastq_arm_links(entry, input_json):
        return None

    hyperparam_set_id = _hyperparam_set_id(input_json)
    if hyperparam_set_id and caas_enabled(input_json):
        project_root = resolve_caas_root(input_json, action_name=entry.action_name)
        if project_root is not None:
            append_instance_ledger(
                project_root,
                hyperparam_set_id,
                action_name=entry.action_name,
                run_key=run_key,
                content_key=content_key,
            )

    logger.info(
        "Skipping %s (signature match, manifest %s)",
        entry.action_name,
        manifest_path,
    )
    return _replay_result(entry, record, manifest_path=manifest_path)


def record_action_execution(
    entry: ActionCatalogEntry,
    input_json: Mapping[str, Any],
    input_model: BaseModel,
    result: ActionExecutionResult,
    *,
    skipped: bool = False,
    skip_reason: Optional[str] = None,
) -> None:
    if not idempotency_enabled_for(entry, input_json):
        return

    output_dir = resolve_action_output_dir(entry, input_json)
    if output_dir is None:
        return

    run_key = _run_key(input_json)
    manifest_path = manifest_path_for(output_dir, entry.action_name, run_key)
    output_dict = result.output.model_dump(mode="json")
    task_input = strip_runtime_input(dict(input_json))

    telemetry_keys = {
        "schema_version",
        "action_name",
        "capability",
        "started_at_utc",
        "finished_at_utc",
        "duration_ms",
        "result_code",
        "exit_code",
        "manifest_path",
        "artifacts",
        "status",
    }
    task_output = {k: v for k, v in output_dict.items() if k not in telemetry_keys}

    started = output_dict.get("started_at_utc") or utc_now()
    finished = output_dict.get("finished_at_utc") or utc_now()
    if isinstance(started, str):
        started = datetime.fromisoformat(started.replace("Z", "+00:00"))
    if isinstance(finished, str):
        finished = datetime.fromisoformat(finished.replace("Z", "+00:00"))

    artifacts = artifacts_from_output(output_dict, action_name=entry.action_name)
    if not artifacts and output_dict.get("artifacts"):
        artifacts = [ArtifactRef.model_validate(a) for a in output_dict["artifacts"]]

    action_revision = compute_action_revision(entry)
    input_signature = compute_input_signature(entry, dict(input_json), input_model)
    content_key = compute_content_key(action_revision, input_signature)
    hyperparam_set_id = _hyperparam_set_id(input_json)

    record = ActionExecutionRecord(
        action_name=entry.action_name,
        capability=entry.capability,
        started_at_utc=started,
        finished_at_utc=finished,
        duration_ms=int(output_dict.get("duration_ms") or 0),
        result_code=result.result_code,
        exit_code=int(output_dict.get("exit_code") or 0),
        manifest_path=str(manifest_path),
        artifacts=artifacts,
        action_revision=action_revision,
        input_signature=input_signature,
        output_signature=compute_output_signature(artifacts),
        content_key=content_key,
        hyperparam_set_id=hyperparam_set_id,
        skipped=skipped,
        skip_reason=skip_reason,
        task_output=task_output,
    )

    if (
        caas_enabled(input_json)
        and result.result_code == 0
        and not skipped
    ):
        project_root = resolve_caas_root(input_json, action_name=entry.action_name)
        if project_root is not None:
            try:
                record = commit_artifacts_to_store(
                    project_root,
                    entry.action_name,
                    content_key,
                    record,
                    output_dir=output_dir,
                )
                record = record.model_copy(update={"manifest_path": str(manifest_path)})
            except Exception:
                logger.warning(
                    "CAAS commit failed for %s",
                    entry.action_name,
                    exc_info=True,
                )
            if hyperparam_set_id:
                try:
                    append_instance_ledger(
                        project_root,
                        hyperparam_set_id,
                        action_name=entry.action_name,
                        run_key=run_key,
                        content_key=content_key,
                    )
                except Exception:
                    logger.debug(
                        "CAAS ledger append failed for %s",
                        entry.action_name,
                        exc_info=True,
                    )

    try:
        action_results_dir(output_dir).mkdir(parents=True, exist_ok=True)
        atomic_write_action_result(manifest_path, record)
    except Exception:
        logger.debug("action manifest write failed for %s", entry.action_name, exc_info=True)

