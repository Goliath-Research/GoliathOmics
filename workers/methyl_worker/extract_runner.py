"""MethylExtractor BAM → per-chromosome HDF5 via native CLI."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from methyl_worker.work_share import (
    append_work_text,
    ensure_work_writable,
    replace_work_file,
    share_work_tree,
    write_work_text,
)

logger = logging.getLogger(__name__)

DEFAULT_EXTRACT_CONTEXTS: tuple[str, ...] = ("CG", "CHG", "CHH")
ALLOWED_CONTEXTS = frozenset({"CG", "CHG", "CHH"})


@dataclass(frozen=True)
class MethylExtractConfig:
    sample_id: str
    sample_dir: Path
    project_path: Path
    chromosomes: tuple[str, ...]
    extract_contexts: tuple[str, ...]
    reference_fasta: Path
    chrom_mapping: Path
    extractor_bin: str
    threads: Optional[int]
    min_mapq: Optional[int]
    min_phred: Optional[int]
    min_cov: Optional[int]
    cap_cov: Optional[int]
    compression: Optional[int]
    chunk_size: Optional[int]
    output_format: str
    split: bool
    read_level: bool
    tile_size: Optional[int]
    mhap: bool = False
    target_panel_bed: Optional[Path] = None
    chrom_parallel: Optional[int] = None
    max_rss_gb: Optional[int] = None


@dataclass(frozen=True)
class MethylExtractPaths:
    sample_dir: Path
    sample_id: str
    bam_path: Path
    log_path: Path


def _normalize_contexts(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return DEFAULT_EXTRACT_CONTEXTS
    if isinstance(raw, str):
        parts = [p.strip().upper() for p in raw.replace(",", " ").split() if p.strip()]
    elif isinstance(raw, (list, tuple)):
        parts = [str(p).strip().upper() for p in raw if str(p).strip()]
    else:
        raise RuntimeError(f"extract_contexts must be a list of CG/CHG/CHH, got {raw!r}")
    if not parts:
        return DEFAULT_EXTRACT_CONTEXTS
    invalid = [p for p in parts if p not in ALLOWED_CONTEXTS]
    if invalid:
        raise RuntimeError(f"Unsupported extract contexts: {invalid}")
    # Preserve order while deduplicating; CG is always implicit in MethylExtractor.
    seen: set[str] = set()
    ordered: List[str] = []
    for ctx in parts:
        if ctx not in seen:
            seen.add(ctx)
            ordered.append(ctx)
    return tuple(ordered)


def _pick(
    input_json: Mapping[str, Any],
    step_cfg: Mapping[str, Any],
    input_key: str,
    step_key: str,
) -> Any:
    if input_key in input_json and input_json[input_key] is not None:
        return input_json[input_key]
    return step_cfg.get(step_key)


def _pick_int(
    input_json: Mapping[str, Any],
    step_cfg: Mapping[str, Any],
    input_key: str,
    step_key: str,
) -> Optional[int]:
    value = _pick(input_json, step_cfg, input_key, step_key)
    if value is None:
        return None
    return int(value)


def _pick_bool(
    input_json: Mapping[str, Any],
    step_cfg: Mapping[str, Any],
    input_key: str,
    step_key: str,
    default: bool,
) -> bool:
    value = _pick(input_json, step_cfg, input_key, step_key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no"}
    return bool(value)


def _contig_triplet(
    chrom: str,
    *,
    contig_naming: str,
    overrides: Mapping[str, Any],
) -> tuple[str, str, str]:
    """Return (name, bam, fasta) for one project chromosome."""
    override = overrides.get(chrom) if isinstance(overrides, dict) else None
    if isinstance(override, dict):
        name = str(override.get("name") or chrom)
        bam = str(override.get("bam") or name)
        fasta = str(override.get("fasta") or bam)
        return name, bam, fasta
    naming = (contig_naming or "ensembl").strip().lower()
    if naming == "ucsc_chr":
        contig = chrom if chrom.startswith("chr") else f"chr{chrom}"
        return chrom, contig, contig
    if naming not in {"ensembl", "custom"}:
        raise RuntimeError(f"unsupported contig_naming: {contig_naming!r}")
    return chrom, chrom, chrom


def _derive_chrom_mapping_object(
    chromosomes: Sequence[str],
    reference: str | Path,
    step_cfg: Mapping[str, Any],
) -> Dict[str, Any]:
    contig_naming = str(step_cfg.get("contig_naming") or "ensembl")
    overrides = step_cfg.get("chromosome_overrides") or {}
    rows: List[Dict[str, Any]] = []
    for chrom in chromosomes:
        name, bam, fasta = _contig_triplet(
            str(chrom),
            contig_naming=contig_naming,
            overrides=overrides,
        )
        rows.append({"name": name, "bam": bam, "fasta": fasta, "extract": True})
    return {"reference": str(reference), "chromosomes": rows}


def _validate_chrom_mapping(mapping: Mapping[str, Any], chromosomes: Sequence[str]) -> None:
    entries = mapping.get("chromosomes")
    if not isinstance(entries, list) or not entries:
        raise RuntimeError("chrom_mapping.chromosomes must be a non-empty list")
    extract_names = {str(e.get("name")) for e in entries if e.get("extract", True)}
    expected = {str(c) for c in chromosomes}
    if extract_names != expected:
        missing = sorted(expected - extract_names)
        extra = sorted(extract_names - expected)
        parts: List[str] = []
        if missing:
            parts.append(f"missing in mapping: {missing}")
        if extra:
            parts.append(f"unexpected in mapping: {extra}")
        raise RuntimeError(
            "chrom_mapping extract names must match project.chromosomes "
            f"({'; '.join(parts)})"
        )


def _materialize_chrom_mapping(
    sample_dir: Path,
    chrom_mapping_raw: Any,
    chromosomes: Sequence[str],
    reference_fasta: Path,
    step_cfg: Mapping[str, Any],
) -> Path:
    if isinstance(chrom_mapping_raw, dict):
        mapping = dict(chrom_mapping_raw)
        _validate_chrom_mapping(mapping, chromosomes)
        out = sample_dir / ".chrom_mapping.json"
        ensure_work_writable(out)
        write_work_text(out, json.dumps(mapping, indent=2) + "\n")
        return out.resolve()

    if chrom_mapping_raw is None or (
        isinstance(chrom_mapping_raw, str) and not str(chrom_mapping_raw).strip()
    ):
        mapping = _derive_chrom_mapping_object(chromosomes, reference_fasta, step_cfg)
        out = sample_dir / ".chrom_mapping.json"
        ensure_work_writable(out)
        write_work_text(out, json.dumps(mapping, indent=2) + "\n")
        return out.resolve()

    path = Path(str(chrom_mapping_raw)).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"chrom_mapping file not found: {path}")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise RuntimeError(f"chrom_mapping file must contain a JSON object: {path}")
    _validate_chrom_mapping(loaded, chromosomes)
    return path


def resolve_methyl_extract_config(
    project_path: str | Path,
    input_json: Mapping[str, Any],
) -> MethylExtractConfig:
    """Resolve production config from project JSON on shared storage + task input_json."""
    from methyl_utils import load_project

    sample_id = str(input_json.get("sampleId") or "").strip()
    sample_dir_raw = input_json.get("sampleDir")
    if not sample_id or not sample_dir_raw:
        raise RuntimeError("sample.methyl_extract requires sampleId and sampleDir")

    project_file = Path(str(project_path)).expanduser().resolve()
    if not project_file.is_file():
        raise RuntimeError(f"project file not found: {project_file}")

    project = load_project(str(project_file))
    chromosomes = tuple(project.chromosomes or [])
    if not chromosomes:
        raise RuntimeError(f"project.chromosomes is required for methyl extract: {project_file}")

    from methyl_utils.action_config_resolver import resolve_from_task_input

    regulatory = project.get_regulatory_config()
    step_cfg: Dict[str, Any] = dict(
        resolve_from_task_input("methyl_extract", input_json, regulatory=regulatory)
    )
    alignment_cfg = resolve_from_task_input("alignment_qc", input_json, regulatory=regulatory)

    reference_raw = step_cfg.get("reference_fasta") or alignment_cfg.get("genome_fasta")
    if not reference_raw:
        raise RuntimeError(
            "reference genome is required in site reference_genome.fasta or "
            "profile/site actionConfig.alignment_qc / methyl_extract"
        )
    reference_fasta = Path(str(reference_raw)).expanduser().resolve()
    if not reference_fasta.is_file():
        raise RuntimeError(f"reference FASTA not found: {reference_fasta}")

    sample_dir = Path(str(sample_dir_raw)).expanduser().resolve()
    chrom_mapping_raw = step_cfg.get("chrom_mapping")
    chrom_mapping = _materialize_chrom_mapping(
        sample_dir,
        chrom_mapping_raw,
        chromosomes,
        reference_fasta,
        step_cfg,
    )

    extract_contexts_raw = step_cfg.get("extract_contexts")
    if extract_contexts_raw is None:
        raise RuntimeError("extract_contexts must be set in profile/site actionConfig.methyl_extract")
    extract_contexts = _normalize_contexts(extract_contexts_raw)

    extractor_bin = str(step_cfg.get("extractor_bin") or "MethylExtractor").strip()
    read_level, tile_size = _resolve_read_level(step_cfg)
    mhap = _resolve_mhap(step_cfg)

    panel_raw = (
        step_cfg.get("target_panel_bed")
        or step_cfg.get("regions_bed")
        or input_json.get("targetPanelBed")
    )
    target_panel_bed: Optional[Path] = None
    if panel_raw not in (None, "", False):
        target_panel_bed = Path(str(panel_raw)).expanduser().resolve()
        if not target_panel_bed.is_file():
            raise RuntimeError(
                f"methyl_extract.target_panel_bed not found: {target_panel_bed} "
                "(required for EM-Seq / hybrid-capture restricted extract)"
            )

    return MethylExtractConfig(
        sample_id=sample_id,
        sample_dir=Path(str(sample_dir_raw)).expanduser().resolve(),
        project_path=project_file,
        chromosomes=chromosomes,
        extract_contexts=extract_contexts,
        reference_fasta=reference_fasta,
        chrom_mapping=chrom_mapping,
        extractor_bin=extractor_bin,
        threads=step_cfg.get("threads") if step_cfg.get("threads") is not None else None,
        min_mapq=step_cfg.get("min_mapq"),
        min_phred=step_cfg.get("min_phred"),
        min_cov=step_cfg.get("min_cov"),
        cap_cov=step_cfg.get("cap_cov"),
        compression=step_cfg.get("compression"),
        chunk_size=step_cfg.get("chunk_size"),
        output_format=str(step_cfg.get("output_format") or "hdf5"),
        split=bool(step_cfg.get("split", True)),
        read_level=read_level,
        tile_size=tile_size,
        mhap=mhap,
        target_panel_bed=target_panel_bed,
        chrom_parallel=int(step_cfg["chrom_parallel"]) if step_cfg.get("chrom_parallel") is not None else None,
        max_rss_gb=int(step_cfg["max_rss_gb"]) if step_cfg.get("max_rss_gb") is not None else None,
    )


def _resolve_read_level(step_cfg: Mapping[str, Any]) -> tuple[bool, Optional[int]]:
    """Parse methyl_extract.read_level (bool or {enabled, tile_size})."""
    raw = step_cfg.get("read_level")
    tile_size = step_cfg.get("tile_size")
    if isinstance(raw, dict):
        enabled = bool(raw.get("enabled", False))
        if raw.get("tile_size") is not None:
            tile_size = raw.get("tile_size")
        return enabled, int(tile_size) if tile_size is not None else None
    if isinstance(raw, bool):
        return raw, int(tile_size) if tile_size is not None else None
    if raw is not None:
        enabled = str(raw).strip().lower() not in {"0", "false", "no"}
        return enabled, int(tile_size) if tile_size is not None else None
    return False, int(tile_size) if tile_size is not None else None


def _resolve_mhap(step_cfg: Mapping[str, Any]) -> bool:
    raw = step_cfg.get("mhap")
    if isinstance(raw, dict):
        return bool(raw.get("enabled", False))
    if isinstance(raw, bool):
        return raw
    if raw is not None:
        return str(raw).strip().lower() not in {"0", "false", "no"}
    return False


def expected_pattern_h5_files(
    chromosomes: Sequence[str],
    extract_contexts: Sequence[str],
) -> List[str]:
    return [f"{chrom}-{ctx}.patterns.h5" for chrom in chromosomes for ctx in extract_contexts]


def extract_pattern_outputs_complete(
    sample_dir: Path,
    expected_names: Sequence[str],
) -> bool:
    if not expected_names:
        return True
    return all((sample_dir / name).is_file() for name in expected_names)


def expected_h5_files(chromosomes: Sequence[str], extract_contexts: Sequence[str]) -> List[str]:
    return [f"{chrom}-{ctx}.h5" for chrom in chromosomes for ctx in extract_contexts]


def extract_outputs_complete(sample_dir: Path, expected_names: Sequence[str]) -> bool:
    if not expected_names:
        return False
    return all((sample_dir / name).is_file() for name in expected_names)


def _load_context_stat_json(sample_dir: Path, chrom: str, context: str) -> Optional[Dict[str, Any]]:
    """Load MethylExtractor ``{chrom}-{context}.json`` sidecar stats when present."""
    path = sample_dir / f"{chrom}-{context}.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _normalize_chrom_key(chrom: str) -> str:
    return str(chrom).lstrip("chr")


def _extraction_manifest_path(sample_dir: Path, sample_id: str) -> Path:
    return sample_dir / f"{sample_id}.extraction_manifest.json"


def _load_existing_extraction_manifest(
    sample_dir: Path,
    sample_id: str,
) -> Optional[Dict[str, Any]]:
    path = _extraction_manifest_path(sample_dir, sample_id)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _is_complete_native_extraction_manifest(
    manifest: Mapping[str, Any],
    chromosomes: Sequence[str],
) -> bool:
    """True when the file is a complete ``methylextractor.extraction_manifest``.

    Completeness: schema name matches, every expected chromosome is present in
    ``per_chromosome``, and ``summary.cpg_weighted_mean_coverage`` is set.
    Stubs (e.g. chr21-only ``WORKER_STUB_EXTERNAL`` leftovers) fail this check.
    """
    metadata = manifest.get("metadata")
    if not isinstance(metadata, dict):
        return False
    if metadata.get("schema_name") != "methylextractor.extraction_manifest":
        return False
    summary = manifest.get("summary")
    if not isinstance(summary, dict):
        return False
    if summary.get("cpg_weighted_mean_coverage") is None:
        return False
    per_chromosome = manifest.get("per_chromosome")
    if not isinstance(per_chromosome, dict):
        return False
    present = {
        _normalize_chrom_key(key)
        for key, entry in per_chromosome.items()
        if isinstance(entry, dict)
    }
    expected = {_normalize_chrom_key(chrom) for chrom in chromosomes}
    return bool(expected) and expected <= present


def _overlay_worker_owned_manifest_fields(
    manifest: Dict[str, Any],
    *,
    sample_id: str,
    h5_files: Sequence[str],
    pattern_files: Sequence[str] | None,
) -> Dict[str, Any]:
    """Fill worker-owned lists and action provenance when the native file omits them."""
    metadata = manifest.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        manifest["metadata"] = metadata
    if not metadata.get("action"):
        metadata["action"] = "sample.methyl_extract"
    if not metadata.get("extractor"):
        metadata["extractor"] = "MethylExtractor"
    if not metadata.get("sample_id"):
        metadata["sample_id"] = sample_id
    if "h5_files" not in manifest:
        manifest["h5_files"] = list(h5_files)
    if "pattern_files" not in manifest:
        manifest["pattern_files"] = list(pattern_files or [])
    return manifest


def build_canonical_extraction_manifest_from_stats(
    *,
    sample_id: str,
    sample_dir: Path,
    chromosomes: Sequence[str],
    contexts: Sequence[str],
    h5_files: Sequence[str],
    pattern_files: Sequence[str] | None = None,
) -> Dict[str, Any]:
    """Build ``methyl_extraction_qc`` canonical manifest from MethylExtractor JSON sidecars.

    MethylExtractor writes per-chromosome ``{chrom}-{ctx}.json`` sidecars
    (``avg_coverage``, ``avg_methylation_level``, …), HDF5s, and a native
    ``{sampleId}.extraction_manifest.json`` (including ``read_filtering``).
    This helper synthesizes the sample manifest from sidecars when that native
    file is missing, a ``WORKER_STUB_EXTERNAL`` leftover, or incomplete (e.g.
    chr21-only). Prefer :func:`ensure_extraction_manifest`, which preserves a
    complete native file and only falls back to this synthesis.
    """
    contexts_list = [str(c) for c in contexts] or ["CG"]
    per_chromosome: Dict[str, Dict[str, Any]] = {}
    weighted_cov_num = 0.0
    weighted_cov_den = 0.0
    weighted_meth_num = 0.0
    weighted_meth_den = 0.0
    ctx_meth_totals: Dict[str, List[float]] = {c: [0.0, 0.0] for c in contexts_list}
    missing: List[str] = []

    for chrom in chromosomes:
        chrom_key = _normalize_chrom_key(chrom)
        chrom_entry: Dict[str, Any] = {}
        for ctx in contexts_list:
            stats = _load_context_stat_json(sample_dir, chrom_key, ctx)
            if stats is None:
                missing.append(f"{chrom_key}-{ctx}.json")
                continue
            num_positions = int(stats.get("num_positions") or 0)
            mean_coverage = stats.get("avg_coverage")
            meth_level = stats.get("avg_methylation_level")
            total_m = float(stats.get("total_methylated") or 0.0)
            total_u = float(stats.get("total_unmethylated") or 0.0)
            chrom_entry[ctx] = {
                "num_positions": num_positions,
                "methylation_level": meth_level,
                "mean_coverage": mean_coverage,
            }
            if ctx == "CG" and num_positions and mean_coverage is not None:
                weighted_cov_num += float(mean_coverage) * float(num_positions)
                weighted_cov_den += float(num_positions)
                total_cov = total_m + total_u
                if meth_level is not None and total_cov > 0:
                    weighted_meth_num += float(meth_level) * total_cov
                    weighted_meth_den += total_cov
            ctx_meth_totals[ctx][0] += total_m
            ctx_meth_totals[ctx][1] += total_m + total_u
        if chrom_entry:
            per_chromosome[chrom_key] = chrom_entry

    if missing:
        raise RuntimeError(
            "MethylExtractor per-chromosome stats incomplete for extraction manifest; "
            f"missing {len(missing)} sidecar(s) under {sample_dir}: "
            + ", ".join(missing[:12])
            + ("…" if len(missing) > 12 else "")
        )

    cpg_weighted_mean_coverage = (
        weighted_cov_num / weighted_cov_den if weighted_cov_den > 0 else 0.0
    )
    cpg_methylation_level = (
        weighted_meth_num / weighted_meth_den if weighted_meth_den > 0 else None
    )
    summary: Dict[str, Any] = {
        "cpg_weighted_mean_coverage": cpg_weighted_mean_coverage,
        "cpg_methylation_level": cpg_methylation_level,
        "n_chromosomes": len(per_chromosome),
        "n_h5_files": len(h5_files),
        "n_pattern_files": len(pattern_files or []),
    }
    for ctx in contexts_list:
        if ctx == "CG":
            continue
        meth_num, meth_den = ctx_meth_totals[ctx]
        summary[f"{ctx.lower()}_methylation_level"] = (
            (meth_num / meth_den) if meth_den > 0 else None
        )

    return {
        "metadata": {
            "schema_name": "methylextractor.extraction_manifest",
            "schema_version": "1.0.0",
            "sample_id": sample_id,
            "contexts_extracted": contexts_list,
            "extractor": "MethylExtractor",
            "action": "sample.methyl_extract",
        },
        "summary": summary,
        "per_chromosome": per_chromosome,
    }


def write_extraction_manifest(
    sample_dir: Path,
    sample_id: str,
    manifest: Mapping[str, Any],
) -> Path:
    """Atomically write ``{sampleId}.extraction_manifest.json`` (overwrites stubs)."""
    path = _extraction_manifest_path(sample_dir, sample_id)
    tmp = path.with_suffix(path.suffix + ".tmp")
    ensure_work_writable(path)
    write_work_text(tmp, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    replace_work_file(tmp, path)
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"Extraction manifest not written: {path}")
    return path


def ensure_extraction_manifest(
    *,
    sample_id: str,
    sample_dir: Path,
    chromosomes: Sequence[str],
    contexts: Sequence[str],
    h5_files: Sequence[str],
    pattern_files: Sequence[str] | None = None,
) -> Path:
    """Preserve a complete native MethylExtractor manifest, or synthesize one.

    When ``{sampleId}.extraction_manifest.json`` is a complete
    ``methylextractor.extraction_manifest`` (expected chromosomes in
    ``per_chromosome``, ``summary.cpg_weighted_mean_coverage`` set), keep native
    ``read_filtering``, ``summary.cpg_fraction_sites_covered``, and metadata, and
    overlay worker-owned ``h5_files`` / ``pattern_files`` / action provenance if
    missing. Otherwise synthesize from per-context JSON sidecars so extraction QC
    does not see a stale stub.
    """
    existing = _load_existing_extraction_manifest(sample_dir, sample_id)
    if existing is not None and _is_complete_native_extraction_manifest(
        existing, chromosomes
    ):
        manifest = _overlay_worker_owned_manifest_fields(
            existing,
            sample_id=sample_id,
            h5_files=h5_files,
            pattern_files=pattern_files,
        )
        path = write_extraction_manifest(sample_dir, sample_id, manifest)
        logger.info(
            "Preserved native extraction manifest for %s (%s chromosomes)",
            sample_id,
            len(chromosomes),
        )
        return path

    manifest = build_canonical_extraction_manifest_from_stats(
        sample_id=sample_id,
        sample_dir=sample_dir,
        chromosomes=chromosomes,
        contexts=contexts,
        h5_files=h5_files,
        pattern_files=pattern_files,
    )
    path = write_extraction_manifest(sample_dir, sample_id, manifest)
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"Extraction manifest not written: {path}")
    logger.info("Wrote extraction manifest for %s (%s chromosomes)", sample_id, len(chromosomes))
    return path


def resolve_bam_path(sample_dir: Path, sample_id: str) -> Path:
    candidates = [
        sample_dir / f"{sample_id}.bam",
        sample_dir / f"{sample_id}.BAM",
        sample_dir / f"{sample_id}.clara_parabrics.duplicates_marked.bam",
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise RuntimeError(f"BAM not found for methyl extract under {sample_dir}")


def find_bam_path(sample_dir: Path, sample_id: str) -> Optional[Path]:
    """Return BAM path if present; None otherwise (no error)."""
    candidates = [
        sample_dir / f"{sample_id}.bam",
        sample_dir / f"{sample_id}.BAM",
        sample_dir / f"{sample_id}.clara_parabrics.duplicates_marked.bam",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def _resolve_paths(sample_dir: Path, sample_id: str, bam_path: Path) -> MethylExtractPaths:
    return MethylExtractPaths(
        sample_dir=sample_dir,
        sample_id=sample_id,
        bam_path=bam_path,
        log_path=sample_dir / f"{sample_id}.methyl_extract.log",
    )


def _filter_bam_to_panel(
    bam_path: Path,
    panel_bed: Path,
    *,
    sample_dir: Path,
    sample_id: str,
    log_path: Path,
) -> Path:
    """Restrict BAM to target panel intervals (EM-Seq / hybrid-capture seam)."""
    samtools = shutil.which("samtools")
    if not samtools:
        raise RuntimeError(
            "samtools is required to apply methyl_extract.target_panel_bed "
            "(EM-Seq / hybrid-capture restricted extract)"
        )
    out_bam = sample_dir / f"{sample_id}.panel.bam"
    cmd = [
        samtools,
        "view",
        "-b",
        "-L",
        str(panel_bed),
        "-o",
        str(out_bam),
        str(bam_path),
    ]
    _append_log(log_path, "PANEL_FILTER: " + " ".join(shlex.quote(p) for p in cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.stdout:
        _append_log(log_path, proc.stdout)
    if proc.stderr:
        _append_log(log_path, proc.stderr)
    if proc.returncode != 0 or not out_bam.is_file():
        raise RuntimeError(
            proc.stderr.strip()
            or proc.stdout.strip()
            or f"samtools view -L failed for panel {panel_bed}"
        )
    idx = subprocess.run(
        [samtools, "index", str(out_bam)],
        capture_output=True,
        text=True,
        check=False,
    )
    if idx.returncode != 0:
        raise RuntimeError(idx.stderr.strip() or "samtools index failed for panel BAM")
    return out_bam


def _extractor_bin(cfg: MethylExtractConfig) -> str:
    if Path(cfg.extractor_bin).is_file():
        return str(Path(cfg.extractor_bin).resolve())
    found = shutil.which(cfg.extractor_bin)
    if found is None:
        raise RuntimeError(
            f"MethylExtractor binary not found: {cfg.extractor_bin!r}; "
            "install from /home/ubuntu/MethylExtractor via make install"
        )
    return found


@lru_cache(maxsize=8)
def _extractor_help_text(bin_path: str) -> str:
    try:
        proc = subprocess.run(
            [bin_path, "--help"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return f"{proc.stdout or ''}\n{proc.stderr or ''}"


def _extractor_supports_flag(bin_path: str, flag: str) -> bool:
    """Return True when ``MethylExtractor --help`` documents ``flag``."""
    return flag in _extractor_help_text(bin_path)


def _extractor_supports_read_level(bin_path: str) -> bool:
    """Return True when ``MethylExtractor --help`` documents ``--read-level``.

    Older aarch64 builds on some workers omit the flag; asking for it aborts
    extraction with ``unrecognized option``. Probe help text rather than
    hard-coding version strings.
    """
    return _extractor_supports_flag(bin_path, "--read-level")


def build_methyl_extractor_command(cfg: MethylExtractConfig, paths: MethylExtractPaths) -> List[str]:
    bin_path = _extractor_bin(cfg)
    cmd: List[str] = [
        bin_path,
    ]
    if cfg.threads is not None:
        cmd.append(f"--threads={cfg.threads}")
    if cfg.chrom_parallel is not None and _extractor_supports_flag(bin_path, "--chrom-parallel"):
        cmd.append(f"--chrom-parallel={int(cfg.chrom_parallel)}")
    if cfg.max_rss_gb is not None and _extractor_supports_flag(bin_path, "--max-rss-gb"):
        cmd.append(f"--max-rss-gb={int(cfg.max_rss_gb)}")
    if cfg.min_mapq is not None:
        cmd.append(f"--min-mapq={cfg.min_mapq}")
    if cfg.min_phred is not None:
        cmd.append(f"--min-phred={cfg.min_phred}")
    if cfg.min_cov is not None:
        cmd.append(f"--min-cov={cfg.min_cov}")
    if cfg.cap_cov is not None:
        cmd.append(f"--cap-cov={cfg.cap_cov}")
    if "CHG" in cfg.extract_contexts:
        cmd.append("--CHG")
    if "CHH" in cfg.extract_contexts:
        cmd.append("--CHH")
    cmd.append(f"--chrom-mapping={cfg.chrom_mapping}")
    if cfg.compression is not None:
        cmd.append(f"--compression={cfg.compression}")
    if cfg.chunk_size is not None:
        cmd.append(f"--chunk-size={cfg.chunk_size}")
    cmd.append(f"--output-format={cfg.output_format}")
    if cfg.split:
        cmd.append("--split")
    if cfg.read_level:
        if _extractor_supports_read_level(bin_path):
            cmd.append("--read-level")
            if cfg.tile_size is not None:
                cmd.append(f"--tile-size={int(cfg.tile_size)}")
        else:
            logger.warning(
                "methyl_extract.read_level requested but %s has no --read-level; "
                "emitting marginal H5 only (upgrade MethylExtractor for patterns)",
                bin_path,
            )
    if cfg.mhap:
        if _extractor_supports_flag(bin_path, "--mhap"):
            cmd.append("--mhap")
        else:
            logger.warning(
                "methyl_extract.mhap requested but %s has no --mhap; "
                "upgrade MethylExtractor for haplotype sidecars",
                bin_path,
            )
    cmd.append(f"--output-dir={paths.sample_dir}")
    cmd.append(str(paths.bam_path))
    # With --output-dir set, MethylExtractor treats the next positional as ref.fa only
    # (see MethylExtractor/src/main.c: output_dir positional is skipped when -o is used).
    cmd.append(str(cfg.reference_fasta))
    return cmd


def _append_log(log_path: Path, text: str) -> None:
    append_work_text(log_path, text)


def run_methyl_extract(
    *,
    sample_id: str,
    sample_dir: str | Path,
    project: str | Path,
    input_json: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Extract methylation HDF5s with MethylExtractor."""
    payload: Dict[str, Any] = dict(input_json or {})
    payload.setdefault("sampleId", sample_id)
    payload.setdefault("sampleDir", str(sample_dir))
    payload.setdefault("project", str(project))

    cfg = resolve_methyl_extract_config(str(project), payload)
    if not cfg.sample_dir.is_dir():
        raise RuntimeError(f"sampleDir not found: {cfg.sample_dir}")
    ensure_work_writable(cfg.sample_dir)

    expected = expected_h5_files(cfg.chromosomes, cfg.extract_contexts)
    pattern_expected = (
        expected_pattern_h5_files(cfg.chromosomes, cfg.extract_contexts)
        if cfg.read_level
        else []
    )
    marginals_ok = extract_outputs_complete(cfg.sample_dir, expected)
    patterns_ok = (
        (not pattern_expected)
        or extract_pattern_outputs_complete(cfg.sample_dir, pattern_expected)
    )
    if marginals_ok and patterns_ok:
        logger.info("Skipping MethylExtractor; outputs already present for %s", cfg.sample_id)
        # Still (re)write the canonical manifest — H5 skip must not leave a stale
        # stub from WORKER_STUB_EXTERNAL / prior runs for extraction QC.
        pattern_present = [
            name for name in pattern_expected if (cfg.sample_dir / name).is_file()
        ]
        manifest_path = ensure_extraction_manifest(
            sample_id=cfg.sample_id,
            sample_dir=cfg.sample_dir,
            chromosomes=cfg.chromosomes,
            contexts=cfg.extract_contexts,
            h5_files=expected,
            pattern_files=pattern_present,
        )
        share_work_tree(cfg.sample_dir)
        return {
            "sampleId": cfg.sample_id,
            "h5Files": expected,
            "extractionManifest": str(manifest_path),
        }
    if marginals_ok and pattern_expected and not patterns_ok:
        # Prefer re-extract when BAM is available so *.patterns.h5 can be emitted.
        # If BAM is gone, do not fail the pipeline — info_measures skips without patterns.
        bam_existing = find_bam_path(cfg.sample_dir, cfg.sample_id)
        if bam_existing is None:
            logger.warning(
                "read_level enabled but *.patterns.h5 incomplete for %s and no BAM present; "
                "keeping existing marginal HDF5s (pipeline.info_measures will skip or partial)",
                cfg.sample_id,
            )
            manifest_path = ensure_extraction_manifest(
                sample_id=cfg.sample_id,
                sample_dir=cfg.sample_dir,
                chromosomes=cfg.chromosomes,
                contexts=cfg.extract_contexts,
                h5_files=expected,
                pattern_files=[],
            )
            share_work_tree(cfg.sample_dir)
            return {
                "sampleId": cfg.sample_id,
                "h5Files": expected,
                "patternsIncomplete": True,
                "extractionManifest": str(manifest_path),
            }

    bam_path = resolve_bam_path(cfg.sample_dir, cfg.sample_id)
    paths = _resolve_paths(cfg.sample_dir, cfg.sample_id, bam_path)
    if cfg.target_panel_bed is not None:
        logger.info(
            "Restricting BAM to target panel %s for %s",
            cfg.target_panel_bed,
            cfg.sample_id,
        )
        panel_bam = _filter_bam_to_panel(
            bam_path,
            cfg.target_panel_bed,
            sample_dir=cfg.sample_dir,
            sample_id=cfg.sample_id,
            log_path=paths.log_path,
        )
        paths = _resolve_paths(cfg.sample_dir, cfg.sample_id, panel_bam)
    cmd = build_methyl_extractor_command(cfg, paths)

    logger.info("Running MethylExtractor for %s", cfg.sample_id)
    _append_log(paths.log_path, "COMMAND: " + " ".join(shlex.quote(part) for part in cmd))

    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.stdout:
        _append_log(paths.log_path, proc.stdout)
    if proc.stderr:
        _append_log(paths.log_path, proc.stderr)
    if proc.returncode != 0:
        raise RuntimeError(
            proc.stderr.strip() or proc.stdout.strip() or "MethylExtractor failed"
        )

    h5_files = [name for name in expected if (cfg.sample_dir / name).is_file()]
    if pattern_expected:
        pattern_files = [
            name for name in pattern_expected if (cfg.sample_dir / name).is_file()
        ]
        if cfg.read_level and not pattern_files:
            logger.warning(
                "MethylExtractor read_level enabled but no *.patterns.h5 files produced for %s "
                "(continuing; pipeline.info_measures will skip if cohort has no patterns)",
                cfg.sample_id,
            )
    if not h5_files:
        h5_files = sorted(
            p.name
            for p in cfg.sample_dir.glob("*-*.h5")
            if not p.name.endswith(".patterns.h5") and not p.name.endswith(".mhap.h5")
        )
    if not h5_files:
        raise RuntimeError(f"MethylExtractor did not produce HDF5 files under {cfg.sample_dir}")

    pattern_files = [
        name for name in pattern_expected if (cfg.sample_dir / name).is_file()
    ] if pattern_expected else []
    manifest_path = ensure_extraction_manifest(
        sample_id=cfg.sample_id,
        sample_dir=cfg.sample_dir,
        chromosomes=cfg.chromosomes,
        contexts=cfg.extract_contexts,
        h5_files=h5_files,
        pattern_files=pattern_files,
    )
    share_work_tree(cfg.sample_dir)

    return {
        "sampleId": cfg.sample_id,
        "h5Files": h5_files,
        "extractionManifest": str(manifest_path),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run MethylExtractor for one sample (local dev / HPC)")
    parser.add_argument("sample_id", help="Sample identifier")
    parser.add_argument("--sample-dir", help="Sample directory (default: /work/samples/{sample_id})")
    parser.add_argument("--project", required=True, help="Pipeline project JSON on shared storage")
    parser.add_argument("--reference-fasta", help="Override reference FASTA")
    parser.add_argument("--chrom-mapping", help="Override chrom_mapping.json path")
    parser.add_argument(
        "--extract-contexts",
        help="Comma-separated contexts to extract (default from project step_config or CG,CHG,CHH)",
    )
    parser.add_argument("--threads", type=int)
    parser.add_argument("--min-mapq", type=int)
    parser.add_argument("--extractor-bin", help="Dev override for MethylExtractor binary path")
    args = parser.parse_args(list(argv) if argv is not None else None)

    sample_dir = Path(
        args.sample_dir or os.environ.get("METHYL_SAMPLE_DIR") or f"/work/samples/{args.sample_id}"
    )
    input_json: Dict[str, Any] = {
        "sampleId": args.sample_id,
        "sampleDir": str(sample_dir),
        "project": args.project,
    }
    if args.reference_fasta:
        input_json["referenceFasta"] = args.reference_fasta
    if args.chrom_mapping:
        input_json["chromMapping"] = args.chrom_mapping
    if args.extract_contexts:
        input_json["extractContexts"] = [
            p.strip() for p in args.extract_contexts.split(",") if p.strip()
        ]
    if args.threads is not None:
        input_json["threads"] = args.threads
    if args.min_mapq is not None:
        input_json["minMapq"] = args.min_mapq
    extractor_bin = args.extractor_bin or os.environ.get("METHYL_EXTRACTOR_BIN")
    if extractor_bin:
        input_json["extractorBin"] = extractor_bin

    result = run_methyl_extract(
        sample_id=args.sample_id,
        sample_dir=sample_dir,
        project=args.project,
        input_json=input_json,
    )
    for key, value in result.items():
        if key == "h5Files":
            print(f"h5Files={json.dumps(value)}")
        else:
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
