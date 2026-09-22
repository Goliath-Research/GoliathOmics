"""Production sample layout: shared FASTQs at sample root, products in align.* leaves.

Canonical dirs under ``/work/samples/<sampleId>/``:

```text
<sampleId>_1.fastq.gz
<sampleId>_2.fastq.gz
align.linear.parabricks/
align.linear.mojo/
align.pangenome.parabricks/
align.pangenome_wgbs.vg/
align.pangenome_wgbs.mojo/
.caas/
```

``alignmentMode`` remains the science flag. Engine knobs under ``actionConfig``
select which arm leaf ``sampleDir`` points at. Explicit ``samples[].sampleDir``
that is already an arm (or legacy mode) leaf is left unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

# Bakeoff subset (Clara linear, Mojo linear, vg WGBS, Mojo WGBS).
COMPARE_ALIGN_ARMS: Tuple[str, ...] = (
    "align.linear.parabricks",
    "align.linear.mojo",
    "align.pangenome_wgbs.vg",
    "align.pangenome_wgbs.mojo",
)

# Full production set, including stock Clara giraffe (not a WGBS GAF substitute).
CANONICAL_ALIGN_ARMS: Tuple[str, ...] = COMPARE_ALIGN_ARMS + (
    "align.pangenome.parabricks",
)

ALIGN_ARM_DIRNAMES = frozenset(CANONICAL_ALIGN_ARMS)

ALIGN_ARM_ALIASES: Dict[str, str] = {
    "align.pangenome.vg": "align.pangenome_wgbs.vg",
}

COMPARE_EXTRACT_ARMS: Tuple[str, ...] = (
    "extract.methylextractor",
    "extract.methyldackel",
)

LEGACY_MODE_DIRNAMES = frozenset({"linear", "pangenome", "pangenome_wgbs"})

ALIGN_ARM_TO_MODE: Dict[str, str] = {
    "align.linear.parabricks": "linear",
    "align.linear.mojo": "linear",
    "align.pangenome.parabricks": "pangenome",
    "align.pangenome_wgbs.vg": "pangenome_wgbs",
    "align.pangenome.vg": "pangenome_wgbs",
    "align.pangenome_wgbs.mojo": "pangenome_wgbs",
}

ALIGN_ARM_ACTION_CONFIG: Dict[str, Dict[str, Any]] = {
    "align.linear.parabricks": {
        "parabricks": {"engine": "parabricks", "alignment_mode": "linear"},
    },
    "align.linear.mojo": {
        "parabricks": {"engine": "mojo", "alignment_mode": "linear"},
    },
    "align.pangenome.parabricks": {
        "parabricks": {"engine": "parabricks", "alignment_mode": "pangenome"},
    },
    "align.pangenome_wgbs.vg": {
        "methylgrapher_wgbs": {
            "alignment_mode": "pangenome_wgbs",
            "align_engine": "cpu_vg",
            "engine": "mojo",
        },
    },
    "align.pangenome.vg": {
        "methylgrapher_wgbs": {
            "alignment_mode": "pangenome_wgbs",
            "align_engine": "cpu_vg",
            "engine": "mojo",
        },
    },
    "align.pangenome_wgbs.mojo": {
        "methylgrapher_wgbs": {
            "alignment_mode": "pangenome_wgbs",
            "align_engine": "gpu_giraffe",
            "engine": "mojo",
        },
    },
}

_KNOWN_ARMS = frozenset(CANONICAL_ALIGN_ARMS) | frozenset(COMPARE_EXTRACT_ARMS) | frozenset(
    ALIGN_ARM_ALIASES
)


def normalize_align_arm(arm: str) -> str:
    return ALIGN_ARM_ALIASES.get(arm, arm)


def is_align_arm_dirname(name: str) -> bool:
    n = normalize_align_arm(str(name or "").strip())
    return n in ALIGN_ARM_TO_MODE or n.startswith("align.")


def is_extract_arm_dirname(name: str) -> bool:
    n = str(name or "").strip()
    return n in COMPARE_EXTRACT_ARMS or n.startswith("extract.")


def is_mode_leaf_dirname(name: str) -> bool:
    n = str(name or "").strip()
    return n in LEGACY_MODE_DIRNAMES or is_align_arm_dirname(n) or is_extract_arm_dirname(n)


def sample_root_dir(samples_base: Path | str, sample_id: str) -> Path:
    return Path(samples_base).expanduser().resolve() / sample_id


def sample_root_from_sample_dir(
    sample_dir: Path | str,
    sample_id: Optional[str] = None,
) -> Path:
    """Identity root for FASTQs / CAAS when ``sampleDir`` may be an arm leaf."""
    path = Path(str(sample_dir)).expanduser()
    if is_mode_leaf_dirname(path.name):
        return path.parent
    sid = (sample_id or "").strip()
    if sid and path.name != sid and is_mode_leaf_dirname(path.name):
        return path.parent
    return path


def align_arm_dir(sample_root: Path | str, arm: str) -> Path:
    arm = normalize_align_arm(arm)
    if arm not in _KNOWN_ARMS and arm not in COMPARE_EXTRACT_ARMS:
        raise ValueError(
            f"unsupported comparison arm: {arm}; "
            f"expected one of {CANONICAL_ALIGN_ARMS + COMPARE_EXTRACT_ARMS}"
        )
    return Path(sample_root).expanduser().resolve() / arm


def ensure_align_arm_dir(sample_root: Path | str, arm: str) -> Path:
    d = align_arm_dir(sample_root, arm)
    d.mkdir(parents=True, exist_ok=True)
    return d


def alignment_mode_for_arm(arm: str) -> str:
    arm = normalize_align_arm(arm)
    try:
        return ALIGN_ARM_TO_MODE[arm]
    except KeyError as exc:
        raise ValueError(f"not an align arm: {arm}") from exc


def action_config_overlay_for_arm(arm: str) -> Dict[str, Any]:
    arm = normalize_align_arm(arm)
    try:
        return dict(ALIGN_ARM_ACTION_CONFIG[arm])
    except KeyError as exc:
        raise ValueError(f"no actionConfig overlay for arm: {arm}") from exc


def _norm_alignment_mode(raw: Any) -> str:
    mode = str(raw or "").strip().lower().replace("-", "_")
    if mode in {"wgbs_pangenome", "pangenomewgbs", "methylgrapher", "methylgrapher_wgbs"}:
        return "pangenome_wgbs"
    if mode in {"giraffe", "stock_pangenome", "stock_giraffe"}:
        return "pangenome"
    if mode in {"linear", "pangenome", "pangenome_wgbs"}:
        return mode
    return "linear"


def align_arm_for_mode_and_config(
    alignment_mode: Any,
    action_config: Optional[Mapping[str, Any]] = None,
) -> str:
    """Map science mode + engine knobs to a canonical ``align.*`` directory name."""
    mode = _norm_alignment_mode(alignment_mode)
    ac = dict(action_config or {})
    if mode == "pangenome_wgbs":
        mg = dict(ac.get("methylgrapher_wgbs") or {})
        align_engine = str(mg.get("align_engine") or "").strip().lower()
        if align_engine in {"cpu_vg", "vg"}:
            return "align.pangenome_wgbs.vg"
        if align_engine in {"gpu_giraffe", "mojo_giraffe"}:
            return "align.pangenome_wgbs.mojo"
        if str(mg.get("engine") or "").strip().lower() == "mojo":
            return "align.pangenome_wgbs.mojo"
        return "align.pangenome_wgbs.vg"
    if mode == "pangenome":
        return "align.pangenome.parabricks"
    pb = dict(ac.get("parabricks") or {})
    engine = str(pb.get("engine") or "").strip().lower()
    if engine == "mojo":
        return "align.linear.mojo"
    return "align.linear.parabricks"


def align_arm_for_context(context: Mapping[str, Any]) -> str:
    ac = context.get("actionConfig")
    if not isinstance(ac, dict):
        ac = {}
    return align_arm_for_mode_and_config(context.get("alignmentMode"), ac)


def discover_root_fastqs(sample_root: Path | str, sample_id: str) -> List[Path]:
    """Return non-empty FASTQ files directly under the sample root (no /fastq child)."""
    root = Path(sample_root).expanduser().resolve()
    patterns = (
        f"{sample_id}_1.fastq.gz",
        f"{sample_id}_2.fastq.gz",
        f"{sample_id}_R1.fastq.gz",
        f"{sample_id}_R2.fastq.gz",
        f"{sample_id}.R1.fastq.gz",
        f"{sample_id}.R2.fastq.gz",
    )
    found: List[Path] = []
    for name in patterns:
        path = root / name
        if path.is_file() and path.stat().st_size > 0:
            found.append(path)
    if found:
        return sorted(set(found))
    for path in sorted(root.glob("*.fastq.gz")) + sorted(root.glob("*.fq.gz")):
        if path.is_file() and path.stat().st_size > 0 and path.parent == root:
            found.append(path)
    return found


def link_paths_into_dir(
    sources: Sequence[Path | str],
    dest_dir: Path | str,
    *,
    method: str = "hardlink",
) -> List[Path]:
    """Hardlink (or symlink/copy) existing files into ``dest_dir`` by basename."""
    dest = Path(dest_dir).expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    linked: List[Path] = []
    for raw in sources:
        src = Path(raw).expanduser().resolve()
        target = dest / src.name
        if target == src:
            linked.append(target)
            continue
        if target.exists() or target.is_symlink():
            if target.is_file() and target.stat().st_size > 0:
                linked.append(target)
                continue
            target.unlink(missing_ok=True)
        if method == "symlink":
            os.symlink(src, target)
        elif method == "copy":
            import shutil

            shutil.copy2(src, target)
        else:
            try:
                os.link(src, target)
            except OSError:
                os.symlink(src, target)
        linked.append(target)
    return linked


def link_root_fastqs_into_dir(
    sample_root: Path | str,
    dest_dir: Path | str,
    *,
    sample_id: str,
    method: str = "hardlink",
) -> List[Path]:
    """Hardlink (or symlink/copy) root FASTQs into ``dest_dir``."""
    root = Path(sample_root).expanduser().resolve()
    dest = Path(dest_dir).expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    if dest == root:
        return discover_root_fastqs(root, sample_id)
    fastqs = discover_root_fastqs(root, sample_id)
    if not fastqs:
        raise FileNotFoundError(
            f"No non-empty root FASTQs under {root} for sample_id={sample_id}"
        )
    return link_paths_into_dir(fastqs, dest, method=method)


def ensure_sample_dir_fastq_links(
    sample_dir: Path | str,
    *,
    sample_id: str,
    sample_root: Path | str | None = None,
    sources: Sequence[Path | str] | None = None,
    method: str = "hardlink",
) -> List[Path]:
    """Hardlink sample-root FASTQs into an arm ``sampleDir`` when they differ.

    Live ``download_fastq`` already does this. CAAS skip restores blobs at
    ``sampleRoot`` and must call this so align (which discovers pairs only under
    ``sampleDir``) still sees files after ``delete_fastqs``.
    """
    dest = Path(sample_dir).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    dest = dest.resolve()
    root = (
        Path(sample_root).expanduser().resolve()
        if sample_root
        else Path(sample_root_from_sample_dir(dest, sample_id)).expanduser().resolve()
    )
    src_paths = (
        [Path(item).expanduser() for item in sources]
        if sources is not None
        else discover_root_fastqs(root, sample_id)
    )
    src_files = [path for path in src_paths if path.is_file() and path.stat().st_size > 0]
    if dest == root:
        return src_files or discover_root_fastqs(dest, sample_id)
    if src_files:
        return link_paths_into_dir(src_files, dest, method=method)
    return discover_root_fastqs(dest, sample_id)


def link_root_fastqs_into_align_arm(
    sample_root: Path | str,
    arm: str,
    *,
    sample_id: str,
    method: str = "hardlink",
) -> List[Path]:
    """Hardlink (or symlink/copy) root FASTQs into an ``align.*`` sampleDir."""
    arm = normalize_align_arm(arm)
    if arm not in ALIGN_ARM_TO_MODE:
        raise ValueError(f"arm {arm} is not an align arm")
    root = Path(sample_root).expanduser().resolve()
    arm_dir = ensure_align_arm_dir(root, arm)
    return link_root_fastqs_into_dir(
        root, arm_dir, sample_id=sample_id, method=method
    )


def resolve_sample_root(
    *,
    sample_root: Optional[str] = None,
    sample_dir: Optional[str] = None,
    sample_id: Optional[str] = None,
    samples_base: Optional[str] = None,
) -> Path:
    """Identity root for FASTQs / CAAS (``/work/samples/{id}``)."""
    if sample_root:
        return Path(str(sample_root)).expanduser().resolve()
    if sample_dir:
        return Path(sample_root_from_sample_dir(sample_dir, sample_id)).expanduser().resolve()
    if sample_id:
        return sample_root_dir(samples_base or "/work/samples", sample_id)
    raise ValueError("sampleRoot, sampleDir, or sampleId is required")


def _paths_equal(left: Path | str, right: Path | str) -> bool:
    try:
        return Path(left).expanduser().resolve() == Path(right).expanduser().resolve()
    except OSError:
        return os.path.normpath(str(left)) == os.path.normpath(str(right))


def is_default_sample_dir(sample_dir: Path | str, sample_root: Path | str) -> bool:
    """True when ``sampleDir`` is the identity root (not an arm / mode leaf)."""
    if is_mode_leaf_dirname(Path(str(sample_dir)).name):
        return False
    return _paths_equal(sample_dir, sample_root)


def bind_production_sample_arms(
    context: MutableMapping[str, Any],
    *,
    program: Any = None,
) -> MutableMapping[str, Any]:
    """Bind methylation SamplePrep arms; skip RNA / proteomics process packs.

    ``program`` is the DomainProgram path/name when the caller has it on the
    start request (it may not yet be copied onto ``context``).
    """
    samples = context.get("samples")
    if not isinstance(samples, list) or not samples:
        return context
    from methyl_utils.modality_gate import (
        context_modality,
        infer_profile_family,
        infer_program_family,
    )

    prog = program
    if prog is None:
        prog = (
            context.get("program_path")
            or context.get("program")
            or context.get("programPath")
        )
    profile = context.get("pipelineProfile") or context.get("profilePath")
    family = (
        infer_program_family(prog)
        or infer_profile_family(profile)
        or context_modality(context)
    )
    if family in {"rnaseq", "proteomics"}:
        return context
    return bind_sample_arm_dirs(context)


def bind_sample_arm_dirs(context: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Set per-sample ``sampleRoot`` and default ``sampleDir`` to the derived arm leaf.

    Explicit ``samples[].sampleDir`` that already names an arm or legacy mode leaf
    is preserved (bakeoff / recovery). Identity-root ``sampleDir`` is rewritten.
    """
    samples = context.get("samples")
    if not isinstance(samples, list) or not samples:
        return context
    arm = align_arm_for_context(context)
    updated: List[Any] = []
    for item in samples:
        if not isinstance(item, dict):
            updated.append(item)
            continue
        sample = dict(item)
        sample_id = str(sample.get("sampleId") or "").strip()
        current_dir = sample.get("sampleDir")
        if current_dir:
            inferred_root = sample_root_from_sample_dir(str(current_dir), sample_id or None)
        else:
            inferred_root = None
        root_raw = sample.get("sampleRoot")
        if root_raw:
            root = Path(str(root_raw)).expanduser()
        elif inferred_root is not None:
            root = inferred_root
        elif sample_id:
            base = context.get("samplesBaseDir") or context.get("samples_base_path") or "/work/samples"
            root = sample_root_dir(str(base), sample_id)
        else:
            updated.append(sample)
            continue
        root_path = Path(root).expanduser().resolve()
        sample["sampleRoot"] = str(root_path)
        if not current_dir or is_default_sample_dir(str(current_dir), root_path):
            sample["sampleDir"] = str(root_path / arm)
        updated.append(sample)
    context["samples"] = updated
    return context
