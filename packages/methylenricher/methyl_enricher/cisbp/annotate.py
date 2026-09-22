"""
Mode 1B: annotate TFs from Enrichr ChEA/ENCODE/TRRUST hits with CIS-BP motif metadata.

Reads per-library enrichment CSVs already written under the enricher output directory,
matches ``Term`` values to CIS-BP TF names, and emits ``enrich_<label>.csv`` with the
standard merge columns plus CIS-BP fields (motif IDs, evidence, family).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

import pandas as pd

from ..enricher_completeness import is_valid_library_csv, library_csv_path
from .download import resolve_bundle
from .gene_sets import _ENRICH_COLUMNS

logger = logging.getLogger(__name__)

DEFAULT_ANNOTATE_LIBRARIES = [
    "ChEA_2022",
    "ENCODE_and_ChEA_Consensus_TFs_from_ChIP-X",
    "TRRUST_Transcription_Factors_2019",
]

_TF_LIBRARY_TOKENS = (
    "chea",
    "trrust",
    "chip",
    "transcription_factor",
    "encode",
)

_OPTIONAL_TF_INFO_COLS = (
    "TF_Family",
    "Motif_Type",
    "MSource_Identifier",
    "Motif_PubMedIDs",
)


def normalize_tf_name(name: str) -> str:
    """Normalize an Enrichr/CIS-BP TF token for case-insensitive lookup."""
    s = str(name).strip()
    if not s:
        return ""
    if "(" in s:
        s = s.split("(", 1)[0].strip()
    # Drop trailing species tags like "MYC_HUMAN" -> keep simple split on space
    s = re.split(r"\s+", s, maxsplit=1)[0].strip()
    return s.upper()


def _is_tf_library_name(library: str) -> bool:
    token = str(library).strip().lower()
    return any(t in token for t in _TF_LIBRARY_TOKENS)


def resolve_annotate_libraries(cfg, output_dir: Path) -> List[str]:
    """Libraries to read for TF terms (explicit config, else scan output_dir)."""
    explicit = getattr(cfg, "annotate_libraries", None)
    if explicit:
        return [str(lib).strip() for lib in explicit if str(lib).strip()]

    discovered: List[str] = []
    for path in sorted(output_dir.glob("enrich_*.csv")):
        lib = path.name[len("enrich_") : -len(".csv")]
        label = getattr(cfg, "label", None) or "CIS-BP"
        if lib == label:
            continue
        if _is_tf_library_name(lib):
            discovered.append(lib)
    if discovered:
        return discovered
    return list(DEFAULT_ANNOTATE_LIBRARIES)


def build_tf_lookup(
    tf_info: pd.DataFrame,
    *,
    pwm_dir: Path,
) -> Dict[str, Dict[str, object]]:
    """
    Map normalized TF name -> aggregated CIS-BP metadata for that TF.

    Only motifs with an on-disk PWM file are included.
    """
    lookup: Dict[str, Dict[str, object]] = {}
    for _, row in tf_info.iterrows():
        tf = str(row.get("TF_Name", "")).strip()
        mid = str(row.get("Motif_ID", "")).strip()
        if not tf or not mid or mid == ".":
            continue
        if not (pwm_dir / f"{mid}.txt").is_file():
            continue
        key = normalize_tf_name(tf)
        if not key:
            continue
        entry = lookup.setdefault(
            key,
            {
                "cisbp_tf_name": tf,
                "motif_ids": set(),
                "motif_evidence": set(),
                "tf_family": "",
                "motif_type": "",
                "msource": "",
                "pubmed_ids": set(),
            },
        )
        entry["motif_ids"].add(mid)
        ev = str(row.get("motif_evidence", row.get("TF_Status", ""))).strip()
        if ev:
            entry["motif_evidence"].add(ev)
        for col, field in (
            ("TF_Family", "tf_family"),
            ("Motif_Type", "motif_type"),
            ("MSource_Identifier", "msource"),
        ):
            if col in row.index:
                val = str(row[col]).strip()
                if val and not entry[field]:
                    entry[field] = val
        if "Motif_PubMedIDs" in row.index:
            pub = str(row["Motif_PubMedIDs"]).strip()
            if pub and pub != ".":
                for pid in re.split(r"[;,]\s*", pub):
                    pid = pid.strip()
                    if pid:
                        entry["pubmed_ids"].add(pid)
    return lookup


def collect_tf_hits(
    output_dir: Path,
    libraries: Sequence[str],
    *,
    cutoff: float,
    max_terms_per_library: Optional[int] = None,
) -> List[Dict[str, object]]:
    """Load significant TF enrichment rows from per-library CSVs."""
    hits: List[Dict[str, object]] = []
    output_dir = Path(output_dir)
    for lib in libraries:
        path = library_csv_path(output_dir, lib)
        if not is_valid_library_csv(path):
            continue
        df = pd.read_csv(path)
        if df.empty or "Term" not in df.columns:
            continue
        if "Adjusted P-value" in df.columns:
            sig = df[df["Adjusted P-value"] <= cutoff]
        else:
            sig = df
        if sig.empty:
            continue
        n = 0
        for _, row in sig.iterrows():
            term = str(row["Term"]).strip()
            if not term:
                continue
            hits.append(
                {
                    "source_library": lib,
                    "source_term": term,
                    "tf_key": normalize_tf_name(term),
                    "row": row,
                }
            )
            n += 1
            if max_terms_per_library and n >= max_terms_per_library:
                break
    logger.info("[CIS-BP] collected %d TF enrichment hits from %d libraries", len(hits), len(libraries))
    return hits


def build_annotation_rows(
    hits: Sequence[Dict[str, object]],
    lookup: Dict[str, Dict[str, object]],
    *,
    label: str,
    include_unmatched: bool = False,
) -> List[Dict[str, object]]:
    """Turn enrichment hits + CIS-BP lookup into rows for ``enrich_<label>.csv``."""
    rows: List[Dict[str, object]] = []
    seen: Set[Tuple[str, str, str]] = set()

    for hit in hits:
        key = hit["tf_key"]
        source_lib = hit["source_library"]
        source_term = hit["source_term"]
        src = hit["row"]
        meta = lookup.get(key)
        if meta is None:
            if not include_unmatched:
                continue
            dedupe_key = (source_lib, source_term, "")
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            out = {col: pd.NA for col in _ENRICH_COLUMNS}
            out["Term"] = source_term
            out["library"] = label
            out["source_library"] = source_lib
            out["source_term"] = source_term
            out["cisbp_matched"] = False
            for col in _ENRICH_COLUMNS:
                if col in src.index:
                    out[col] = src[col]
            rows.append(out)
            continue

        cisbp_name = str(meta["cisbp_tf_name"])
        dedupe_key = (source_lib, source_term, cisbp_name)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        motif_ids = sorted(meta["motif_ids"])
        out = {col: pd.NA for col in _ENRICH_COLUMNS}
        for col in _ENRICH_COLUMNS:
            if col in src.index:
                out[col] = src[col]
        out["Term"] = cisbp_name
        out["library"] = label
        out["source_library"] = source_lib
        out["source_term"] = source_term
        out["cisbp_matched"] = True
        out["cisbp_motif_ids"] = ",".join(motif_ids)
        out["cisbp_n_motifs"] = len(motif_ids)
        out["cisbp_motif_evidence"] = ",".join(sorted(meta["motif_evidence"]))
        if meta.get("tf_family"):
            out["cisbp_tf_family"] = meta["tf_family"]
        if meta.get("motif_type"):
            out["cisbp_motif_type"] = meta["motif_type"]
        if meta.get("msource"):
            out["cisbp_motif_source"] = meta["msource"]
        if meta.get("pubmed_ids"):
            out["cisbp_motif_pubmed_ids"] = ",".join(sorted(meta["pubmed_ids"])[:20])
        rows.append(out)

    return rows


def write_annotation_csv(rows: Sequence[Dict[str, object]], output_dir: Path, label: str) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"enrich_{label}.csv"
    if not rows:
        return out_path
    df = pd.DataFrame(rows)
    df.to_csv(out_path, index=False)
    logger.info("[CIS-BP] wrote %d annotated TF rows -> %s", len(df), out_path)
    return out_path


def run(cfg, genes: Sequence[str], output_dir, context) -> Optional[str]:  # noqa: ANN001
    del genes  # annotate uses existing TF enrichment outputs, not the gene list
    label = cfg.label or "CIS-BP"
    output_dir = Path(output_dir)
    cutoff = float(context.cutoff if context is not None else 0.05)
    cache_dir = context.cache_dir if context is not None else Path.home() / ".methyl_enricher"

    libraries = resolve_annotate_libraries(cfg, output_dir)
    hits = collect_tf_hits(
        output_dir,
        libraries,
        cutoff=cutoff,
        max_terms_per_library=getattr(cfg, "annotate_max_terms_per_library", None),
    )
    if not hits:
        logger.warning(
            "[CIS-BP] annotate: no TF enrichment hits (libraries=%s, cutoff=%s); "
            "run ChEA/ENCODE/TRRUST enrichment first",
            libraries,
            cutoff,
        )
        return None

    bundle = resolve_bundle(
        species=cfg.species or "Homo_sapiens",
        build=cfg.build or "3.10",
        data_dir=cfg.data_dir,
        cache_dir=str(cache_dir),
        auto_download=True if cfg.auto_download is None else bool(cfg.auto_download),
        base_url=cfg.base_url or "https://cisbp.ccbr.utoronto.ca",
        archive_url=cfg.archive_url,
    )
    tf_info = bundle.load_tf_info(motif_evidence=cfg.motif_evidence)
    lookup = build_tf_lookup(tf_info, pwm_dir=bundle.pwm_dir)
    if not lookup:
        logger.warning("[CIS-BP] annotate: empty CIS-BP TF lookup")
        return None

    rows = build_annotation_rows(
        hits,
        lookup,
        label=label,
        include_unmatched=bool(getattr(cfg, "annotate_include_unmatched", False)),
    )
    if not rows:
        logger.warning(
            "[CIS-BP] annotate: %d enrichment hits but none matched CIS-BP TF names",
            len(hits),
        )
        return None

    write_annotation_csv(rows, output_dir, label)
    return label
