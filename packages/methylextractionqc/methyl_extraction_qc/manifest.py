"""Load MethylExtractor extraction manifest and context sidecars."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


def manifest_path(sample_dir: Path, sample_id: str) -> Path:
    return sample_dir / f"{sample_id}.extraction_manifest.json"


def load_extraction_manifest(sample_dir: Path, sample_id: str) -> Dict[str, Any]:
    path = manifest_path(sample_dir, sample_id)
    if not path.is_file():
        raise FileNotFoundError(f"Extraction manifest not found: {path}")
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Extraction manifest must be a JSON object: {path}")
    return payload


def load_context_qc_sidecar(sample_dir: Path, chromosome: str, context: str) -> Optional[Dict[str, Any]]:
    path = sample_dir / f"{chromosome}-{context}.json"
    if not path.is_file():
        return None
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else None


def contexts_extracted(manifest: Dict[str, Any]) -> list[str]:
    metadata = manifest.get("metadata") or {}
    raw = metadata.get("contexts_extracted") or []
    if not isinstance(raw, list):
        return ["CG"]
    return [str(item) for item in raw if str(item).strip()]
