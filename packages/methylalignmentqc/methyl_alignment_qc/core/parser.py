"""
Parser for Picard-style deduplication metrics (Parabricks/bwa-mem2 output).
"""

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from methyl_utils.sample_arm_layout import (
    ALIGN_ARM_DIRNAMES as _ALIGN_ARM_DIRNAMES,
    LEGACY_MODE_DIRNAMES as _LEGACY_EXPERIMENT_MODE_DIRNAMES,
    is_mode_leaf_dirname,
)

# Experiment-mode / arm directory names: the leaf dirname is not the sample id.
_EXPERIMENT_MODE_DIRNAMES = _LEGACY_EXPERIMENT_MODE_DIRNAMES | _ALIGN_ARM_DIRNAMES


def _infer_sample_id_from_artifacts(sample_dir: Path) -> Optional[str]:
    """Derive sample id from uniquely named SamplePrep artifacts under ``sample_dir``."""
    sample_dir = Path(sample_dir)
    if not sample_dir.is_dir():
        return None
    dedups = sorted(sample_dir.glob("*.deduplicate_metrics.txt"))
    if len(dedups) == 1:
        name = dedups[0].name
        return name[: -len(".deduplicate_metrics.txt")]
    tars = sorted(sample_dir.glob("*.qc-metrics.tar"))
    if len(tars) == 1:
        name = tars[0].name
        return name[: -len(".qc-metrics.tar")]
    bams = sorted(sample_dir.glob("*.bam"))
    if len(bams) == 1:
        return bams[0].stem
    return None


def _is_mode_or_arm_leaf(dirname: str) -> bool:
    """True when ``dirname`` is a legacy mode tree or a production align.* arm."""
    return is_mode_leaf_dirname(dirname)


def resolve_sample_artifact_id(
    sample_dir: Path,
    sample_id: Optional[str] = None,
) -> str:
    """Identity for ``{id}.bam`` / metrics / QC JSON basename.

    Prefer an explicit ``sample_id`` (worker task input). Otherwise infer from
    uniquely named artifacts in ``sample_dir`` only (never sibling ``align.*``
    trees). When ``sample_dir.name`` is an experiment-mode or production arm
    leaf (``linear``, ``align.linear.parabricks``, …), fall back to the parent
    directory name (``/work/samples/<sampleId>/<arm>/``). Otherwise use the
    directory basename (flat ``/work/samples/<sampleId>/`` layout).
    """
    explicit = (sample_id or "").strip()
    if explicit:
        return explicit
    sample_dir = Path(sample_dir)
    inferred = _infer_sample_id_from_artifacts(sample_dir)
    dirname = sample_dir.name
    if _is_mode_or_arm_leaf(dirname):
        if inferred:
            return inferred
        parent_name = sample_dir.parent.name
        if parent_name:
            return parent_name
        return dirname
    if inferred and inferred != dirname:
        return inferred
    return dirname


def parse_deduplication_metrics(file_path: Path) -> Dict[str, Any]:
    """
    Parse Picard-style deduplication metrics from a file.

    Args:
        file_path: Path to the deduplication metrics file

    Returns:
        Dictionary containing parsed metrics (duplication_metrics, duplication_histogram)
    """
    with open(file_path, "r") as f:
        content = f.read()

    sections = content.split("## ")
    metrics_data: Dict[str, Any] = {}

    for section in sections:
        section = section.strip()
        if not section:
            continue
        lines = section.split("\n")
        header = lines[0].strip()
        if header.startswith("METRICS CLASS"):
            metrics_data.update(_parse_metrics_section(lines))
        elif header.startswith("HISTOGRAM"):
            metrics_data.update(_parse_histogram_section(lines))

    return metrics_data


def _parse_metrics_section(lines: List[str]) -> Dict[str, Any]:
    """Parse the METRICS CLASS section."""
    metrics: Dict[str, Any] = {}
    header_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("METRICS CLASS"):
            continue
        if not line.startswith("##") and line.strip() and "\t" in line:
            if not any(c.isdigit() for c in line):
                header_idx = i
                break
    if header_idx is None:
        return metrics

    headers = lines[header_idx].strip().split("\t")
    data_rows = []
    for line in lines[header_idx + 1 :]:
        line = line.strip()
        if not line or line.startswith("##"):
            continue
        values = line.split("\t")
        if len(values) == len(headers):
            row_dict = dict(zip(headers, values))
            for key, value in row_dict.items():
                try:
                    float_val = float(value)
                    row_dict[key] = int(float_val) if float_val.is_integer() else float_val
                except ValueError:
                    pass
            data_rows.append(row_dict)
    if data_rows:
        metrics["duplication_metrics"] = data_rows
    return metrics


def _parse_histogram_section(lines: List[str]) -> Dict[str, Any]:
    """Parse the HISTOGRAM section."""
    histogram_data: Dict[str, Any] = {}
    bin_header_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("BIN"):
            bin_header_idx = i
            break
    if bin_header_idx is None:
        return histogram_data

    hist_headers = lines[bin_header_idx].strip().split("\t")
    hist_rows = []
    for line in lines[bin_header_idx + 1 :]:
        line = line.strip()
        if not line or line.startswith("##"):
            continue
        values = line.split("\t")
        if len(values) == len(hist_headers):
            row_dict = {}
            for header, value in zip(hist_headers, values):
                try:
                    float_val = float(value)
                    row_dict[header] = int(float_val) if float_val.is_integer() else float_val
                except ValueError:
                    row_dict[header] = value
            hist_rows.append(row_dict)
    if hist_rows:
        histogram_data["duplication_histogram"] = hist_rows
    return histogram_data


def find_metrics_in_sample_dir(
    sample_dir: Path,
    sample_id: Optional[str] = None,
) -> Optional[Path]:
    """
    Find deduplication metrics file in a single sample directory.

    Looks for {sample_id}.deduplicate_metrics.txt (when sample_id given),
    then the artifact-resolved id (parent of an align.* / mode leaf), then
    {sample_dir.name}.deduplicate_metrics.txt, then *deduplicate_metrics.txt
    / *duplication_metrics.txt under sample_dir only (no sibling arms).

    Returns:
        Path to the metrics file, or None if not found.
    """
    sample_dir = Path(sample_dir)
    if not sample_dir.is_dir():
        return None
    resolved = resolve_sample_artifact_id(sample_dir, sample_id)
    # Prefer explicit/resolved sample id, then directory basename, then glob.
    for name in (resolved, sample_dir.name):
        if not name:
            continue
        candidate = sample_dir / f"{name}.deduplicate_metrics.txt"
        if candidate.exists():
            return candidate
    for pattern in ["*deduplicate_metrics.txt", "*duplication_metrics.txt"]:
        matches = list(sample_dir.glob(pattern))
        if matches:
            return sorted(matches)[0]
    return None


def find_metrics_files(metrics_root: Path) -> List[Path]:
    """Find all deduplication metrics files under metrics_root (rglob)."""
    metrics_files: List[Path] = []
    for pattern in ["*deduplicate_metrics.txt", "*duplication_metrics.txt"]:
        metrics_files.extend(metrics_root.rglob(pattern))
    return sorted(set(metrics_files))


def parse_all_metrics(metrics_root: Path) -> Dict[str, Dict[str, Any]]:
    """
    Parse all metrics files under metrics_root.
    Sample name is taken from the parent directory of each metrics file.

    Returns:
        Dict mapping sample_basename -> parsed metrics dict
    """
    metrics_files = find_metrics_files(metrics_root)
    all_metrics: Dict[str, Dict[str, Any]] = {}
    for metrics_file in metrics_files:
        sample_name = metrics_file.parent.name
        try:
            sample_metrics = parse_deduplication_metrics(metrics_file)
            if sample_metrics:
                all_metrics[sample_name] = sample_metrics
        except Exception as e:
            print(f"Warning: Failed to parse {metrics_file}: {e}")
    return all_metrics


def parse_metrics_from_sample_paths(
    sample_paths: List[Path],
    *,
    sample_id_by_path: Optional[Mapping[str, str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Parse metrics from explicit sample directories.
    Each sample path is a directory; metrics file is discovered inside it.

    Returns:
        Dict mapping sample_id -> parsed metrics dict (skips samples with no metrics file).
        Keys prefer ``sample_id_by_path`` / artifact-resolved ids over directory basenames
        (mode trees and production arms use leaves like ``linear`` / ``align.linear.parabricks``).
    """
    result: Dict[str, Dict[str, Any]] = {}
    id_map = {str(Path(k)): str(v) for k, v in (sample_id_by_path or {}).items() if v}
    for sample_dir in sample_paths:
        sample_dir = Path(sample_dir)
        sample_id = resolve_sample_artifact_id(sample_dir, id_map.get(str(sample_dir)))
        metrics_file = find_metrics_in_sample_dir(sample_dir, sample_id=sample_id)
        if metrics_file is None:
            print(f"Warning: No metrics file found in {sample_dir}")
            continue
        try:
            metrics = parse_deduplication_metrics(metrics_file)
            if metrics:
                result[sample_id] = metrics
        except Exception as e:
            print(f"Warning: Failed to parse {metrics_file}: {e}")
    return result


def calculate_summary_stats(metrics_data: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Compute summary stats (total_reads, duplication_rate, etc.) per sample."""
    summary: Dict[str, Dict[str, Any]] = {}
    for sample_name, sample_data in metrics_data.items():
        if "duplication_metrics" in sample_data and sample_data["duplication_metrics"]:
            metric = sample_data["duplication_metrics"][0]
            summary[sample_name] = {
                "total_reads": metric.get("UNPAIRED_READS_EXAMINED", 0) + metric.get("READ_PAIRS_EXAMINED", 0),
                "duplication_rate": metric.get("PERCENT_DUPLICATION", 0.0),
                "estimated_library_size": metric.get("ESTIMATED_LIBRARY_SIZE", 0),
                "duplicate_reads": metric.get("READ_PAIR_DUPLICATES", 0),
                "optical_duplicates": metric.get("READ_PAIR_OPTICAL_DUPLICATES", 0),
            }
    return summary
