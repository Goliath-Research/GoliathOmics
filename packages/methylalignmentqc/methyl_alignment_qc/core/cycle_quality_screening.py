"""Read-end-aware cycle quality screening for alignment QC remediation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..models.config import CycleScreeningConfig

DISPOSITION_USE_CURRENT = "USE_CURRENT_ALIGNMENT"
DISPOSITION_REALIGN_TRIM = "REALIGN_TRIM"
# Legacy alias retained for qc_history / cohort tooling
DISPOSITION_R2_TRIM = "REALIGN_READ2_TRIM"
DISPOSITION_MULTI_REGION = "INVESTIGATE_MULTI_REGION"
DISPOSITION_GUARDRAIL_ONLY = "INVESTIGATE_GUARDRAIL_ONLY"
DISPOSITION_NOT_FIXABLE = "NOT_FIXABLE"

PATTERN_NO_LOW = "NO_LOW_QUALITY_CYCLES"
PATTERN_R1_START = "READ1_START_LOW_QUALITY"
PATTERN_R1_END = "READ1_END_LOW_QUALITY"
PATTERN_R2_START = "READ2_START_LOW_QUALITY"
PATTERN_R2_END = "READ2_END_LOW_QUALITY"
PATTERN_LOCALIZED = "LOCALIZED_INTERNAL_LOW_QUALITY"
PATTERN_BROAD = "BROAD_LOW_QUALITY"
PATTERN_MULTI = "MULTIPLE_LOW_QUALITY_REGIONS"
PATTERN_UNKNOWN = "UNKNOWN"


@dataclass
class DipRegion:
    start_cycle: int
    end_cycle: int

    def to_dict(self) -> Dict[str, int]:
        return {"start_cycle": self.start_cycle, "end_cycle": self.end_cycle}


def _extract_cycle_qualities(payload: Dict[str, Any]) -> List[Tuple[int, float]]:
    mqc = payload.get("mean_quality_by_cycle") or {}
    if "rows" in mqc:
        rows = mqc.get("rows") or []
        return [(int(r["cycle"]), float(r["mean_quality"])) for r in rows]
    cycles = mqc.get("cycle") or []
    quals = mqc.get("mean_quality") or []
    return list(zip([int(c) for c in cycles], [float(q) for q in quals]))


def _cluster_cycle_regions(cycles: List[int]) -> List[Tuple[int, int]]:
    if not cycles:
        return []
    sorted_cycles = sorted(set(cycles))
    regions: List[Tuple[int, int]] = []
    start = prev = sorted_cycles[0]
    for cycle in sorted_cycles[1:]:
        if cycle <= prev + 1:
            prev = cycle
            continue
        regions.append((start, prev))
        start = prev = cycle
    regions.append((start, prev))
    return regions


def _find_dip_regions(
    cycle_quals: List[Tuple[int, float]],
    threshold: float,
    *,
    min_cycles: int = 1,
) -> List[DipRegion]:
    regions: List[DipRegion] = []
    run_start: Optional[int] = None
    run_len = 0
    prev_cycle: Optional[int] = None

    for cycle, qual in cycle_quals:
        if qual < threshold:
            if run_start is None:
                run_start = cycle
            run_len += 1
            prev_cycle = cycle
        elif run_start is not None and prev_cycle is not None:
            if run_len >= min_cycles:
                regions.append(DipRegion(run_start, prev_cycle))
            run_start = None
            run_len = 0
            prev_cycle = None

    if run_start is not None and prev_cycle is not None and run_len >= min_cycles:
        regions.append(DipRegion(run_start, prev_cycle))

    return regions


def classify_quality_pattern(
    bad_cycles: List[int],
    regions: List[Tuple[int, int]],
    read_length: Optional[int],
    n_cycles: int,
    *,
    edge_window: int,
    broad_bad_count: int,
    localized_max_span: int,
) -> str:
    if not bad_cycles:
        return PATTERN_NO_LOW

    if read_length is None:
        return PATTERN_BROAD if len(bad_cycles) >= broad_bad_count else PATTERN_UNKNOWN

    r1_bad = [c for c in bad_cycles if c <= read_length]
    r2_bad = [c for c in bad_cycles if c > read_length]

    if (
        len(bad_cycles) >= broad_bad_count
        and len(r1_bad) >= 4
        and len(r2_bad) >= 4
    ):
        return PATTERN_BROAD
    if len(bad_cycles) >= max(broad_bad_count + 4, 12):
        return PATTERN_BROAD

    if len(regions) >= 2:
        return PATTERN_MULTI

    start, end = regions[0]
    span = end - start + 1
    cycles = bad_cycles

    def all_match(predicate) -> bool:
        return all(predicate(c) for c in cycles)

    r1_start_max = edge_window
    r1_end_min = read_length - edge_window + 1
    r2_start_max = read_length + edge_window
    r2_end_min = n_cycles - edge_window + 1

    if all_match(lambda c: c <= read_length and c <= r1_start_max):
        return PATTERN_R1_START
    if all_match(lambda c: c <= read_length and c >= r1_end_min):
        return PATTERN_R1_END
    if all_match(lambda c: c > read_length and c <= r2_start_max):
        return PATTERN_R2_START
    if all_match(lambda c: c > read_length and c >= r2_end_min):
        return PATTERN_R2_END

    if span <= localized_max_span and (r1_bad or r2_bad) and not (r1_bad and r2_bad):
        return PATTERN_LOCALIZED

    if len(bad_cycles) >= 6:
        return PATTERN_BROAD

    if r1_bad and r2_bad:
        return PATTERN_MULTI

    return PATTERN_LOCALIZED


def compute_trim_spec(
    pattern: str,
    bad_start: int,
    bad_end: int,
    read_length: Optional[int],
    n_cycles: int,
) -> Optional[Tuple[int, str, int]]:
    """Return (read, end, bases) where end is 'start' or 'end'."""
    if read_length is None:
        return None
    span = bad_end - bad_start + 1

    if pattern == PATTERN_R1_START:
        return (1, "start", span)
    if pattern == PATTERN_R1_END:
        return (1, "end", read_length - bad_start + 1)
    if pattern == PATTERN_R2_START:
        return (2, "start", bad_end - read_length)
    if pattern == PATTERN_R2_END:
        return (2, "end", n_cycles - bad_start + 1)
    if pattern == PATTERN_LOCALIZED:
        if bad_end <= read_length:
            if bad_start <= read_length // 2:
                return (1, "start", span)
            return (1, "end", read_length - bad_start + 1)
        if bad_start > read_length:
            r2_len = n_cycles - read_length
            if bad_start - read_length <= r2_len // 2:
                return (2, "start", bad_end - read_length)
            return (2, "end", n_cycles - bad_start + 1)
    return None


def _trim_fields_from_spec(spec: Optional[Tuple[int, str, int]]) -> Dict[str, int]:
    fields = {"trim_front1": 0, "trim_tail1": 0, "trim_front2": 0, "trim_tail2": 0}
    if spec is None:
        return fields
    read, end, bases = spec
    if bases < 1:
        return fields
    if read == 1:
        key = "trim_front1" if end == "start" else "trim_tail1"
    else:
        key = "trim_front2" if end == "start" else "trim_tail2"
    fields[key] = bases
    return fields


def _compute_trim_front2(
    cycle_quals: List[Tuple[int, float]],
    r2_start: int,
    threshold: float,
    recovery_cycles: int,
    max_trim: int,
) -> int:
    by_cycle = {c: q for c, q in cycle_quals}
    trim = 0
    for offset in range(max_trim):
        cycle = r2_start + offset
        if cycle not in by_cycle:
            break
        if by_cycle[cycle] < threshold:
            trim += 1
        else:
            break

    if trim == 0:
        return 0

    recovery_start = r2_start + trim
    recovered = False
    for offset in range(recovery_cycles):
        cycle = recovery_start + offset
        if cycle not in by_cycle:
            break
        if by_cycle[cycle] >= threshold:
            recovered = True
            break

    if not recovered:
        return 0
    return min(trim, max_trim)


def _cycles_acceptable(guardrails: Dict[str, Any], threshold: float) -> bool:
    details = guardrails.get("details") or {}
    for key in ("mean_quality", "min_quality_post20"):
        node = details.get(key)
        if isinstance(node, dict) and node.get("pass") is False:
            return False
    return True


def _is_metric_node(node: Any) -> bool:
    return isinstance(node, dict) and "pass" in node


def _failed_guardrail_keys(guardrails: Dict[str, Any]) -> List[str]:
    details = guardrails.get("details") or {}
    failed: List[str] = []
    for key, node in details.items():
        if not isinstance(node, dict):
            continue
        if _is_metric_node(node):
            if node.get("pass") is False:
                failed.append(str(key))
            continue
        for sub_key, sub_node in node.items():
            if _is_metric_node(sub_node) and sub_node.get("pass") is False:
                failed.append(f"{key}.{sub_key}")
    return failed


def _empty_screening(message: str) -> Dict[str, Any]:
    return {
        "disposition": DISPOSITION_NOT_FIXABLE,
        "quality_pattern": PATTERN_UNKNOWN,
        "read_length": 0,
        "r2_start_cycle": 0,
        "trim_front1": 0,
        "trim_tail1": 0,
        "trim_front2": 0,
        "trim_tail2": 0,
        "trim_spec": None,
        "r2_start_mean_quality": None,
        "r2_recovery_mean_quality": None,
        "dip_regions": [],
        "message": message,
    }


def _build_realign_result(
    *,
    read_length: int,
    r2_start: int,
    pattern: str,
    bad_start: int,
    bad_end: int,
    n_cycles: int,
    trim_fields: Dict[str, int],
    trim_spec: Optional[Tuple[int, str, int]],
    dip_dicts: List[Dict[str, int]],
    message: str,
    r2_start_mean: Optional[float] = None,
    r2_recovery_mean: Optional[float] = None,
) -> Dict[str, Any]:
    spec_dict = None
    if trim_spec is not None:
        read, end, bases = trim_spec
        spec_dict = {"read": read, "end": end, "bases": bases}
    return {
        "disposition": DISPOSITION_REALIGN_TRIM,
        "quality_pattern": pattern,
        "read_length": read_length,
        "r2_start_cycle": r2_start,
        "trim_spec": spec_dict,
        **trim_fields,
        "r2_start_mean_quality": round(r2_start_mean, 3) if r2_start_mean is not None else None,
        "r2_recovery_mean_quality": round(r2_recovery_mean, 3) if r2_recovery_mean is not None else None,
        "dip_regions": dip_dicts,
        "message": message,
        "bad_cycle_start": bad_start,
        "bad_cycle_end": bad_end,
        "n_cycles": n_cycles,
    }


def screen_cycle_quality(
    payload: Dict[str, Any],
    guardrails: Dict[str, Any],
    config: Optional[CycleScreeningConfig] = None,
) -> Dict[str, Any]:
    """Return screening report dict for guardrails.screening."""
    cfg = config or CycleScreeningConfig()
    cycle_quals = _extract_cycle_qualities(payload)
    if not cycle_quals:
        return _empty_screening("No mean_quality_by_cycle data available for screening.")

    threshold = cfg.r2_quality_threshold
    edge_window = cfg.read_edge_window
    broad_bad = cfg.broad_bad_cycle_count
    localized_max = cfg.localized_max_span

    max_cycle = max(c for c, _ in cycle_quals)
    read_length = cfg.read_length or (max_cycle // 2)
    if max_cycle % 2 != 0 and cfg.read_length is None:
        read_length = max_cycle // 2
    r2_start = read_length + 1
    n_cycles = max_cycle

    overall_pass = bool(guardrails.get("overall_pass"))
    bad_cycles = [c for c, q in cycle_quals if q < threshold]
    regions = _cluster_cycle_regions(bad_cycles)
    dip_regions = _find_dip_regions(cycle_quals, threshold)
    dip_dicts = [r.to_dict() for r in dip_regions]

    pattern = classify_quality_pattern(
        bad_cycles,
        regions,
        read_length,
        n_cycles,
        edge_window=edge_window,
        broad_bad_count=broad_bad,
        localized_max_span=localized_max,
    )

    if overall_pass:
        return {
            "disposition": DISPOSITION_USE_CURRENT,
            "quality_pattern": pattern,
            "read_length": read_length,
            "r2_start_cycle": r2_start,
            "trim_front1": 0,
            "trim_tail1": 0,
            "trim_front2": 0,
            "trim_tail2": 0,
            "trim_spec": None,
            "r2_start_mean_quality": None,
            "r2_recovery_mean_quality": None,
            "dip_regions": dip_dicts,
            "message": "Alignment QC passed; no remediation required.",
        }

    if not bad_cycles:
        if overall_pass:
            return {
                "disposition": DISPOSITION_USE_CURRENT,
                "quality_pattern": pattern,
                "read_length": read_length,
                "r2_start_cycle": r2_start,
                "trim_front1": 0,
                "trim_tail1": 0,
                "trim_front2": 0,
                "trim_tail2": 0,
                "trim_spec": None,
                "r2_start_mean_quality": None,
                "r2_recovery_mean_quality": None,
                "dip_regions": [],
                "message": "Alignment QC passed; no remediation required.",
            }
        failed = _failed_guardrail_keys(guardrails)
        return {
            "disposition": DISPOSITION_GUARDRAIL_ONLY,
            "quality_pattern": pattern,
            "read_length": read_length,
            "r2_start_cycle": r2_start,
            "trim_front1": 0,
            "trim_tail1": 0,
            "trim_front2": 0,
            "trim_tail2": 0,
            "trim_spec": None,
            "r2_start_mean_quality": None,
            "r2_recovery_mean_quality": None,
            "dip_regions": dip_dicts,
            "message": (
                "Per-cycle quality acceptable but alignment guardrails failed "
                f"({', '.join(failed) or 'unknown'}); investigate duplication, insert size, or depth."
            ),
        }

    bad_start, bad_end = min(bad_cycles), max(bad_cycles)
    trim_spec = compute_trim_spec(pattern, bad_start, bad_end, read_length, n_cycles)
    trim_fields = _trim_fields_from_spec(trim_spec)

    # Cap trim bases
    for key in trim_fields:
        if trim_fields[key] > cfg.max_trim_bases:
            trim_fields[key] = cfg.max_trim_bases
    if trim_spec is not None:
        read, end, _ = trim_spec
        bases = trim_fields[
            f"trim_{'front' if end == 'start' else 'tail'}{read}"
        ]
        trim_spec = (read, end, bases)

    fixable_patterns = {
        PATTERN_R1_START,
        PATTERN_R1_END,
        PATTERN_R2_START,
        PATTERN_R2_END,
        PATTERN_LOCALIZED,
    }

    by_cycle = {c: q for c, q in cycle_quals}
    r2_quals = [
        by_cycle[c]
        for c in range(r2_start, r2_start + cfg.r2_start_window_cycles)
        if c in by_cycle
    ]
    r2_start_mean = sum(r2_quals) / len(r2_quals) if r2_quals else None

    if pattern in fixable_patterns and trim_spec is not None and any(trim_fields.values()):
        read, end, bases = trim_spec
        if bases >= 1:
            fastp_flag = f"trim_{'front' if end == 'start' else 'tail'}{read}"
            return _build_realign_result(
                read_length=read_length,
                r2_start=r2_start,
                pattern=pattern,
                bad_start=bad_start,
                bad_end=bad_end,
                n_cycles=n_cycles,
                trim_fields=trim_fields,
                trim_spec=trim_spec,
                dip_dicts=dip_dicts,
                message=(
                    f"{pattern}: recommended {fastp_flag}={bases} before realign."
                ),
                r2_start_mean=r2_start_mean,
            )

    # Legacy R2-start path with recovery check when pattern is R2 start but trim_spec failed
    if pattern == PATTERN_R2_START:
        trim_front2 = _compute_trim_front2(
            cycle_quals,
            r2_start,
            threshold,
            cfg.recovery_cycles,
            cfg.max_trim_bases,
        )
        if trim_front2 >= 1:
            tf = {"trim_front1": 0, "trim_tail1": 0, "trim_front2": trim_front2, "trim_tail2": 0}
            ts = (2, "start", trim_front2)
            end_cycle = r2_start + trim_front2 - 1
            return _build_realign_result(
                read_length=read_length,
                r2_start=r2_start,
                pattern=pattern,
                bad_start=bad_start,
                bad_end=bad_end,
                n_cycles=n_cycles,
                trim_fields=tf,
                trim_spec=ts,
                dip_dicts=dip_dicts,
                message=(
                    f"Read 2 start low quality (cycles {r2_start}–{end_cycle}); "
                    f"recommended trim_front2={trim_front2} before realign."
                ),
                r2_start_mean=r2_start_mean,
            )

    if pattern in {PATTERN_BROAD, PATTERN_MULTI}:
        return {
            "disposition": DISPOSITION_MULTI_REGION if pattern == PATTERN_MULTI else DISPOSITION_NOT_FIXABLE,
            "quality_pattern": pattern,
            "read_length": read_length,
            "r2_start_cycle": r2_start,
            "trim_front1": 0,
            "trim_tail1": 0,
            "trim_front2": 0,
            "trim_tail2": 0,
            "trim_spec": None,
            "r2_start_mean_quality": round(r2_start_mean, 3) if r2_start_mean is not None else None,
            "r2_recovery_mean_quality": None,
            "dip_regions": dip_dicts,
            "message": (
                f"Multiple low-quality cycle regions detected ({len(dip_regions)}); "
                "manual FastQC / sliding-window review recommended."
                if pattern == PATTERN_MULTI
                else "Broad low-quality cycles; not fixable by targeted trim alone."
            ),
        }

    if _cycles_acceptable(guardrails, threshold):
        failed = _failed_guardrail_keys(guardrails)
        return {
            "disposition": DISPOSITION_GUARDRAIL_ONLY,
            "quality_pattern": pattern,
            "read_length": read_length,
            "r2_start_cycle": r2_start,
            "trim_front1": 0,
            "trim_tail1": 0,
            "trim_front2": 0,
            "trim_tail2": 0,
            "trim_spec": None,
            "r2_start_mean_quality": round(r2_start_mean, 3) if r2_start_mean is not None else None,
            "r2_recovery_mean_quality": None,
            "dip_regions": dip_dicts,
            "message": (
                "Per-cycle quality acceptable but alignment guardrails failed "
                f"({', '.join(failed) or 'unknown'}); investigate duplication, insert size, or depth."
            ),
        }

    return {
        "disposition": DISPOSITION_NOT_FIXABLE,
        "quality_pattern": pattern,
        "read_length": read_length,
        "r2_start_cycle": r2_start,
        "trim_front1": 0,
        "trim_tail1": 0,
        "trim_front2": 0,
        "trim_tail2": 0,
        "trim_spec": None,
        "r2_start_mean_quality": round(r2_start_mean, 3) if r2_start_mean is not None else None,
        "r2_recovery_mean_quality": None,
        "dip_regions": dip_dicts,
        "message": "Alignment QC failed without a fixable trim pattern.",
    }


def failed_guardrail_keys(guardrails: Dict[str, Any]) -> List[str]:
    """Public wrapper for failed guardrail key collection."""
    return _failed_guardrail_keys(guardrails)


def apply_screening_recommendations(guardrails: Dict[str, Any], screening: Dict[str, Any]) -> None:
    """Update recommendation/next_steps from screening disposition."""
    disposition = screening.get("disposition")
    if disposition == DISPOSITION_USE_CURRENT:
        return

    tf1 = int(screening.get("trim_front1") or 0)
    tt1 = int(screening.get("trim_tail1") or 0)
    tf2 = int(screening.get("trim_front2") or 0)
    tt2 = int(screening.get("trim_tail2") or 0)
    has_trim = any(v > 0 for v in (tf1, tt1, tf2, tt2))

    if disposition == DISPOSITION_REALIGN_TRIM and has_trim:
        parts = []
        if tf1:
            parts.append(f"--trim_front1 {tf1}")
        if tt1:
            parts.append(f"--trim_tail1 {tt1}")
        if tf2:
            parts.append(f"--trim_front2 {tf2}")
        if tt2:
            parts.append(f"--trim_tail2 {tt2}")
        guardrails["recommendation"] = (
            f"REMEDIATE: Trim bases ({', '.join(parts)}) and realign."
        )
        guardrails["next_steps"] = (
            "Run sample.trim_fastq then parabricks with forceRealign; re-run methyl_qc before extract."
        )
    elif disposition == DISPOSITION_MULTI_REGION:
        guardrails["recommendation"] = "FAIL: Multiple low-quality regions; not fixable by targeted trim alone."
        guardrails["next_steps"] = (
            "Run FastQC per-base quality profile; consider sliding-window trim (SLIDINGWINDOW:4:20) or tail crop."
        )
    elif disposition == DISPOSITION_GUARDRAIL_ONLY:
        guardrails["recommendation"] = (
            "FAIL: Guardrail failure with acceptable cycle quality; investigate non-sequence metrics."
        )
        guardrails["next_steps"] = (
            "Review duplication rate, insert size distribution, and total read count; check library prep logs."
        )
    elif disposition == DISPOSITION_NOT_FIXABLE:
        guardrails["recommendation"] = "FAIL: Alignment QC failed without an automatic trim remediation."
        guardrails["next_steps"] = "Inspect cycle plot and guardrail metrics before deciding next steps."
