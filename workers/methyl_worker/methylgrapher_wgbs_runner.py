"""Worker-side methylGrapher WGBS pangenome alignment + graph-aware extraction.

Produces QC-compatible GRCh38 BAM artifacts for ``sample.methyl_qc`` and
``{chrom}-{context}.h5`` / ``{chrom}-{context}.patterns.h5`` for extraction QC
and informME. Asset paths come exclusively from task ``resolvedConfig`` on the
worker path (no site/profile re-read for science knobs).
"""

from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import logging
import os
import shlex
import shutil
import subprocess
import tarfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from methyl_worker.work_share import share_work_path, share_work_tree

import numpy as np

from methyl_worker.mojo_align_env import (
    INSTALL_PREFIX,
    getenv as mojo_align_getenv,
    overlay_dir as mojo_align_overlay_dir,
)

logger = logging.getLogger(__name__)

METHYLGRAPHER_IMAGE_ENV = "METHYL_METHYLGRAPHER_IMAGE"
VG_BIN_ENV = "METHYL_VG_BIN"
METHYLGRAPHER_BIN_ENV = "METHYL_METHYLGRAPHER_BIN"


@dataclass(frozen=True)
class MethylGrapherWgbsBundle:
    c2t_gbz: Path
    c2t_dist: Path
    c2t_min: Path
    c2t_zipcodes: Path
    g2a_gbz: Path
    g2a_dist: Path
    g2a_min: Path
    g2a_zipcodes: Path
    ref_paths: Path
    cpg_tsv: Path
    linear_ref_fasta: Path
    original_gbz: Optional[Path] = None
    node_replacement_json: Optional[Path] = None
    index_prefix: Optional[str] = None
    directional: bool = True
    threads: Optional[int] = None
    image: Optional[str] = None
    image_digest: Optional[str] = None
    methylgrapher_version: Optional[str] = None
    vg_version: Optional[str] = None
    engine: str = "python"
    align_engine: str = "cpu_vg"
    gpu_giraffe_fallback: Optional[str] = None
    giraffe_device: Optional[str] = None
    mojo_giraffe_ready: Optional[bool] = None
    mojo_segments_cache: Optional[str] = None
    modular_cache_dir: Optional[str] = None
    qc_bam_engine: Optional[str] = None
    qc_bam_fallback: Optional[str] = None
    dual_graph_parallel: Optional[bool] = None
    conversion_rate_enabled: bool = False
    conversion_rate_sidecar: str = "bisulfite_conversion.json"
    cg_only: bool = True
    contexts: Tuple[str, ...] = ("CG",)
    read_level_enabled: bool = True
    # When false, skip 15GB+ GAF pattern scans and use marginal_surrogate H5s.
    # Operator-set under resolvedConfig.read_level.gaf_patterns (site/profile).
    read_level_gaf_patterns: bool = True
    tile_size: int = 4
    batch_size: Optional[int] = None
    linear_cpg_tsv: Optional[Path] = None
    wl_gfa: Optional[Path] = None

    def required_files(self) -> List[Tuple[str, Path]]:
        items = [
            ("c2t.gbz", self.c2t_gbz),
            ("c2t.dist", self.c2t_dist),
            ("c2t.min", self.c2t_min),
            ("c2t.zipcodes", self.c2t_zipcodes),
            ("g2a.gbz", self.g2a_gbz),
            ("g2a.dist", self.g2a_dist),
            ("g2a.min", self.g2a_min),
            ("g2a.zipcodes", self.g2a_zipcodes),
            ("ref_paths", self.ref_paths),
            ("cpg_tsv", self.cpg_tsv),
            ("linear_ref_fasta", self.linear_ref_fasta),
        ]
        if self.original_gbz is not None:
            items.append(("original_gbz", self.original_gbz))
        if self.node_replacement_json is not None:
            items.append(("node_replacement_json", self.node_replacement_json))
        return items

    def assert_present(self) -> None:
        missing = [label for label, path in self.required_files() if not path.is_file()]
        if missing:
            raise RuntimeError(
                "methylGrapher WGBS assets missing on disk (no stock Giraffe fallback): "
                + ", ".join(missing)
            )


def _pick_bool(payload: Mapping[str, Any], key: str, default: bool) -> bool:
    if key not in payload or payload[key] is None:
        return default
    val = payload[key]
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_engine(value: Any) -> str:
    """Return ``python`` or ``mojo``; unset → ``python`` (safe default)."""
    if value is None or str(value).strip() == "":
        return "python"
    eng = str(value).strip().lower()
    if eng in {"mojo", "methylgrapher-mojo"}:
        return "mojo"
    if eng in {"python", "py", "0.2.0", "stock"}:
        return "python"
    raise RuntimeError(
        f"methylgrapher_wgbs.engine must be 'python' or 'mojo' (got {value!r})"
    )


def _normalize_align_engine(value: Any) -> str:
    """Return ``cpu_vg``, ``gpu_giraffe``, or ``mojo_giraffe``; unset → ``cpu_vg``."""
    if value is None or str(value).strip() == "":
        return "cpu_vg"
    eng = str(value).strip().lower()
    if eng in {"cpu_vg", "cpu", "vg"}:
        return "cpu_vg"
    if eng in {"mojo_giraffe", "mojo"}:
        return "mojo_giraffe"
    if eng in {"gpu_giraffe", "gpu", "gh200"}:
        return "gpu_giraffe"
    raise RuntimeError(
        "methylgrapher_wgbs.align_engine must be 'cpu_vg', 'gpu_giraffe', or "
        f"'mojo_giraffe' (got {value!r})"
    )


def resolve_wgbs_bundle_from_resolved(
    resolved_config: Mapping[str, Any] | None,
) -> MethylGrapherWgbsBundle:
    from methyl_utils.action_config_resolver import resolve_methylgrapher_wgbs_genome

    if not resolved_config:
        raise RuntimeError(
            "sample.methylgrapher_wgbs_* requires resolvedConfig.methylgrapher_wgbs "
            "(C2T/G2A assets baked at instance configuration)"
        )
    raw = resolve_methylgrapher_wgbs_genome(resolved_config=resolved_config)
    c2t = raw["c2t"]
    g2a = raw["g2a"]
    rl = dict(raw.get("read_level") or {})
    contexts = tuple(raw.get("contexts") or ("CG",))
    bundle = MethylGrapherWgbsBundle(
        c2t_gbz=Path(c2t["gbz"]).expanduser().resolve(),
        c2t_dist=Path(c2t["dist"]).expanduser().resolve(),
        c2t_min=Path(c2t["min"]).expanduser().resolve(),
        c2t_zipcodes=Path(c2t["zipcodes"]).expanduser().resolve(),
        g2a_gbz=Path(g2a["gbz"]).expanduser().resolve(),
        g2a_dist=Path(g2a["dist"]).expanduser().resolve(),
        g2a_min=Path(g2a["min"]).expanduser().resolve(),
        g2a_zipcodes=Path(g2a["zipcodes"]).expanduser().resolve(),
        ref_paths=Path(raw["ref_paths"]).expanduser().resolve(),
        cpg_tsv=Path(raw["cpg_tsv"]).expanduser().resolve(),
        linear_ref_fasta=Path(raw["linear_ref_fasta"]).expanduser().resolve(),
        original_gbz=(
            Path(raw["original_gbz"]).expanduser().resolve()
            if raw.get("original_gbz")
            else None
        ),
        node_replacement_json=(
            Path(raw["node_replacement_json"]).expanduser().resolve()
            if raw.get("node_replacement_json")
            else None
        ),
        index_prefix=str(raw["index_prefix"]) if raw.get("index_prefix") else None,
        directional=_pick_bool(raw, "directional", True),
        threads=int(raw["threads"]) if raw.get("threads") is not None else None,
        image=str(raw["image"]) if raw.get("image") else None,
        image_digest=str(raw["image_digest"]) if raw.get("image_digest") else None,
        methylgrapher_version=(
            str(raw["methylgrapher_version"]) if raw.get("methylgrapher_version") else None
        ),
        vg_version=str(raw["vg_version"]) if raw.get("vg_version") else None,
        engine=_normalize_engine(raw.get("engine")),
        align_engine=_normalize_align_engine(raw.get("align_engine")),
        gpu_giraffe_fallback=(
            str(raw["gpu_giraffe_fallback"]).strip().lower()
            if raw.get("gpu_giraffe_fallback") not in (None, "")
            else None
        ),
        giraffe_device=(
            str(raw["giraffe_device"]).strip()
            if raw.get("giraffe_device") not in (None, "")
            else (
                str(raw["align_device"]).strip()
                if raw.get("align_device") not in (None, "")
                else None
            )
        ),
        mojo_giraffe_ready=(
            bool(raw["mojo_giraffe_ready"])
            if "mojo_giraffe_ready" in raw and raw["mojo_giraffe_ready"] is not None
            else None
        ),
        mojo_segments_cache=(
            str(raw["mojo_segments_cache"]).strip()
            if raw.get("mojo_segments_cache") not in (None, "")
            else None
        ),
        modular_cache_dir=(
            str(raw["modular_cache_dir"]).strip()
            if raw.get("modular_cache_dir") not in (None, "")
            else None
        ),
        qc_bam_engine=(
            str(raw["qc_bam_engine"]).strip().lower()
            if raw.get("qc_bam_engine") not in (None, "")
            else None
        ),
        qc_bam_fallback=(
            str(raw["qc_bam_fallback"]).strip().lower()
            if raw.get("qc_bam_fallback") not in (None, "")
            else None
        ),
        dual_graph_parallel=(
            bool(raw["dual_graph_parallel"])
            if "dual_graph_parallel" in raw and raw["dual_graph_parallel"] is not None
            else None
        ),
        conversion_rate_enabled=_pick_bool(raw, "conversion_rate_enabled", False),
        conversion_rate_sidecar=(
            str(raw.get("conversion_rate_sidecar") or "bisulfite_conversion.json").strip()
            or "bisulfite_conversion.json"
        ),
        cg_only=_pick_bool(raw, "cg_only", True),
        contexts=contexts,
        read_level_enabled=_pick_bool(
            rl,
            "enabled",
            _pick_bool(raw, "read_level_enabled", True),
        ),
        # Mojo GAFs are multi-GB; default skip GAF pattern scans unless the
        # operator explicitly sets read_level.gaf_patterns=true (site/profile).
        read_level_gaf_patterns=_pick_bool(
            rl,
            "gaf_patterns",
            _pick_bool(
                raw,
                "read_level_gaf_patterns",
                _normalize_align_engine(raw.get("align_engine"))
                not in {"mojo", "mojo_giraffe", "gpu_giraffe"},
            ),
        ),
        tile_size=int(rl.get("tile_size") or 4),
        batch_size=int(raw["batch_size"]) if raw.get("batch_size") is not None else None,
        linear_cpg_tsv=(
            Path(str(raw["linear_cpg_tsv"])).expanduser().resolve()
            if raw.get("linear_cpg_tsv")
            else None
        ),
        wl_gfa=(
            Path(str(raw["wl_gfa"])).expanduser().resolve() if raw.get("wl_gfa") else None
        ),
    )
    bundle.assert_present()
    return bundle


# Stock python methylGrapher forks a second in-memory GFA worker when -t > 20;
# for HPRC-scale graphs that doubles RAM. Cap MethylCall threads for engine=python.
# engine=mojo forces gfa_worker_num=1, so this cap is not applied.
_METHYLCALL_THREAD_CAP = 16
_DEFAULT_MOJO_IMAGE = "goliath/methylgrapher:1.70-mojo"


def resolve_index_prefix(bundle: MethylGrapherWgbsBundle) -> str:
    index_prefix = bundle.index_prefix or str(
        bundle.c2t_gbz.parent / Path(bundle.c2t_gbz.name.split(".wl.")[0])
    )
    return str(Path(index_prefix).expanduser().resolve())


def resolve_wl_gfa_path(bundle: MethylGrapherWgbsBundle, index_prefix: str) -> Path:
    if bundle.wl_gfa is not None:
        return Path(bundle.wl_gfa)
    return Path(f"{index_prefix}.wl.gfa")


def assert_methylcall_assets(bundle: MethylGrapherWgbsBundle, index_prefix: str) -> Path:
    """Fail fast when MethylCall's required PrepareGenome artifacts are absent.

    Without ``{index_prefix}.wl.gfa``, methylGrapher's GFA workers crash while the
    alignment parser keeps filling an orphan queue — looks like a hang.
    """
    wl_gfa = resolve_wl_gfa_path(bundle, index_prefix)
    node_repl = Path(f"{index_prefix}.wl.node.replacement.json")
    if bundle.node_replacement_json is not None:
        node_repl = Path(bundle.node_replacement_json)
    missing: List[str] = []
    if not wl_gfa.is_file() or wl_gfa.stat().st_size == 0:
        missing.append(str(wl_gfa))
    if not node_repl.is_file() or node_repl.stat().st_size == 0:
        missing.append(str(node_repl))
    if missing:
        raise RuntimeError(
            "methylGrapher MethylCall requires PrepareGenome assets that are missing "
            "or empty: " + ", ".join(missing)
        )
    return wl_gfa


def fingerprint_wgbs_assets(bundle: MethylGrapherWgbsBundle) -> Dict[str, str]:
    """Content fingerprints for CAAS (size + partial digest)."""

    def _fp(path: Path) -> str:
        h = hashlib.sha256()
        h.update(str(path.stat().st_size).encode())
        with path.open("rb") as fh:
            h.update(fh.read(1_048_576))
        return h.hexdigest()[:16]

    out: Dict[str, str] = {}
    for label, path in bundle.required_files():
        out[label] = _fp(path)
    if bundle.image_digest:
        out["image_digest"] = bundle.image_digest
    if bundle.methylgrapher_version:
        out["methylgrapher_version"] = bundle.methylgrapher_version
    if bundle.vg_version:
        out["vg_version"] = bundle.vg_version
    out["engine"] = bundle.engine
    out["align_engine"] = bundle.align_engine
    out["directional"] = "1" if bundle.directional else "0"
    return out


def _docker_bin() -> str:
    return shutil.which("docker") or "docker"


def _resolve_image(bundle: MethylGrapherWgbsBundle) -> str:
    image = (
        bundle.image
        or os.environ.get(METHYLGRAPHER_IMAGE_ENV, "").strip()
        or os.environ.get("METHYL_METHYLGRAPHER_IMAGE", "").strip()
    )
    if not image and bundle.engine == "mojo":
        image = _DEFAULT_MOJO_IMAGE
    if not image:
        raise RuntimeError(
            "methylGrapher image not configured "
            f"(resolvedConfig.image or {METHYLGRAPHER_IMAGE_ENV})"
        )
    return image


def _find_paired_fastqs(sample_dir: Path, sample_id: str) -> Tuple[Path, Path]:
    from methyl_worker.parabricks_runner import resolve_paired_fastqs

    pair = resolve_paired_fastqs(sample_dir, sample_id)
    if len(pair) < 2:
        raise RuntimeError(f"Need paired FASTQs for {sample_id} in {sample_dir}")
    return pair[0], pair[1]


def _append_log(log_path: Path, text: str) -> None:
    from methyl_worker.work_share import append_work_text

    append_work_text(log_path, text.rstrip() + "\n")


def _align_docker_user() -> str:
    """Docker ``--user`` for Align/Extract writes on shared NFS ``/work``.

    Always prefer ``1000:1000`` so a sister that runs the worker as root does
    not leave root-owned GAFs/BAMs that other hosts cannot chmod. Fleet hosts
    all appear as ``ubuntu`` but have different numeric uids on NFS — pair this
    with umask ``002`` inside the container and :func:`share_work_path` so mode
    ``0600`` never blocks another sister.
    Override with ``METHYL_ALIGN_DOCKER_USER=uid:gid`` when needed.
    """
    explicit = (os.environ.get("METHYL_ALIGN_DOCKER_USER") or "").strip()
    if explicit:
        return explicit
    return "1000:1000"


def _with_umask_002(cmd: Sequence[str]) -> List[str]:
    """Run ``cmd`` under ``umask 002`` so new files are group/other-readable."""
    return ["sh", "-c", 'umask 002; exec "$@"', "sh", *cmd]


def _ensure_align_workdir_writable(work_dir: Path) -> None:
    """Drop Align stubs that uid 1000 cannot rewrite.

    Sisters often run the worker as root while Align docker uses ``1000:1000``.
    ``os.access(..., W_OK)`` as root is a false green light on root-owned files,
    so we always clear rewrite targets and prefer ownership by the docker uid.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    docker_uid = 1000
    try:
        docker_uid = int(_align_docker_user().split(":", 1)[0])
    except ValueError:
        pass
    # Always clear log/report stubs — Align reopens them with mode "w".
    always_clear = (
        "alignment.Ref_*.log",
        "report.txt",
        "MojoGiraffe*.log",
    )
    for pattern in always_clear:
        for path in work_dir.glob(pattern):
            try:
                path.unlink(missing_ok=True)
                logger.info("Cleared Align stub %s", path)
            except OSError as exc:
                logger.warning("Could not clear Align stub %s: %s", path, exc)
    # Incomplete Mojo GAF shards: drop if not owned by docker uid.
    for path in work_dir.glob("alignment.mojo.Ref_*.gaf"):
        try:
            if path.stat().st_uid == docker_uid:
                continue
            path.unlink(missing_ok=True)
            logger.info("Removed foreign-owned Mojo GAF stub %s", path)
        except OSError as exc:
            logger.warning("Could not clear Mojo GAF stub %s: %s", path, exc)
    try:
        if work_dir.stat().st_uid != docker_uid:
            os.chown(work_dir, docker_uid, docker_uid)
    except OSError:
        pass


def _nvidia_hbm_free_total_gib() -> tuple[float, float] | None:
    """Return ``(free_gib, total_gib)`` for GPU 0, or ``None`` if unreadable."""
    try:
        ps = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.free,memory.total",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if ps.returncode != 0 or not (ps.stdout or "").strip():
        return None
    try:
        parts = (ps.stdout or "").strip().splitlines()[0].split(",")
        free_mib = float(parts[0].strip())
        total_mib = float(parts[1].strip())
    except (ValueError, IndexError):
        return None
    return free_mib / 1024.0, total_mib / 1024.0


def _hbm_free_fraction() -> float | None:
    """Operator-pinned share of total HBM that must be free before/after GPU work.

    Set ``METHYL_GPU_HBM_FREE_FRACTION`` in ``worker.env`` / site deploy (e.g. ``0.90``).
    Absolute legacy ``METHYL_GPU_ALIGN_MIN_FREE_GIB`` remains supported when fraction
    is unset. Neither set → ``None`` (caller decides).
    """
    raw = os.environ.get("METHYL_GPU_HBM_FREE_FRACTION", "").strip()
    if not raw:
        return None
    try:
        frac = float(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"METHYL_GPU_HBM_FREE_FRACTION must be a float in (0, 1], got {raw!r}"
        ) from exc
    if not (0.0 < frac <= 1.0):
        raise RuntimeError(
            f"METHYL_GPU_HBM_FREE_FRACTION must be in (0, 1], got {frac}"
        )
    return frac


def _admission_min_free_gib(total_gib: float) -> float:
    """Minimum free HBM (GiB) required to admit / verify release of a GPU action."""
    frac = _hbm_free_fraction()
    if frac is not None:
        return float(total_gib) * frac
    legacy = os.environ.get("METHYL_GPU_ALIGN_MIN_FREE_GIB", "").strip()
    if legacy:
        return float(legacy)
    raise RuntimeError(
        "GPU HBM admission is not configured: set METHYL_GPU_HBM_FREE_FRACTION "
        "(preferred, e.g. 0.90) or legacy METHYL_GPU_ALIGN_MIN_FREE_GIB in "
        "worker.env / site deploy."
    )


# methylGrapher GPU docker argv markers that hold DeviceContext / HBM.
_GPU_ORPHAN_CMD_MARKERS = (
    "Align",
    "MojoGiraffe",
    "MojoFq2bamMeth",
)


def _is_orphan_gpu_container(image: str, cmd: str) -> bool:
    """True when a running container is a leftover methylGrapher GPU job."""
    if "methylgrapher" not in (image or "").lower():
        return False
    return any(marker in (cmd or "") for marker in _GPU_ORPHAN_CMD_MARKERS)


def _kill_orphan_gpu_containers() -> list[str]:
    """Kill leftover methylGrapher GPU containers (Align *and* MojoGiraffe QC)."""
    ps = subprocess.run(
        [
            _docker_bin(),
            "ps",
            "--format",
            "{{.ID}}\t{{.Image}}\t{{.Command}}",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    killed: list[str] = []
    for ln in (ps.stdout or "").splitlines():
        parts = ln.split("\t", 2)
        if len(parts) < 3:
            continue
        cid, image, cmd = parts[0], parts[1], parts[2]
        if not _is_orphan_gpu_container(image, cmd):
            continue
        subprocess.run(
            [_docker_bin(), "kill", cid],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        killed.append(cid)
    return killed


# Back-compat alias for callers/tests that still import the old name.
_kill_orphan_align_containers = _kill_orphan_gpu_containers


def _wait_host_gpu_hbm(device: str, *, timeout_s: float, purpose: str) -> None:
    """Block until free HBM ≥ configured fraction (or legacy absolute GiB) of total."""
    dev = (device or "").strip().lower()
    if dev not in {"nvidia", "cuda"}:
        return
    deadline = time.monotonic() + timeout_s
    last_free: float | None = None
    last_need: float | None = None
    while True:
        pair = _nvidia_hbm_free_total_gib()
        if pair is not None:
            free, total = pair
            last_free = free
            need = _admission_min_free_gib(total)
            last_need = need
            if free >= need:
                logger.info(
                    "GPU HBM %s: free=%.1f GiB / total=%.1f GiB (need ≥%.1f)",
                    purpose,
                    free,
                    total,
                    need,
                )
                return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"GPU HBM not ready for {purpose}: need ≥"
                f"{last_need if last_need is not None else '?'} GiB free, "
                f"have {last_free if last_free is not None else 'unknown'} GiB after "
                f"{timeout_s:.0f}s. Kill leftover Mojo/Align/MojoGiraffe on this host; "
                "set METHYL_GPU_HBM_FREE_FRACTION in worker.env."
            )
        time.sleep(2.0)


def _reclaim_gpu_hbm(device: str, *, timeout_s: float) -> None:
    """Kill orphan GPU containers, then wait until HBM is free again."""
    killed = _kill_orphan_gpu_containers()
    if killed:
        logger.warning("Reclaimed orphan GPU container(s): %s", killed)
        time.sleep(3.0)
    _wait_host_gpu_hbm(device, timeout_s=timeout_s, purpose="release")


@contextmanager
def _gpu_align_lock(device: str) -> Iterator[None]:
    """Serialize one GPU Align/QC docker on a node and always release HBM after.

    Host flock path is local (not NFS) so sisters do not block each other.
    CPU Align skips the lock.

    On enter: kill orphan methylGrapher GPU containers, wait until free HBM is
    at least ``METHYL_GPU_HBM_FREE_FRACTION`` of total (or legacy absolute GiB).
    On exit (success *or* failure): kill orphans again and wait for the same
    free fraction so the next action sees a clean card.

    Catalog ``dispatch.exclusive_worker`` / ``max_per_worker`` gate claims in
    ``wf.sp_worker_request_task``; this flock covers local races / orphans.
    """
    dev = (device or "").strip().lower()
    if dev not in {"nvidia", "cuda", "amd", "hip", "rocm"}:
        yield
        return
    lock_dir = Path(
        os.environ.get("METHYL_GPU_ALIGN_LOCK_DIR", "/tmp/methyl-gpu-align")
    )
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "align.lock"
    # Seconds to wait for HBM admit/release. Operator-set in worker.env; empty
    # means "not configured" (fail closed — no silent Python default).
    wait_raw = os.environ.get("METHYL_GPU_ALIGN_HBM_WAIT_S", "").strip()
    if not wait_raw:
        raise RuntimeError(
            "METHYL_GPU_ALIGN_HBM_WAIT_S is not set (seconds to wait for HBM "
            "admit/release). Pin it in worker.env (see deploy/env/worker.env.example)."
        )
    wait_s = float(wait_raw)
    with lock_path.open("a+", encoding="utf-8") as fh:
        logger.info("Acquiring GPU Align flock %s (device=%s)", lock_path, dev)
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            killed = _kill_orphan_gpu_containers()
            if killed:
                logger.warning("Killed orphan GPU container(s): %s", killed)
                time.sleep(3.0)
            _wait_host_gpu_hbm(dev, timeout_s=wait_s, purpose="admit")
            fh.seek(0)
            fh.truncate()
            fh.write(f"pid={os.getpid()}\ndevice={dev}\n")
            fh.flush()
            yield
        finally:
            try:
                _reclaim_gpu_hbm(dev, timeout_s=wait_s)
            except Exception:
                logger.exception(
                    "GPU HBM release wait failed after Align/QC; "
                    "next admit may re-kill orphans"
                )
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _run(
    cmd: Sequence[str],
    log_path: Path,
    *,
    step: str,
    stdout_path: Path | None = None,
) -> None:
    """Run a command, optionally redirecting binary stdout to ``stdout_path``.

    When ``stdout_path`` is set (e.g. ``vg surject -b`` BAM), stdout is written
    as raw bytes and must not use ``text=True`` / ``capture_output``.
    Honors the bound :class:`~methyl_worker.execution_handle.ExecutionHandle` for STOP.
    """
    from methyl_worker.execution_handle import (
        WorkerStoppedError,
        current_handle,
        run_cancellable,
    )

    logger.info("%s: %s", step, " ".join(shlex.quote(c) for c in cmd))
    _append_log(log_path, "COMMAND: " + " ".join(shlex.quote(c) for c in cmd))
    handle = current_handle()
    if stdout_path is not None:
        # stdout goes to a file; still drain stderr via communicate(timeout=…)
        # so a chatty child cannot fill the stderr pipe and deadlock.
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        with stdout_path.open("wb") as out_fh:
            proc = subprocess.Popen(
                list(cmd),
                stdout=out_fh,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            if handle is not None:
                handle.register_process(proc)
            stderr_raw = b""
            while True:
                try:
                    _out, stderr_raw = proc.communicate(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    if handle is not None and handle.is_cancelled:
                        handle.kill_children()
                        try:
                            proc.communicate(timeout=5)
                        except Exception:
                            pass
                        raise WorkerStoppedError(f"Stopped during {step}") from None
        stderr_text = (stderr_raw or b"").decode("utf-8", errors="replace")
        if stderr_text:
            _append_log(log_path, f"[{step}] stderr:\n{stderr_text}")
        if proc.returncode != 0:
            raise RuntimeError(
                stderr_text.strip()
                or f"methylGrapher step {step} failed (rc={proc.returncode})"
            )
        if not stdout_path.is_file() or stdout_path.stat().st_size == 0:
            raise RuntimeError(f"{step} produced empty stdout file: {stdout_path}")
        return

    try:
        proc = run_cancellable(list(cmd), handle=handle)
    except WorkerStoppedError:
        raise
    if proc.stdout:
        _append_log(log_path, f"[{step}] stdout:\n{proc.stdout}")
    if proc.stderr:
        _append_log(log_path, f"[{step}] stderr:\n{proc.stderr}")
    if proc.returncode != 0:
        raise RuntimeError(
            proc.stderr.strip()
            or proc.stdout.strip()
            or f"methylGrapher step {step} failed (rc={proc.returncode})"
        )


def effective_align_engine(bundle: MethylGrapherWgbsBundle) -> str:
    """Align engine from baked ``resolvedConfig`` only (no host env override)."""
    return bundle.align_engine


def effective_qc_bam_engine(bundle: MethylGrapherWgbsBundle) -> str:
    """QC BAM engine: explicit ``qc_bam_engine``, else mojo when engine=mojo, else vg."""
    raw = (bundle.qc_bam_engine or "").strip().lower()
    if raw in {"mojo", "mojo_giraffe", "gpu_giraffe"}:
        return "mojo"
    if raw in {"vg", "cpu_vg"}:
        return "vg"
    return "mojo" if bundle.engine == "mojo" else "vg"


def effective_qc_bam_fallback(bundle: MethylGrapherWgbsBundle) -> str:
    """QC BAM failure policy: ``error`` (fail closed) or ``vg``.

    When unset: ``error`` for mojo QC / mojo engine (keep QC off multi-hour vg);
    ``vg`` only when QC engine is already vg.
    """
    raw = (bundle.qc_bam_fallback or "").strip().lower()
    if raw in {"error", "fail", "fail_closed", "none", "off"}:
        return "error"
    if raw in {"vg", "cpu_vg", "giraffe"}:
        return "vg"
    if effective_qc_bam_engine(bundle) == "mojo" or bundle.engine == "mojo":
        return "error"
    return "vg"


def resolve_giraffe_device(bundle: MethylGrapherWgbsBundle) -> str:
    """Resolve ``auto|nvidia|amd|cpu`` for the container.

    Probe on the **host** when config says ``auto`` so the container never runs
    ``nvidia-smi`` without ``--gpus`` (FileNotFoundError fail-fast).
    """
    raw = (bundle.giraffe_device or "auto").strip().lower() or "auto"
    if raw in {"cuda"}:
        return "nvidia"
    if raw in {"hip", "rocm"}:
        return "amd"
    if raw in {"cpu", "nvidia", "amd"}:
        return raw
    # auto — host probe
    if shutil.which("nvidia-smi"):
        try:
            proc = subprocess.run(
                ["nvidia-smi", "-L"],
                capture_output=True,
                check=False,
            )
            if proc.returncode == 0:
                return "nvidia"
        except OSError:
            pass
    if shutil.which("rocm-smi"):
        try:
            proc = subprocess.run(
                ["rocm-smi", "--showproductname"],
                capture_output=True,
                check=False,
            )
            if proc.returncode == 0:
                return "amd"
        except OSError:
            pass
    if shutil.which("rocminfo"):
        try:
            proc = subprocess.run(
                ["rocminfo"],
                capture_output=True,
                check=False,
            )
            if proc.returncode == 0:
                return "amd"
        except OSError:
            pass
    return "cpu"


def align_docker_gpu_flags(device: str) -> List[str]:
    """Docker device flags for Mojo Align / MojoGiraffe QC."""
    dev = (device or "cpu").strip().lower()
    if dev == "nvidia":
        return ["--gpus", "all"]
    if dev == "amd":
        return [
            "--device=/dev/kfd",
            "--device=/dev/dri",
            "--group-add",
            "video",
        ]
    return []


def mojo_segments_cache_path(bundle: MethylGrapherWgbsBundle) -> Path:
    """Resolved Mojo segment-pack cache dir (site/profile ``mojo_segments_cache``)."""
    return Path(
        (bundle.mojo_segments_cache or "").strip() or "/work/cache/mojo_segments"
    )


def add_mojo_segment_cache_mounts(
    mount_roots: set, bundle: MethylGrapherWgbsBundle
) -> None:
    """Ensure MojoGiraffe can read/build segment packs inside Docker.

    Must run for **both** science Align and QC MojoGiraffe. When science GAF is
    reused, Align's docker block is skipped — without this, QC still sets
    ``MOJO_ALIGN_SEGMENTS_CACHE`` but never mounts it, and Mojo fails with
    ``Permission denied: '/work'`` (node 890).
    """
    cache_root = mojo_segments_cache_path(bundle)
    try:
        mount_roots.add(cache_root.resolve())
    except OSError:
        mount_roots.add(cache_root)
    for extra in (Path("/work/cache"), Path("/lambda/nfs/Work/cache")):
        if extra.is_dir():
            try:
                mount_roots.add(extra.resolve())
            except OSError:
                mount_roots.add(extra)


def materialize_align_docker_env(bundle: MethylGrapherWgbsBundle) -> List[str]:
    """Map resolvedConfig Mojo/GPU knobs to container ``-e KEY=VAL`` pairs.

    Host ``METHYLGRAPHER_*`` is intentionally ignored on the worker path so fleet
    control stays in DB-backed ``resolvedConfig``. Device ``auto`` is resolved on
    the host before baking env so in-container probes are not required.
    """
    fallback = (bundle.gpu_giraffe_fallback or "mojo").strip() or "mojo"
    device = resolve_giraffe_device(bundle)
    if bundle.mojo_giraffe_ready is None:
        ready = "1"
    else:
        ready = "1" if bundle.mojo_giraffe_ready else "0"
    segments = (
        (bundle.mojo_segments_cache or "").strip() or "/work/cache/mojo_segments"
    )
    modular = (bundle.modular_cache_dir or "").strip() or "/tmp/modular_cache"
    # Serialize C2T/G2A on GPU DeviceContext unless operator explicitly opts in.
    if bundle.dual_graph_parallel is True:
        dual_parallel = "1"
    elif bundle.dual_graph_parallel is False:
        dual_parallel = "0"
    elif device in {"nvidia", "amd"}:
        dual_parallel = "0"
    else:
        dual_parallel = "1"
    env = [
        f"METHYLGRAPHER_ALIGN_ENGINE={effective_align_engine(bundle)}",
        f"METHYLGRAPHER_GPU_GIRAFFE_FALLBACK={fallback}",
        f"METHYLGRAPHER_GIRAFFE_DEVICE={device}",
        f"METHYLGRAPHER_ALIGN_DEVICE={device}",
        f"MOJO_ALIGN_GIRAFFE_READY={ready}",
        f"MODULAR_CACHE_DIR={modular}",
        f"MOJO_ALIGN_SEGMENTS_CACHE={segments}",
        f"METHYLGRAPHER_DUAL_GRAPH_PARALLEL={dual_parallel}",
        # Fail closed when nvidia/amd DeviceContext cannot be created (no silent CPU).
        "METHYLGRAPHER_GPU_REQUIRE=1",
        # Align orchestration prints progress to stdout; worker capture is often ascii.
        "PYTHONIOENCODING=utf-8",
        "PYTHONUTF8=1",
        "LANG=C.UTF-8",
    ]
    # Older NVIDIA drivers (<580) need system ptxas for Mojo CUDA create.
    ptxas = (
        os.environ.get("MODULAR_NVPTX_COMPILER_PATH", "").strip()
        or f"{INSTALL_PREFIX}/cuda/bin/ptxas"
    )
    env.append(f"MODULAR_NVPTX_COMPILER_PATH={ptxas}")
    # Host HBM free-fraction → container preflight budget (same operator pin).
    hbm_frac = os.environ.get("METHYL_GPU_HBM_FREE_FRACTION", "").strip()
    if hbm_frac:
        env.append(f"METHYLGRAPHER_GPU_HBM_FRACTION={hbm_frac}")
        # The dual-graph C2T→G2A handoff wait reads an absolute GiB floor, which a
        # fixed number turns into an unreachable target on any GPU whose HBM is not
        # the size it was written for. Derive it from this device's real total so the
        # pin stays a share of what we actually have.
        handoff_pin = os.environ.get(
            "METHYLGRAPHER_GRAPH_HANDOFF_FREE_GIB", ""
        ).strip()
        if handoff_pin:
            env.append(f"METHYLGRAPHER_GRAPH_HANDOFF_FREE_GIB={handoff_pin}")
        elif device in {"nvidia", "cuda"}:
            pair = _nvidia_hbm_free_total_gib()
            if pair is not None:
                handoff = _admission_min_free_gib(pair[1])
                env.append(
                    f"METHYLGRAPHER_GRAPH_HANDOFF_FREE_GIB={handoff:.2f}"
                )
    return env


def build_align_command(
    *,
    bundle: MethylGrapherWgbsBundle,
    work_dir: Path,
    fq1: Path,
    fq2: Path,
    index_prefix: str,
) -> List[str]:
    """Construct methylGrapher Align argv (host or container-inner)."""
    threads = bundle.threads or max(1, (os.cpu_count() or 4) // 2)
    cmd = [
        os.environ.get(METHYLGRAPHER_BIN_ENV, "").strip() or "methylGrapher",
        "Align",
        "-t",
        str(threads),
        "-work_dir",
        str(work_dir),
        "-index_prefix",
        index_prefix,
        "-fq1",
        str(fq1),
        "-fq2",
        str(fq2),
        "-directional",
        "Y" if bundle.directional else "N",
        "-align_engine",
        effective_align_engine(bundle),
    ]
    return cmd


def build_methylcall_command(
    *,
    bundle: MethylGrapherWgbsBundle,
    work_dir: Path,
    index_prefix: str,
) -> List[str]:
    threads = bundle.threads or max(1, (os.cpu_count() or 4) // 2)
    # Stock python dual-GFA cliff; mojo engine always uses a single GFA worker.
    if bundle.engine != "mojo" and threads > _METHYLCALL_THREAD_CAP:
        logger.warning(
            "MethylCall threads=%s exceeds safety cap %s (avoids dual in-memory GFA "
            "workers); clamping",
            threads,
            _METHYLCALL_THREAD_CAP,
        )
        threads = _METHYLCALL_THREAD_CAP
    cmd = [
        os.environ.get(METHYLGRAPHER_BIN_ENV, "").strip() or "methylGrapher",
        "MethylCall",
        "-t",
        str(threads),
        "-work_dir",
        str(work_dir),
        "-index_prefix",
        index_prefix,
        "-cg_only",
        "Y" if bundle.cg_only else "N",
    ]
    if bundle.batch_size is not None:
        cmd.extend(["-batch_size", str(int(bundle.batch_size))])
    return cmd


def build_qc_bam_command(
    *,
    bundle: MethylGrapherWgbsBundle,
    fq1_c2t: Path,
    fq2_g2a: Path,
    threads: int | None = None,
) -> List[str]:
    """Build ``vg giraffe`` argv emitting a GRCh38-surjected BAM on **stdout**.

    The QC BAM must not be produced by surjecting methylGrapher's science GAF
    (named-coordinates ≠ vg surject node IDs). Re-map converted reads with
    ``-o BAM --ref-paths``. Rollback path when ``qc_bam_engine=vg``.
    """
    vg = os.environ.get(VG_BIN_ENV, "").strip() or "vg"
    n_threads = threads if threads is not None else max(1, (os.cpu_count() or 4) // 2)
    return [
        vg,
        "giraffe",
        "-p",
        "-t",
        str(n_threads),
        "-o",
        "BAM",
        "--ref-paths",
        str(bundle.ref_paths),
        "-Z",
        str(bundle.c2t_gbz),
        "-d",
        str(bundle.c2t_dist),
        "-m",
        str(bundle.c2t_min),
        "-z",
        str(bundle.c2t_zipcodes),
        "-f",
        str(fq1_c2t),
        "-f",
        str(fq2_g2a),
    ]


def resolve_grch38_offsets_dir(
    bundle: MethylGrapherWgbsBundle, index_prefix: str
) -> Path:
    """Directory with grch38-dense-v1 offsets (``meta.json`` + ``records.bin``).

    Search order: ``MOJO_ALIGN_SEGMENT_OFFSETS``,
    ``{wl.gfa}.grch38_offsets``, cache siblings under mojo_segments.
    """

    def _ready(cand: Path) -> bool:
        return (cand / "meta.json").is_file() and (cand / "records.bin").is_file()

    env = mojo_align_getenv("SEGMENT_OFFSETS")
    candidates: List[Path] = []
    if env:
        env_p = Path(env).expanduser()
        if _ready(env_p):
            return env_p.resolve()
        candidates.append(env_p)
    wl = resolve_wl_gfa_path(bundle, index_prefix)
    candidates.append(Path(str(wl) + ".grch38_offsets"))
    candidates.append(wl.parent / f"{wl.name}.grch38_offsets")
    cache_roots = [
        Path("/work/cache/mojo_segments"),
        Path("/lambda/nfs/Work/cache/mojo_segments"),
    ]
    try:
        cache_roots.append(mojo_segments_cache_path(bundle))
    except Exception:
        pass
    for root in cache_roots:
        candidates.append(root / "hprc-d9-bs.wl.grch38_offsets")
        candidates.append(root / f"{Path(index_prefix).name}.wl.grch38_offsets")
    for cand in candidates:
        if _ready(cand):
            return cand.resolve()
    raise RuntimeError(
        "Mojo QC SAM requires grch38-dense-v1 offsets "
        "(run mojo-align/giraffe/scripts/build_grch38_offsets.py). "
        f"Searched: {', '.join(str(c) for c in candidates)}"
    )


def build_qc_bam_mojo_sam_command(
    *,
    bundle: MethylGrapherWgbsBundle,
    fq1_c2t: Path,
    fq2_g2a: Path,
    out_sam: Path,
    segment_offsets: Path,
) -> List[str]:
    """MojoGiraffe QC remap → linear SAM (native emit; no host packer)."""
    mg = os.environ.get(METHYLGRAPHER_BIN_ENV, "").strip() or "methylGrapher"
    device = (bundle.giraffe_device or "nvidia").strip() or "nvidia"
    return [
        mg,
        "MojoGiraffe",
        "-gbz",
        str(bundle.c2t_gbz),
        "-dist",
        str(bundle.c2t_dist),
        "-min",
        str(bundle.c2t_min),
        "-zipcodes",
        str(bundle.c2t_zipcodes),
        "-fq1",
        str(fq1_c2t),
        "-fq2",
        str(fq2_g2a),
        "-out_sam",
        str(out_sam),
        "-segment_offsets",
        str(segment_offsets),
        "-device",
        device,
    ]


def add_mojo_src_overlay_mounts(docker_cmd: List[str]) -> None:
    """Bind-mount host Mojo src/engine hotfixes into the Align/QC container."""
    overlay = mojo_align_overlay_dir()
    src_overlay = overlay / "src"
    if src_overlay.is_dir():
        for name in (
            "giraffe_gaf_emit.mojo",
            "giraffe_sam_emit.mojo",
            "giraffe_mapper.mojo",
            "giraffe_stream_map.mojo",
            "giraffe_gpu_map_kernels.mojo",
            "main.mojo",
            "mcall.mojo",
        ):
            p = src_overlay / name
            if p.is_file():
                docker_cmd.extend(
                    ["-v", f"{p}:{INSTALL_PREFIX}/src/{name}:ro"]
                )
    eng_dir = overlay / "engine"
    if eng_dir.is_dir():
        for name in (
            "mcall.py",
            "grch38_offsets.py",
            "qc_sam_state.py",
            "qc_sam_emit.py",
            "gpu_mem.py",
            "alignments.py",
            "named_coords.py",
            "quartet_map.py",
        ):
            p = eng_dir / name
            if p.is_file():
                docker_cmd.extend(
                    ["-v", f"{p}:{INSTALL_PREFIX}/engine/{name}:ro"]
                )


def _mojo_named_coords_paths() -> Tuple[Path, Path]:
    """Return (translate_script, pythonpath_root) for GBZ→GFA GAF rewrite.

    Resolves only via ``MOJO_ALIGN_ROOT``, overlay env, in-image
    ``/opt/mojo-align``, and ``/work/goliath/images/*`` mounts.
    Hardcoded developer home paths are not consulted.
    """
    overlay = mojo_align_overlay_dir()
    scripts_root = (os.environ.get("METHYLGRAPHER_SCRIPTS") or "").strip()
    mojo_root = mojo_align_getenv("ROOT")

    candidates: List[Tuple[Path, Path]] = []
    if scripts_root:
        root = Path(scripts_root)
        candidates.append((root / "translate_mojo_gaf_named_coords.py", root.parent))
        candidates.append((root / "scripts" / "translate_mojo_gaf_named_coords.py", root))
    if mojo_root:
        root = Path(mojo_root)
        # Flat staged tree (stage_flat_image_tree.sh) or package monorepo layout
        candidates.extend(
            [
                (root / "scripts" / "translate_mojo_gaf_named_coords.py", root),
                (root / "_flat_image" / "scripts" / "translate_mojo_gaf_named_coords.py", root / "_flat_image"),
                (
                    root / "giraffe" / "scripts" / "translate_mojo_gaf_named_coords.py",
                    root / "methylgrapher",
                ),
            ]
        )
    candidates.extend(
        [
            (overlay / "scripts" / "translate_mojo_gaf_named_coords.py", overlay),
            (
                Path(f"{INSTALL_PREFIX}/scripts/translate_mojo_gaf_named_coords.py"),
                Path(INSTALL_PREFIX),
            ),
            (
                Path(
                    "/work/goliath/images/mojo_named_coords/scripts/"
                    "translate_mojo_gaf_named_coords.py"
                ),
                Path("/work/goliath/images/mojo_named_coords"),
            ),
        ]
    )
    for script, py_root in candidates:
        if not script.is_file():
            continue
        if (py_root / "engine" / "named_coords.py").is_file():
            return script, py_root
        # Flat overlay without engine/ at py_root — accept parent of scripts/
        if (script.parent.parent / "engine" / "named_coords.py").is_file():
            return script, script.parent.parent
    searched = [str(c[1]) for c in candidates]
    raise FileNotFoundError(
        "translate_mojo_gaf_named_coords.py + engine/named_coords.py not found. "
        "Set MOJO_ALIGN_ROOT to a mojo-align checkout (or flat staged tree), "
        "or MOJO_ALIGN_OVERLAY / METHYLGRAPHER_SCRIPTS, or bake the script "
        f"into the methylgrapher image under {INSTALL_PREFIX}. Searched: {searched}"
    )


def _named_coords_index_dir(bundle: "MethylGrapherWgbsBundle") -> Path:
    cache = (
        (bundle.mojo_segments_cache or "").strip()
        or mojo_align_getenv("SEGMENTS_CACHE")
        or "/work/cache/mojo_segments"
    )
    return Path(cache) / "hprc-d9-bs.wl.gbz_to_gfa.named_coords"


def _gaf_has_ids_above(gaf_path: Path, max_id: int, *, sample_lines: int = 50000) -> bool:
    """True if any GAF path node id exceeds ``max_id`` (sampled)."""
    try:
        with gaf_path.open("r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= sample_lines:
                    break
                parts = line.split("\t")
                if len(parts) < 6:
                    continue
                for tok in parts[5].replace("<", ">").split(">"):
                    if tok.isdigit() and int(tok) > max_id:
                        return True
    except OSError:
        return False
    return False


def _gaf_needs_gbz_to_gfa(work_dir: Path, gaf_path: Path, index_dir: Path) -> bool:
    """True when GAF still carries GBZ chopped-node ids (Mojo emit).

    Decision is ID-space based (any path node > max GFA segment). Do **not**
    re-translate merely because Mojo shard files exist or the GAF was touched
    after the stamp — that double-applies GBZ→GFA and corrupts paths.
    """
    stamp = Path(str(gaf_path) + ".named_coords.json")
    if stamp.is_file() and stamp.stat().st_mtime >= gaf_path.stat().st_mtime:
        return False
    meta_path = index_dir / "meta.json"
    max_gfa = 0
    if meta_path.is_file():
        try:
            max_gfa = int(
                json.loads(meta_path.read_text(encoding="utf-8")).get(
                    "max_gfa_segment"
                )
                or 0
            )
        except (OSError, ValueError, json.JSONDecodeError):
            max_gfa = 0
    if max_gfa > 0:
        return _gaf_has_ids_above(gaf_path, max_gfa)
    # No index meta: only translate when Mojo shards exist and no stamp yet.
    return any(work_dir.glob("alignment.mojo.Ref_*.gaf")) and not stamp.is_file()


def ensure_gaf_named_coordinates(
    gaf_path: Path,
    work_dir: Path,
    bundle: "MethylGrapherWgbsBundle",
    log_path: Path,
) -> None:
    """Rewrite Mojo GAF paths to GFA named-coordinates before MethylCall.

    Align packs use ``vg convert --no-translation`` (GBZ node ids). MethylCall
    + cpg.tsv use ``*.wl.gfa`` segment ids. Skip cpu_vg GAFs already in GFA space.
    """
    if not gaf_path.is_file() or gaf_path.stat().st_size <= 0:
        return
    index_dir = _named_coords_index_dir(bundle)
    if not (index_dir / "nodes.bin").is_file():
        if any(work_dir.glob("alignment.mojo.Ref_*.gaf")):
            raise RuntimeError(
                "Mojo GAF present but GBZ→GFA named_coords index missing: "
                f"{index_dir} (build via scripts/build_named_coords_index.py)"
            )
        return
    if not _gaf_needs_gbz_to_gfa(work_dir, gaf_path, index_dir):
        return
    script, py_root = _mojo_named_coords_paths()
    cmd = [
        os.environ.get("PYTHON", "").strip() or "python3",
        str(script),
        str(gaf_path),
        "--index",
        str(index_dir),
    ]
    env = os.environ.copy()
    prev = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(py_root) + (os.pathsep + prev if prev else "")
    _append_log(
        log_path,
        f"NAMED_COORDS: translating {gaf_path} via {index_dir}",
    )
    logger.info("mojo.gaf_named_coords: %s", " ".join(shlex.quote(c) for c in cmd))
    _append_log(log_path, "COMMAND: " + " ".join(shlex.quote(c) for c in cmd))
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if proc.stdout:
        _append_log(log_path, f"[mojo.gaf_named_coords] stdout:\n{proc.stdout}")
    if proc.stderr:
        _append_log(log_path, f"[mojo.gaf_named_coords] stderr:\n{proc.stderr}")
    if proc.returncode != 0:
        raise RuntimeError(
            (proc.stderr or proc.stdout or "").strip()
            or f"mojo.gaf_named_coords failed (rc={proc.returncode})"
        )
    share_work_path(gaf_path)
    stamp = Path(str(gaf_path) + ".named_coords.json")
    if stamp.is_file():
        share_work_path(stamp)


def samtools_sam_to_bam(sam_path: Path, bam_path: Path, log_path: Path) -> None:
    """Convert Mojo QC SAM → unsorted BAM (host samtools; streaming read)."""
    if not sam_path.is_file() or sam_path.stat().st_size == 0:
        raise RuntimeError(f"Mojo QC SAM missing or empty: {sam_path}")
    _run(
        ["samtools", "view", "-b", "-o", str(bam_path), str(sam_path)],
        log_path,
        step="samtools.qc_sam_to_bam",
    )
    if not bam_path.is_file() or bam_path.stat().st_size == 0:
        raise RuntimeError(f"samtools view wrote empty BAM: {bam_path}")
    share_work_path(bam_path)


def ensure_qc_bam_read_group(
    bam_path: Path,
    sample_id: str,
    log_path: Path,
) -> bool:
    """Ensure QC BAM has ``@RG`` with ``LB`` (Clara ``collectmultiplemetrics``).

    Linear ``pbrun giraffe`` / ``fq2bam_meth`` emit ``--read-group-library=library``.
    Mojo QC SAM→BAM has no RG; Clara then fails with
    ``Record contains library that is missing from header``. Same stamp as giraffe
    (ID/SM/LB/PL/PU). Returns True when a rewrite ran.
    """
    if not bam_path.is_file() or bam_path.stat().st_size == 0:
        return False
    # Serialize rewrites — concurrent addreplacerg on the same BAM (fleet +
    # backfill) can leave a truncated .rg.tmp / corrupt replace.
    lock_path = Path(str(bam_path) + ".rg.lock")
    lock_fh = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        header = subprocess.run(
            ["samtools", "view", "-H", str(bam_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if header.returncode != 0:
            raise RuntimeError(
                f"samtools view -H failed for {bam_path}: {(header.stderr or '').strip()}"
            )
        rg_lines = [ln for ln in header.stdout.splitlines() if ln.startswith("@RG")]
        for ln in rg_lines:
            for field in ln.split("\t")[1:]:
                if field.startswith("LB:") and len(field) > 3:
                    return False  # non-empty LB already present (linear pbrun style)
        rg_id = f"{sample_id}_rg"
        tmp = bam_path.with_suffix(bam_path.suffix + ".rg.tmp")
        if tmp.is_file():
            # Stale concurrent attempt — drop before rewrite.
            tmp.unlink(missing_ok=True)
        cmd = [
            "samtools",
            "addreplacerg",
            "-r",
            f"ID:{rg_id}",
            "-r",
            f"SM:{sample_id}",
            "-r",
            "LB:library",
            "-r",
            "PL:ILLUMINA",
            "-r",
            f"PU:{sample_id}",
            "-o",
            str(tmp),
            str(bam_path),
        ]
        _run(cmd, log_path, step="samtools.addreplacerg")
        if not tmp.is_file() or tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"samtools addreplacerg wrote empty BAM: {tmp}")
        os.replace(str(tmp), str(bam_path))
        share_work_path(bam_path)
        # Re-index only coordinate-sorted BGZF BAMs (post markdup/sort QC BAMs).
        # Uncompressed ``samtools view -b`` test BAMs are SO:coordinate but not BGZF.
        if "SO:coordinate" in header.stdout:
            bai = Path(str(bam_path) + ".bai")
            bai.unlink(missing_ok=True)
            try:
                _run(
                    ["samtools", "index", str(bam_path)],
                    log_path,
                    step="samtools.index_after_rg",
                )
                share_work_path(bai)
            except RuntimeError as exc:
                logger.warning(
                    "QC BAM index after @RG skipped for %s: %s", bam_path, exc
                )
                _append_log(log_path, f"WARN: index after @RG skipped: {exc}")
        return True
    finally:
        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lock_fh.close()
        lock_path.unlink(missing_ok=True)


def mojo_qc_sam_provenance_path(qc_bam: Path) -> Path:
    """Sidecar proving ``giraffe.bam`` came from native Mojo SAM emit (not packer)."""
    return Path(str(qc_bam) + ".mojo_sam")


def write_mojo_qc_sam_provenance(qc_bam: Path, *, offsets_dir: Path) -> Path:
    marker = mojo_qc_sam_provenance_path(qc_bam)
    marker.write_text(
        json.dumps(
            {
                "engine": "mojo_sam",
                "qc_bam": str(qc_bam),
                "bytes": qc_bam.stat().st_size,
                "offsets_dir": str(offsets_dir),
                "written_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return marker


def giraffe_bam_reusable_for_mojo_qc(qc_bam: Path) -> bool:
    """Reuse only Mojo-SAM BAMs; packer-era leftovers must be regenerated."""
    return mojo_qc_sam_provenance_path(qc_bam).is_file()


def _write_bs_converted_fastq(
    src: Path,
    dst: Path,
    *,
    base_from: str,
    base_to: str,
    log_path: Path,
) -> None:
    """Stream ``src`` into its in-silico bisulfite-converted form at ``dst``.

    Mirrors the conversion methylGrapher applies before ``vg giraffe`` (R1 C→T,
    R2 G→A for directional libraries), which it does not keep on disk. Only
    sequence lines are rewritten, so names and qualities stay intact and
    :func:`_restore_original_sequences` can put the original bases back.
    """
    if src.name.endswith(".gz"):
        reader = [("pigz" if shutil.which("pigz") else "gzip"), "-dc", str(src)]
    else:
        reader = ["cat", str(src)]
    convert = ["awk", f'NR % 4 == 2 {{ gsub(/{base_from}/, "{base_to}") }} 1']
    _append_log(
        log_path,
        "COMMAND: "
        + " ".join(shlex.quote(c) for c in reader)
        + " | "
        + " ".join(shlex.quote(c) for c in convert)
        + " > "
        + shlex.quote(str(dst)),
    )
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("wb") as out_fh:
        rd = subprocess.Popen(reader, stdout=subprocess.PIPE)
        try:
            cv = subprocess.Popen(
                convert, stdin=rd.stdout, stdout=out_fh, stderr=subprocess.PIPE
            )
        finally:
            # Close our handle so the reader sees EPIPE if awk dies early.
            if rd.stdout is not None:
                rd.stdout.close()
        cv_err = (cv.communicate()[1] or b"").decode("utf-8", errors="replace")
        rc_reader = rd.wait()
    if rc_reader != 0 or cv.returncode != 0:
        raise RuntimeError(
            f"bisulfite conversion of {src.name} failed "
            f"(reader rc={rc_reader}, awk rc={cv.returncode}): {cv_err.strip()}"
        )
    if not dst.is_file() or dst.stat().st_size == 0:
        raise RuntimeError(f"bisulfite conversion produced empty FASTQ: {dst}")


def _flatten_qc_metrics_dir(metrics_dir: Path) -> None:
    """Copy nested metric files up to ``metrics_dir`` root for flat tar packaging.

    Parabricks ``collectmultiplemetrics`` may write under a subdirectory. Downstream
    ``_package_qc_tar`` only packs top-level files, and mode-aware QC looks up tables
    by basename suffix — so insert/GC/cycle/artifact files left nested would be
    omitted while ``quality_yield.txt`` alone could still mark collect as success.
    """
    if not metrics_dir.is_dir():
        return
    root = metrics_dir.resolve()
    for src in sorted(metrics_dir.rglob("*")):
        if not src.is_file():
            continue
        try:
            if src.resolve().parent == root:
                continue
        except OSError:
            continue
        dest = metrics_dir / src.name
        if dest.exists() and dest.resolve() == src.resolve():
            continue
        if dest.is_file() and dest.stat().st_size > 0 and dest.resolve() != src.resolve():
            # Prefer an existing non-empty root copy; do not clobber.
            continue
        shutil.copy2(src, dest)


def _package_qc_tar(sample_dir: Path, sample_id: str, metrics_dir: Path) -> Path:
    """Pack Picard metric text files into ``{sample_id}.qc-metrics.tar``.

    Files are added by basename (flat). Call ``_flatten_qc_metrics_dir`` first when
    collect may have written nested outputs.
    """
    qc_tar = sample_dir / f"{sample_id}.qc-metrics.tar"
    with tarfile.open(qc_tar, "w") as tar:
        if metrics_dir.is_dir():
            # Root-level files are authoritative (matches _flatten_qc_metrics_dir
            # no-clobber). Then add nested-only basenames as a safety net.
            seen: set[str] = set()
            try:
                root = metrics_dir.resolve()
            except OSError:
                root = metrics_dir
            for p in sorted(metrics_dir.iterdir(), key=lambda x: x.name):
                if not p.is_file() or p.name in seen:
                    continue
                seen.add(p.name)
                tar.add(p, arcname=p.name)
            for p in sorted(metrics_dir.rglob("*"), key=lambda x: (x.name, str(x))):
                if not p.is_file() or p.name in seen:
                    continue
                try:
                    if p.resolve().parent == root:
                        continue
                except OSError:
                    pass
                seen.add(p.name)
                tar.add(p, arcname=p.name)
    return qc_tar


def _maybe_collect_picard_metrics(
    *,
    sample_id: str,
    sample_path: Path,
    bam_path: Path,
    metrics_dir: Path,
    linear_ref_fasta: Path,
    log_path: Path,
    input_json: Optional[Mapping[str, Any]],
) -> bool:
    """Run ``pbrun collectmultiplemetrics`` on the WGBS QC BAM (soft-fail).

    Uses the **Clara Parabricks** image (``METHYL_PARABRICKS_IMAGE``), not the Mojo
    methylgrapher align image — Mojo does not ship ``pbrun``. BAM production and
    Picard metrics are separate containers.

    Returns True when ``quality_yield.txt`` (or equivalent) lands under
    ``metrics_dir``. BS-converted BAM bases can skew artifact/GC metrics;
    these tables are operational screening only.
    """
    if not bam_path.is_file() or bam_path.stat().st_size == 0:
        logger.warning(
            "Skipping collectmultiplemetrics for %s: BAM missing or empty", sample_id
        )
        return False
    if not linear_ref_fasta.is_file():
        logger.warning(
            "Skipping collectmultiplemetrics for %s: linear_ref_fasta missing (%s)",
            sample_id,
            linear_ref_fasta,
        )
        return False

    try:
        from methyl_worker.giraffe_runner import _build_collect_metrics_docker_command
        from methyl_worker.parabricks_runner import (
            ParabricksPaths,
            resolve_collectmultiplemetrics_config,
        )
    except Exception as exc:  # pragma: no cover - import surface
        logger.warning("collectmultiplemetrics unavailable (import): %s", exc)
        return False

    try:
        cfg = resolve_collectmultiplemetrics_config(
            project_path=(input_json or {}).get("projectPath")
            or (input_json or {}).get("project"),
            input_json=input_json,
        )
    except Exception as exc:
        logger.warning(
            "Skipping collectmultiplemetrics for %s: Parabricks config unresolved (%s)",
            sample_id,
            exc,
        )
        return False

    # Mojo QC BAMs lack @RG/LB; Clara requires the same library stamp as linear
    # pbrun giraffe (--read-group-library=library).
    try:
        if ensure_qc_bam_read_group(bam_path, sample_id, log_path):
            logger.info("Stamped @RG LB=library on QC BAM %s for Clara metrics", bam_path)
    except Exception as exc:
        logger.warning(
            "Skipping collectmultiplemetrics for %s: RG stamp failed (%s)",
            sample_id,
            exc,
        )
        return False

    metrics_dir.mkdir(parents=True, exist_ok=True)
    share_work_path(metrics_dir)
    paths = ParabricksPaths(
        sample_dir=sample_path,
        sample_id=sample_id,
        reference_fasta=linear_ref_fasta,
        bam_path=bam_path,
        qc_metrics_dir=metrics_dir,
        qc_metrics_tar=sample_path / f"{sample_id}.qc-metrics.tar",
        metrics_json=sample_path / f"{sample_id}.json",
        dedup_metrics=sample_path / f"{sample_id}.deduplicate_metrics.txt",
        log_path=log_path,
        tmp_dir=sample_path / "tmp",
    )
    metrics_cmd = _build_collect_metrics_docker_command(cfg, paths, linear_ref_fasta)
    logger.info(
        "Running Parabricks collectmultiplemetrics for WGBS QC BAM %s (image=%s)",
        sample_id,
        cfg.image,
    )
    _append_log(log_path, "COMMAND: " + " ".join(shlex.quote(c) for c in metrics_cmd))
    try:
        _run(metrics_cmd, log_path, step="collectmultiplemetrics")
    except Exception as exc:
        logger.warning(
            "collectmultiplemetrics failed for %s (continuing with provenance-only QC): %s",
            sample_id,
            exc,
        )
        return False

    # Always flatten nested Parabricks outputs (not only when quality_yield is missing).
    _flatten_qc_metrics_dir(metrics_dir)
    qy = metrics_dir / "quality_yield.txt"
    if not qy.is_file():
        logger.warning(
            "collectmultiplemetrics produced no quality_yield.txt for %s", sample_id
        )
        return False
    share_work_tree(metrics_dir)
    return True


def _alignment_complete(sample_dir: Path, sample_id: str) -> bool:
    bam = sample_dir / f"{sample_id}.bam"
    dedup = sample_dir / f"{sample_id}.deduplicate_metrics.txt"
    gaf = sample_dir / f"{sample_id}.alignment.gaf"
    qc_tar = sample_dir / f"{sample_id}.qc-metrics.tar"
    return bam.is_file() and dedup.is_file() and gaf.is_file() and qc_tar.is_file()


def _clear_alignment_outputs(sample_dir: Path, sample_id: str) -> None:
    for name in (
        f"{sample_id}.bam",
        f"{sample_id}.bam.bai",
        f"{sample_id}.deduplicate_metrics.txt",
        f"{sample_id}.alignment.gaf",
        f"{sample_id}.qc-metrics.tar",
        f"{sample_id}.alignment_metrics.json",
        f"{sample_id}.methylgrapher_align.log",
        f"{sample_id}.conversion_report.txt",
    ):
        path = sample_dir / name
        if path.is_file():
            path.unlink()
    work = sample_dir / "methylgrapher_work"
    if work.is_dir():
        shutil.rmtree(work, ignore_errors=True)


def _write_dedup_metrics(
    path: Path,
    sample_id: str,
    *,
    unpaired_examined: int = 0,
    read_pairs_examined: int = 0,
    unmapped_reads: int = 0,
    unpaired_duplicates: int = 0,
    read_pair_duplicates: int = 0,
    optical_duplicates: int = 0,
    percent_duplication: float = 0.0,
) -> None:
    """Write a Picard-shaped duplication metrics file methyl_qc can parse.

    The parser requires a ``METRICS CLASS`` header; a bare TSV is ignored and
    ``process_samples_to_qc_jsons`` then silently writes nothing.
    """
    path.write_text(
        "## methylGrapher WGBS QC BAM mark-duplicates metrics\n"
        f"## sample={sample_id}\n"
        "## METRICS CLASS\tpicard.sam.DuplicationMetrics\n"
        "LIBRARY\tUNPAIRED_READS_EXAMINED\tREAD_PAIRS_EXAMINED\t"
        "SECONDARY_OR_SUPPLEMENTARY_RDS\tUNMAPPED_READS\tUNPAIRED_READ_DUPLICATES\t"
        "READ_PAIR_DUPLICATES\tREAD_PAIR_OPTICAL_DUPLICATES\tPERCENT_DUPLICATION\t"
        "ESTIMATED_LIBRARY_SIZE\n"
        f"Unknown\t{unpaired_examined}\t{read_pairs_examined}\t0\t{unmapped_reads}\t"
        f"{unpaired_duplicates}\t{read_pair_duplicates}\t{optical_duplicates}\t"
        f"{percent_duplication:.6f}\t0\n",
        encoding="utf-8",
    )


def _dedup_metrics_from_markdup_stderr(stderr: str) -> Dict[str, float | int]:
    """Parse ``samtools markdup -s`` summary lines into Picard-ish counts."""
    stats: Dict[str, float | int] = {
        "unpaired_examined": 0,
        "read_pairs_examined": 0,
        "unmapped_reads": 0,
        "unpaired_duplicates": 0,
        "read_pair_duplicates": 0,
        "optical_duplicates": 0,
        "percent_duplication": 0.0,
    }
    written = 0
    dup_pair = 0
    dup_single = 0
    for line in stderr.splitlines():
        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        key = key.strip().upper()
        try:
            value = int(rest.strip().split()[0])
        except (ValueError, IndexError):
            continue
        if key == "WRITTEN":
            written = value
        elif key == "DUPLICATE PAIR":
            dup_pair = value
        elif key == "DUPLICATE SINGLE":
            dup_single = value
        elif key == "DUPLICATE PAIR OPTICAL":
            stats["optical_duplicates"] = value
    # markdup -s reports per-read WRITTEN; approximate pair counts for Picard.
    stats["read_pairs_examined"] = max(written // 2, 0)
    stats["read_pair_duplicates"] = dup_pair
    stats["unpaired_duplicates"] = dup_single
    examined = max(int(stats["read_pairs_examined"]), 1)
    stats["percent_duplication"] = float(dup_pair) / float(examined)
    return stats


def _run_capturing_stderr(
    cmd: Sequence[str],
    log_path: Path,
    *,
    step: str,
) -> str:
    """Like :func:`_run` but return decoded stderr (for markdup -s summaries)."""
    logger.info("%s: %s", step, " ".join(shlex.quote(c) for c in cmd))
    _append_log(log_path, "COMMAND: " + " ".join(shlex.quote(c) for c in cmd))
    proc = subprocess.run(list(cmd), capture_output=True, text=True, check=False)
    if proc.stdout:
        _append_log(log_path, f"[{step}] stdout:\n{proc.stdout}")
    if proc.stderr:
        _append_log(log_path, f"[{step}] stderr:\n{proc.stderr}")
    if proc.returncode != 0:
        raise RuntimeError(
            proc.stderr.strip()
            or proc.stdout.strip()
            or f"methylGrapher step {step} failed (rc={proc.returncode})"
        )
    return proc.stderr or ""


def _fastq_base_name(header_name: str) -> str:
    name = header_name.split()[0]
    if name.startswith("@"):
        name = name[1:]
    if name.endswith("/1") or name.endswith("/2"):
        name = name[:-2]
    return name


def _iter_fastq_records(path: Path):
    """Yield ``(base_name, seq, qual)`` without loading the full file."""
    try:
        from pysam import FastxFile  # type: ignore

        with FastxFile(str(path)) as fh:
            for rec in fh:
                yield _fastq_base_name(rec.name), rec.sequence, rec.quality or ""
            return
    except Exception:
        pass

    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rt") as fh:  # type: ignore[arg-type]
        while True:
            header = fh.readline()
            if not header:
                break
            seq = fh.readline().rstrip("\n")
            fh.readline()
            qual = fh.readline().rstrip("\n")
            yield _fastq_base_name(header[1:]), seq, qual


def _write_sorted_fastq_tsv(fastq: Path, out_tsv: Path, *, sort_dir: Path) -> None:
    """Stream FASTQ → ``name\\tseq\\tqual`` lines, externally sorted by name.

    Collation is ``LC_ALL=C`` lexicographic byte order — the same order as
    ``samtools sort -N`` (not natural ``-n``) and Python ``str`` comparisons used
    by the merge-join. Uses ``sort -S`` with a bounded memory budget so production
    WGBS FASTQs never materialize as Python dicts.
    """
    out_tsv.parent.mkdir(parents=True, exist_ok=True)
    sort_dir.mkdir(parents=True, exist_ok=True)
    # Bound RAM for external sort; disk spill under sort_dir.
    sort_mem = os.environ.get("METHYL_FASTQ_SORT_MEM", "2G").strip() or "2G"
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    sort_cmd = [
        "sort",
        "-t",
        "\t",
        "-k1,1",
        "-S",
        sort_mem,
        "-T",
        str(sort_dir),
        "-o",
        str(out_tsv),
    ]
    proc = subprocess.Popen(
        sort_cmd,
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert proc.stdin is not None
    try:
        for name, seq, qual in _iter_fastq_records(fastq):
            proc.stdin.write(f"{name}\t{seq}\t{qual}\n")
        proc.stdin.close()
    except Exception:
        proc.kill()
        raise
    stderr = proc.stderr.read() if proc.stderr else ""
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"external FASTQ sort failed (rc={rc}): {stderr.strip()}")


def _iter_sorted_fastq_tsv(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            yield parts[0], parts[1], parts[2]


def _bam_query_key(qname: str | None) -> str | None:
    if not qname:
        return None
    if qname.endswith("/1") or qname.endswith("/2"):
        return qname[:-2]
    return qname


def _restore_original_sequences(
    bam_path: Path,
    fq1: Path,
    fq2: Path,
    out_bam: Path,
    log_path: Path,
) -> None:
    """Restore original read bases/qualities into surjected BAM (streaming).

    Surjection after C2T/G2A alignment can leave converted sequences; QC expects
    original bases. Never loads full FASTQs into RAM: lexicographically name-sort
    the BAM (``samtools sort -N``), externally sort FASTQ records with matching
    ``LC_ALL=C`` collation, then merge-join by read name.

    Important: do **not** use ``samtools sort -n`` (natural/alpha-numeric order);
    that disagrees with Unix ``sort`` / Python ``str`` lexicographic order and
    silently drops restores for Illumina-style numeric read names.
    """
    try:
        import pysam  # type: ignore
    except Exception:
        logger.warning("pysam unavailable; QC BAM keeps surjected sequences")
        if bam_path.resolve() != out_bam.resolve():
            shutil.copy2(bam_path, out_bam)
        return

    work = out_bam.parent / f"{out_bam.stem}.restore_work"
    work.mkdir(parents=True, exist_ok=True)
    name_sorted_bam = work / "name_sorted.bam"
    r1_tsv = work / "r1.sorted.tsv"
    r2_tsv = work / "r2.sorted.tsv"
    sort_tmp = work / "sort_tmp"
    tmp_out = work / "restored.tmp.bam"

    try:
        # -N = lexicographic (raw) name order; matches LC_ALL=C sort + str comparisons.
        _run(
            ["samtools", "sort", "-N", "-o", str(name_sorted_bam), str(bam_path)],
            log_path,
            step="samtools.sort_name_lex",
        )
        _write_sorted_fastq_tsv(fq1, r1_tsv, sort_dir=sort_tmp / "r1")
        _write_sorted_fastq_tsv(fq2, r2_tsv, sort_dir=sort_tmp / "r2")

        r1_iter = _iter_sorted_fastq_tsv(r1_tsv)
        r2_iter = _iter_sorted_fastq_tsv(r2_tsv)
        r1_cur = next(r1_iter, None)
        r2_cur = next(r2_iter, None)

        def _advance(cur, it, key: str):
            """Advance sorted iterator until name >= key; consume match if equal.

            Both streams must use the same lexicographic collation.
            """
            while cur is not None and cur[0] < key:
                cur = next(it, None)
            if cur is not None and cur[0] == key:
                hit = cur
                cur = next(it, None)
                return cur, hit
            return cur, None

        n_restored = 0
        with pysam.AlignmentFile(str(name_sorted_bam), "rb") as inn, pysam.AlignmentFile(
            str(tmp_out), "wb", template=inn
        ) as out:
            for aln in inn:
                key = _bam_query_key(aln.query_name)
                if key is None or not aln.query_sequence:
                    out.write(aln)
                    continue
                if aln.is_read2:
                    r2_cur, hit = _advance(r2_cur, r2_iter, key)
                else:
                    r1_cur, hit = _advance(r1_cur, r1_iter, key)
                if hit is not None and len(hit[1]) == len(aln.query_sequence):
                    aln.query_sequence = hit[1]
                    aln.query_qualities = pysam.qualitystring_to_array(hit[2])
                    n_restored += 1
                out.write(aln)

        shutil.copy2(tmp_out, out_bam)
        _append_log(
            log_path,
            f"Restored original sequences into {out_bam} (n_restored={n_restored}, streaming merge-join)",
        )
    finally:
        # Keep work dir only when METHYL_KEEP_RESTORE_WORK=1 for debugging.
        if os.environ.get("METHYL_KEEP_RESTORE_WORK", "").strip() not in {
            "1",
            "true",
            "yes",
        }:
            shutil.rmtree(work, ignore_errors=True)


def run_methylgrapher_wgbs_align(
    *,
    sample_id: str,
    sample_dir: str | Path,
    input_json: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Optional[str]]:
    """Dual C2T/G2A methylGrapher Align + QC-compatible GRCh38 BAM."""
    payload = dict(input_json or {})
    resolved = dict(payload.get("resolvedConfig") or {})
    sample_path = Path(sample_dir).resolve()
    if not sample_path.is_dir():
        raise RuntimeError(f"sampleDir not found: {sample_path}")

    force = _pick_bool(payload, "forceRealign", False)
    if force and _alignment_complete(sample_path, sample_id):
        logger.info("forceRealign: clearing methylGrapher outputs for %s", sample_id)
        _clear_alignment_outputs(sample_path, sample_id)

    if _alignment_complete(sample_path, sample_id):
        logger.info("Skipping methylGrapher align; outputs present for %s", sample_id)
        return {
            "sampleId": sample_id,
            "bamPath": str(sample_path / f"{sample_id}.bam"),
            "gafPath": str(sample_path / f"{sample_id}.alignment.gaf"),
            "metricsJson": str(sample_path / f"{sample_id}.alignment_metrics.json"),
            "qcMetricsTar": str(sample_path / f"{sample_id}.qc-metrics.tar"),
            "dedupMetricsPath": str(sample_path / f"{sample_id}.deduplicate_metrics.txt"),
            "conversionReportPath": (
                str(sample_path / f"{sample_id}.conversion_report.txt")
                if (sample_path / f"{sample_id}.conversion_report.txt").is_file()
                else None
            ),
        }

    bundle = resolve_wgbs_bundle_from_resolved(resolved)
    log_path = sample_path / f"{sample_id}.methylgrapher_align.log"
    work_dir = sample_path / "methylgrapher_work"
    work_dir.mkdir(parents=True, exist_ok=True)
    fq1, fq2 = _find_paired_fastqs(sample_path, sample_id)

    index_prefix = bundle.index_prefix or str(
        bundle.c2t_gbz.parent / Path(bundle.c2t_gbz.name.split(".wl.")[0])
    )
    # Resolve symlinks (/work -> …/Work) so Docker -v mounts and -index_prefix agree.
    index_prefix = str(Path(index_prefix).expanduser().resolve())
    work_dir = work_dir.resolve()
    fq1 = fq1.resolve()
    fq2 = fq2.resolve()

    # Dry / unit-test hook: skip external tools when METHYL_METHYLGRAPHER_DRY_RUN=1
    dry = os.environ.get("METHYL_METHYLGRAPHER_DRY_RUN", "").strip() in {"1", "true", "yes"}
    gaf_path = sample_path / f"{sample_id}.alignment.gaf"
    bam_path = sample_path / f"{sample_id}.bam"
    metrics_dir = sample_path / f"{sample_id}.qc-metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    metrics_json = sample_path / f"{sample_id}.alignment_metrics.json"
    dedup_path = sample_path / f"{sample_id}.deduplicate_metrics.txt"
    conversion_report = sample_path / f"{sample_id}.conversion_report.txt"

    align_cmd = build_align_command(
        bundle=bundle, work_dir=work_dir, fq1=fq1, fq2=fq2, index_prefix=index_prefix
    )
    provenance = {
        "tool": "methylGrapher",
        "action": "sample.methylgrapher_wgbs_align",
        "sample_id": sample_id,
        "index_prefix": index_prefix,
        "directional": bundle.directional,
        "asset_fingerprints": fingerprint_wgbs_assets(bundle),
        "align_command": align_cmd,
        "align_engine": effective_align_engine(bundle),
        "qc_bam_engine": effective_qc_bam_engine(bundle),
        "engine": bundle.engine,
    }

    if dry:
        gaf_path.write_text("# dry-run GAF\n", encoding="utf-8")
        # Minimal BAM placeholder is not valid for samtools; write empty file + metrics.
        bam_path.write_bytes(b"")
        conversion_report.write_text("dry_run=1\n", encoding="utf-8")
        _write_dedup_metrics(dedup_path, sample_id)
    else:
        image = _resolve_image(bundle)
        # Mount sample + genome roots covering C2T/G2A/linear assets.
        # Include resolved index_prefix parent so -index_prefix matches a mounted path.
        mount_roots = {
            sample_path.resolve(),
            work_dir,
            bundle.c2t_gbz.parent.resolve(),
            bundle.g2a_gbz.parent.resolve(),
            bundle.linear_ref_fasta.parent.resolve(),
            fq1.parent.resolve(),
            Path(index_prefix).parent,
        }
        # Align + QC MojoGiraffe both need segment packs (even when science GAF is reused).
        add_mojo_segment_cache_mounts(mount_roots, bundle)
        if gaf_path.is_file() and gaf_path.stat().st_size > 0:
            # Resume: alignment is the longest step and the GAF only lands here
            # after methylGrapher succeeded, so never re-map it.
            logger.info("Reusing existing methylGrapher GAF for %s", sample_id)
            _append_log(log_path, f"REUSE: existing GAF {gaf_path}")
        else:
            align_device = resolve_giraffe_device(bundle)
            _ensure_align_workdir_writable(work_dir)
            docker_cmd = [
                _docker_bin(),
                "run",
                "--rm",
                "--user",
                _align_docker_user(),
                *align_docker_gpu_flags(align_device),
            ]
            for env_pair in materialize_align_docker_env(bundle):
                docker_cmd.extend(["-e", env_pair])
            for root in sorted(mount_roots, key=str):
                docker_cmd.extend(["-v", f"{root}:{root}"])
            add_mojo_src_overlay_mounts(docker_cmd)
            docker_cmd.extend([image, *_with_umask_002(align_cmd)])
            # One GH200 ≈ full HBM: flock + admit fraction + always release after.
            with _gpu_align_lock(align_device):
                _run(docker_cmd, log_path, step="methylGrapher.Align")
            share_work_tree(work_dir)

            # methylGrapher merges to work_dir/alignment.gaf; ignore empty shard files
            # left behind by a failed prior attempt (alignment.0.gaf …).
            preferred = work_dir / "alignment.gaf"
            if preferred.is_file() and preferred.stat().st_size > 0:
                shutil.copy2(preferred, gaf_path)
            else:
                candidates = sorted(
                    (
                        p
                        for p in work_dir.glob("*.gaf")
                        if p.is_file() and p.stat().st_size > 0
                    ),
                    key=lambda p: p.stat().st_size,
                    reverse=True,
                )
                if not candidates:
                    raise RuntimeError(
                        f"methylGrapher Align did not produce a GAF under {work_dir}"
                    )
                shutil.copy2(candidates[0], gaf_path)
            share_work_path(gaf_path)

        # Mojo emits GBZ chopped-node ids; MethylCall needs GFA named-coordinates.
        work_gaf = work_dir / "alignment.gaf"
        if not work_gaf.is_file() and gaf_path.is_file():
            shutil.copy2(gaf_path, work_gaf)
        if work_gaf.is_file():
            ensure_gaf_named_coordinates(work_gaf, work_dir, bundle, log_path)
            if gaf_path.resolve() != work_gaf.resolve():
                shutil.copy2(work_gaf, gaf_path)
        elif gaf_path.is_file():
            ensure_gaf_named_coordinates(gaf_path, work_dir, bundle, log_path)

        if not bundle.directional:
            logger.warning(
                "non-directional library %s: QC BAM covers the R1-C2T/R2-G2A pass "
                "against the C2T graph only; methylation calls still use every "
                "methylGrapher pass",
                sample_id,
            )

        qc_bam = work_dir / f"{sample_id}.giraffe.bam"
        restored = work_dir / f"{sample_id}.restored.bam"

        def _bam_reusable(path: Path) -> bool:
            """Reject truncated/corrupt BAM leftovers from killed Aligns."""
            if not path.is_file() or path.stat().st_size <= 0:
                return False
            try:
                proc = subprocess.run(
                    ["samtools", "quickcheck", "-v", str(path)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                return proc.returncode == 0
            except FileNotFoundError:
                # No samtools on PATH: fall back to non-empty only.
                return path.stat().st_size > 1_000_000

        if restored.is_file() and restored.stat().st_size > 0 and _bam_reusable(restored):
            # Resume after a failed fixmate/markdup: keep the name-ordered BAM.
            logger.info("Reusing restored QC BAM for %s", sample_id)
            _append_log(log_path, f"REUSE: existing restored BAM {restored}")
        else:
            if restored.is_file() and not _bam_reusable(restored):
                _append_log(log_path, f"DISCARD: truncated restored BAM {restored}")
                restored.unlink(missing_ok=True)
            qc_engine = effective_qc_bam_engine(bundle)
            qc_fallback = effective_qc_bam_fallback(bundle)
            provenance["qc_bam_engine"] = qc_engine
            provenance["qc_bam_fallback"] = qc_fallback
            can_reuse_giraffe = _bam_reusable(qc_bam) and (
                qc_engine != "mojo" or giraffe_bam_reusable_for_mojo_qc(qc_bam)
            )
            if qc_bam.is_file() and can_reuse_giraffe:
                logger.info("Reusing giraffe QC BAM for %s", sample_id)
                _append_log(log_path, f"REUSE: existing giraffe BAM {qc_bam}")
            else:
                if qc_bam.is_file() and not can_reuse_giraffe:
                    why = (
                        "truncated/corrupt"
                        if not _bam_reusable(qc_bam)
                        else "missing Mojo-SAM provenance (packer-era or incomplete)"
                    )
                    _append_log(log_path, f"DISCARD: giraffe BAM ({why}) {qc_bam}")
                    qc_bam.unlink(missing_ok=True)
                    mojo_qc_sam_provenance_path(qc_bam).unlink(missing_ok=True)
                    # Stale GAF packer intermediate — never resume that path.
                    (work_dir / f"{sample_id}.qc_mojo.gaf").unlink(missing_ok=True)
                c2t_r1 = work_dir / f"{sample_id}.C2T.R1.fastq"
                g2a_r2 = work_dir / f"{sample_id}.G2A.R2.fastq"
                _write_bs_converted_fastq(
                    fq1, c2t_r1, base_from="C", base_to="T", log_path=log_path
                )
                _write_bs_converted_fastq(
                    fq2, g2a_r2, base_from="G", base_to="A", log_path=log_path
                )
                used_vg_fallback = False
                if qc_engine == "mojo":
                    qc_sam = work_dir / f"{sample_id}.qc_mojo.sam"
                    index_prefix_qc = resolve_index_prefix(bundle)
                    offsets_dir = resolve_grch38_offsets_dir(bundle, index_prefix_qc)
                    mojo_qc_cmd = build_qc_bam_mojo_sam_command(
                        bundle=bundle,
                        fq1_c2t=c2t_r1,
                        fq2_g2a=g2a_r2,
                        out_sam=qc_sam,
                        segment_offsets=offsets_dir,
                    )
                    docker_mojo_qc = [
                        _docker_bin(),
                        "run",
                        "--rm",
                        "--user",
                        _align_docker_user(),
                        *align_docker_gpu_flags(resolve_giraffe_device(bundle)),
                    ]
                    for env_pair in materialize_align_docker_env(bundle):
                        docker_mojo_qc.extend(["-e", env_pair])
                    docker_mojo_qc.extend(
                        [
                            "-e",
                            "MOJO_ALIGN_EMIT=sam",
                            "-e",
                            f"MOJO_ALIGN_SEGMENT_OFFSETS={offsets_dir}",
                        ]
                    )
                    qc_mounts = set(mount_roots)
                    qc_mounts.add(bundle.ref_paths.parent.resolve())
                    try:
                        qc_mounts.add(offsets_dir.resolve())
                    except OSError:
                        qc_mounts.add(offsets_dir)
                    add_mojo_segment_cache_mounts(qc_mounts, bundle)
                    for root in sorted(qc_mounts, key=str):
                        docker_mojo_qc.extend(["-v", f"{root}:{root}"])
                    add_mojo_src_overlay_mounts(docker_mojo_qc)
                    docker_mojo_qc.extend([image, *_with_umask_002(mojo_qc_cmd)])
                    try:
                        _append_log(
                            log_path,
                            f"Mojo QC SAM emit (native); offsets={offsets_dir}",
                        )
                        # QC MojoGiraffe needs the same HBM residency as Align; run
                        # under the flock even when science GAF was reused (that
                        # path skips the Align lock). Always release on exit.
                        with _gpu_align_lock(resolve_giraffe_device(bundle)):
                            _run(docker_mojo_qc, log_path, step="mojo.qc_sam")
                        samtools_sam_to_bam(qc_sam, qc_bam, log_path)
                        write_mojo_qc_sam_provenance(qc_bam, offsets_dir=offsets_dir)
                        _append_log(
                            log_path,
                            f"Mojo QC SAM→BAM {qc_sam} → {qc_bam} "
                            f"({qc_bam.stat().st_size} bytes)",
                        )
                        qc_sam.unlink(missing_ok=True)
                    except Exception as exc:
                        if qc_fallback == "error":
                            _append_log(
                                log_path,
                                f"Mojo QC BAM failed (qc_bam_fallback=error, no vg): {exc}",
                            )
                            raise
                        logger.warning(
                            "Mojo QC BAM failed for %s (%s); falling back to vg giraffe BAM",
                            sample_id,
                            exc,
                        )
                        _append_log(log_path, f"Mojo QC BAM fallback to vg: {exc}")
                        used_vg_fallback = True
                if qc_engine == "vg" or used_vg_fallback:
                    qc_bam_cmd = build_qc_bam_command(
                        bundle=bundle,
                        fq1_c2t=c2t_r1,
                        fq2_g2a=g2a_r2,
                        threads=bundle.threads,
                    )
                    docker_qc_bam = [
                        _docker_bin(),
                        "run",
                        "--rm",
                        "--user",
                        _align_docker_user(),
                    ]
                    for root in sorted(
                        mount_roots | {bundle.ref_paths.parent.resolve()}, key=str
                    ):
                        docker_qc_bam.extend(["-v", f"{root}:{root}"])
                    docker_qc_bam.extend([image, *_with_umask_002(qc_bam_cmd)])
                    _run(
                        docker_qc_bam,
                        log_path,
                        step="vg.giraffe_qc_bam",
                        stdout_path=qc_bam,
                    )
                for converted in (c2t_r1, g2a_r2):
                    converted.unlink(missing_ok=True)

            _restore_original_sequences(qc_bam, fq1, fq2, restored, log_path)

        # Restored BAM is name-ordered (merge-join walks a -N sorted stream).
        # fixmate must see that order so markdup gets the MC/ms tags it needs.
        fixed = work_dir / f"{sample_id}.fixmate.bam"
        _run(
            [
                "samtools",
                "fixmate",
                "-m",
                str(restored if restored.is_file() else qc_bam),
                str(fixed),
            ],
            log_path,
            step="samtools.fixmate",
        )
        sorted_bam = work_dir / f"{sample_id}.sorted.bam"
        _run(
            ["samtools", "sort", "-o", str(sorted_bam), str(fixed)],
            log_path,
            step="samtools.sort",
        )
        marked = work_dir / f"{sample_id}.markdup.bam"
        markdup_stderr = _run_capturing_stderr(
            ["samtools", "markdup", "-s", str(sorted_bam), str(marked)],
            log_path,
            step="samtools.markdup",
        )
        shutil.copy2(marked, bam_path)
        share_work_path(bam_path)
        ensure_qc_bam_read_group(bam_path, sample_id, log_path)
        _run(["samtools", "index", str(bam_path)], log_path, step="samtools.index")
        share_work_path(Path(str(bam_path) + ".bai"))
        _write_dedup_metrics(
            dedup_path, sample_id, **_dedup_metrics_from_markdup_stderr(markdup_stderr)
        )
        share_work_path(dedup_path)
        # Capture conversion / mapping reports if present.
        for report in work_dir.glob("*report*"):
            if report.is_file():
                shutil.copy2(report, conversion_report)
                break
        if not conversion_report.is_file():
            conversion_report.write_text(
                json.dumps({"status": "ok", "gaf": str(gaf_path)}, indent=2) + "\n",
                encoding="utf-8",
            )

    provenance["gaf"] = str(gaf_path)
    provenance["bam"] = str(bam_path)

    picard_ok = False
    if not dry:
        picard_ok = _maybe_collect_picard_metrics(
            sample_id=sample_id,
            sample_path=sample_path,
            bam_path=bam_path,
            metrics_dir=metrics_dir,
            linear_ref_fasta=bundle.linear_ref_fasta,
            log_path=log_path,
            input_json=payload,
        )
    provenance["collectmultiplemetrics"] = bool(picard_ok)
    if picard_ok:
        provenance["picard_qc_note"] = (
            "Picard CollectMultipleMetrics on restored QC BAM; BS chemistry may "
            "skew artifact/GC metrics — operational screening only."
        )

    metrics_json.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    (metrics_dir / "alignment_metrics.json").write_text(
        metrics_json.read_text(encoding="utf-8"), encoding="utf-8"
    )
    qc_tar = _package_qc_tar(sample_path, sample_id, metrics_dir)

    return {
        "sampleId": sample_id,
        "bamPath": str(bam_path),
        "gafPath": str(gaf_path),
        "metricsJson": str(metrics_json),
        "qcMetricsTar": str(qc_tar),
        "dedupMetricsPath": str(dedup_path),
        "conversionReportPath": str(conversion_report) if conversion_report.is_file() else None,
    }


def _is_grch38_sample(name: str) -> bool:
    """True when a PanSN sample name denotes the GRCh38 linear reference."""
    head = name.strip().split("#", 1)[0].upper()
    return head == "HG38" or head.startswith("GRCH38")


def _normalize_grch38_chrom(name: str) -> Optional[str]:
    """Map path/sequence names like ``GRCh38#0#chr1`` / ``chr1`` → ``1``."""
    raw = str(name).strip()
    if not raw:
        return None
    # Prefer GRCh38 haplotype paths; ignore CHM13 / sample haplotypes.
    if "#" in raw:
        if not _is_grch38_sample(raw):
            return None
        raw = raw.split("#")[-1]
    if raw.lower().startswith("chr"):
        raw = raw[3:]
    if raw.upper() == "M":
        return "MT"
    if raw in {"X", "Y", "MT"} or raw.isdigit():
        return raw
    return None


def build_grch38_segment_offsets_from_gfa(
    gfa_path: Path,
) -> Dict[str, Tuple[str, int, int, str]]:
    """Map segment ID → (chrom, start_0based, length, orient) for GRCh38 paths.

    Parses GFA ``W`` / ``P`` lines. When a segment appears on multiple GRCh38
    paths, the first occurrence wins (stable for primary chromosomes).
    ``orient`` is ``'>'`` or ``'<'`` for the walk step used.
    """
    import re

    seg_re = re.compile(r"([><])([^><]+)")
    offsets: Dict[str, Tuple[str, int, int, str]] = {}
    # Cache segment lengths from S-lines so W-path walks can advance.
    seg_len: Dict[str, int] = {}

    with gfa_path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line or line[0] not in "SWP":
                continue
            if line[0] == "S":
                parts = line.rstrip("\r\n").split("\t")
                if len(parts) < 3:
                    continue
                seg_id, seq = parts[1], parts[2]
                if seq == "*":
                    # length may be in LN:i: tag
                    length = 0
                    for tag in parts[3:]:
                        if tag.startswith("LN:i:"):
                            length = int(tag[5:])
                            break
                    seg_len[seg_id] = length
                else:
                    seg_len[seg_id] = len(seq)
                continue

            parts = line.rstrip("\r\n").split("\t")
            path_seq = ""
            chrom: Optional[str] = None
            path_start = 0
            if line[0] == "W" and len(parts) >= 7:
                # W SampleId HapIndex SeqId SeqStart SeqEnd Walk
                # SeqId is usually a bare chromosome name, so the sample field is
                # the only place the haplotype is identified.
                if not _is_grch38_sample(parts[1]):
                    continue
                chrom = _normalize_grch38_chrom(parts[3])
                if chrom is None:
                    continue
                try:
                    path_start = int(parts[4])
                except ValueError:
                    path_start = 0
                path_seq = parts[6]
            elif line[0] == "P" and len(parts) >= 3:
                chrom = _normalize_grch38_chrom(parts[1])
                if chrom is None:
                    continue
                # P pathName seg+[,seg+]…
                path_seq = "".join(
                    (">" if tok.endswith("+") else "<") + tok[:-1]
                    for tok in parts[2].split(",")
                    if tok
                )
            else:
                continue

            cursor = path_start
            for m in seg_re.finditer(path_seq):
                orient, seg_id = m.group(1), m.group(2)
                length = int(seg_len.get(seg_id, 0))
                if seg_id not in offsets:
                    offsets[seg_id] = (chrom, cursor, length, orient)
                cursor += length
    return offsets


def _genomic_pos_1based(
    *,
    base_0: int,
    seg_len: int,
    orient: str,
    pos_0: int,
) -> int:
    """Convert 0-based segment offset to 1-based linear genomic coordinate."""
    if orient == "<":
        if seg_len <= 0:
            g0 = base_0 + int(pos_0)
        else:
            g0 = base_0 + (int(seg_len) - 1 - int(pos_0))
    else:
        g0 = base_0 + int(pos_0)
    return g0 + 1


def project_graph_cpg_to_linear_tsv(
    *,
    graph_cpg_tsv: Path,
    cpg_registry_tsv: Path,
    segment_offsets: Mapping[str, Tuple],
    out_tsv: Path,
) -> int:
    """Join MergeCpG ``graph.cpg.tsv`` to ``cpg.tsv`` and emit chrom/pos/mC/uC.

    Emits **1-based** GRCh38 coordinates (MethylExtractor / linear H5 convention).
    ``segment_offsets`` values are ``(chrom, start_0based[, length, orient])``.
    Sites whose cytosines are not on a GRCh38 reference path are skipped.
    Duplicate linear positions are aggregated (sum mC/uC).
    Returns the number of projected rows written.
    """
    # cpg.tsv: C0\tseg1\tpos1\tseg2\tpos2\ttag...
    registry: Dict[str, Tuple[str, int, str, int]] = {}
    with cpg_registry_tsv.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            parts = line.rstrip("\r\n").split("\t")
            if len(parts) < 5:
                continue
            # First field is like C0 / E12 (letter glued to index).
            cpg_id = parts[0]
            try:
                registry[cpg_id] = (parts[1], int(parts[2]), parts[3], int(parts[4]))
            except ValueError:
                continue

    # Aggregate in case multiple graph CpGs project to the same linear locus.
    agg: Dict[Tuple[str, int], List[int]] = {}
    with graph_cpg_tsv.open("r", encoding="utf-8", errors="replace") as fin:
        for line in fin:
            if not line.strip():
                continue
            parts = line.rstrip("\r\n").split("\t")
            if len(parts) < 3:
                continue
            cpg_id, met_s, cov_s = parts[0], parts[1], parts[2]
            if cpg_id not in registry:
                continue
            try:
                met = int(float(met_s))
                cov = int(float(cov_s))
            except ValueError:
                continue
            if cov <= 0:
                continue
            seg1, pos1, seg2, pos2 = registry[cpg_id]
            # Prefer the first cytosine on a GRCh38 path.
            chrom_pos: Optional[Tuple[str, int]] = None
            for seg, pos in ((seg1, pos1), (seg2, pos2)):
                if seg not in segment_offsets:
                    continue
                entry = segment_offsets[seg]
                chrom = str(entry[0])
                base = int(entry[1])
                if len(entry) >= 4:
                    slen = int(entry[2])
                    orient = str(entry[3]) if entry[3] in (">", "<") else ">"
                elif len(entry) == 3 and str(entry[2]) in (">", "<"):
                    slen = 0
                    orient = str(entry[2])
                else:
                    slen = int(entry[2]) if len(entry) >= 3 else 0
                    orient = ">"
                chrom_pos = (chrom, _genomic_pos_1based(base_0=base, seg_len=slen, orient=orient, pos_0=int(pos)))
                break
            if chrom_pos is None:
                continue
            chrom, pos = chrom_pos
            uc = max(0, cov - met)
            key = (chrom, pos)
            cur = agg.get(key)
            if cur is None:
                agg[key] = [met, uc]
            else:
                cur[0] += met
                cur[1] += uc

    with out_tsv.open("w", encoding="utf-8") as fout:
        fout.write("chrom\tpos\tmC\tuC\ttnc\n")
        for (chrom, pos), (met, uc) in sorted(agg.items(), key=lambda kv: (kv[0][0], kv[0][1])):
            fout.write(f"{chrom}\t{pos}\t{met}\t{uc}\t1\n")
    return len(agg)


def _parse_linear_methyl_tsv(path: Path) -> Dict[str, Dict[str, Any]]:
    """Parse a TSV with chrom,pos,mC,uC[,tnc] into per-chromosome arrays."""
    by_chrom: Dict[str, Dict[str, list]] = {}
    with path.open("r", encoding="utf-8") as fh:
        header = fh.readline().strip().split("\t")
        cols = {c.lower(): i for i, c in enumerate(header)}
        # Support methylGrapher MergeCpG-style or operator-projected linear TSV.
        for line in fh:
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if "chrom" in cols and "pos" in cols:
                chrom = parts[cols["chrom"]].lstrip("chr")
                pos = int(float(parts[cols["pos"]]))
                mc = int(float(parts[cols.get("mc", cols.get("methylated", 2))]))
                uc = int(float(parts[cols.get("uc", cols.get("unmethylated", 3))]))
                tnc = int(float(parts[cols["tnc"]])) if "tnc" in cols else 1
            elif len(parts) >= 4:
                chrom = parts[0].lstrip("chr")
                pos = int(float(parts[1]))
                mc = int(float(parts[2]))
                uc = int(float(parts[3]))
                tnc = int(float(parts[4])) if len(parts) > 4 else 1
            else:
                continue
            bucket = by_chrom.setdefault(chrom, {"pos": [], "mC": [], "uC": [], "tnc": []})
            bucket["pos"].append(pos)
            bucket["mC"].append(mc)
            bucket["uC"].append(uc)
            bucket["tnc"].append(tnc)
    return by_chrom


def _write_marginal_h5(
    sample_dir: Path,
    by_chrom: Mapping[str, Mapping[str, Any]],
    contexts: Sequence[str],
) -> List[str]:
    """Write MethylExtractor-compatible structured ``methylation_data`` datasets.

    Uses gzip (not Zstd plugin) so QC tools can read without ``HDF5_PLUGIN_PATH``.
    Widens mC/uC to uint32 when counts exceed uint16.
    """
    import h5py

    written: List[str] = []
    for chrom, arrays in by_chrom.items():
        for ctx in contexts:
            path = sample_dir / f"{chrom}-{ctx}.h5"
            pos = np.asarray(arrays["pos"], dtype=np.uint32)
            mc = np.asarray(arrays["mC"], dtype=np.int64)
            uc = np.asarray(arrays["uC"], dtype=np.int64)
            tnc = np.asarray(arrays.get("tnc", np.ones(pos.size, dtype=np.uint8)), dtype=np.uint8)
            if mc.size and (int(mc.max()) > 65535 or int(uc.max()) > 65535):
                dt = np.dtype([("pos", "<u4"), ("mC", "<u4"), ("uC", "<u4"), ("tnc", "u1")])
                data = np.empty(pos.size, dtype=dt)
                data["pos"] = pos
                data["mC"] = mc.astype(np.uint32)
                data["uC"] = uc.astype(np.uint32)
                data["tnc"] = tnc
            else:
                dt = np.dtype([("pos", "<u4"), ("mC", "<u2"), ("uC", "<u2"), ("tnc", "u1")])
                data = np.empty(pos.size, dtype=dt)
                data["pos"] = pos
                data["mC"] = mc.astype(np.uint16, copy=False)
                data["uC"] = uc.astype(np.uint16, copy=False)
                data["tnc"] = tnc
            with h5py.File(path, "w") as handle:
                handle.create_dataset(
                    "methylation_data", data=data, compression="gzip", compression_opts=4
                )
                handle.attrs["context"] = str(ctx)
                handle.attrs["chromosome"] = str(chrom)
                handle.attrs["source"] = "methylGrapher"
            written.append(str(path))
    return written


def _context_metrics_from_calls(arrays: Mapping[str, Any]) -> Dict[str, Any]:
    """Per-chromosome/context metrics for extraction-QC manifests."""
    mc = np.asarray(arrays.get("mC", []), dtype=np.float64)
    uc = np.asarray(arrays.get("uC", []), dtype=np.float64)
    if mc.size == 0:
        return {
            "num_positions": 0,
            "methylation_level": None,
            "mean_coverage": 0.0,
            "total_coverage": 0.0,
        }
    cov = mc + uc
    total_cov = float(cov.sum())
    mean_cov = float(cov.mean())
    meth = float(mc.sum() / total_cov) if total_cov > 0 else None
    return {
        "num_positions": int(mc.size),
        "methylation_level": meth,
        "mean_coverage": mean_cov,
        "total_coverage": total_cov,
    }


def build_canonical_extraction_manifest(
    *,
    sample_id: str,
    project: str | Path,
    by_chrom: Mapping[str, Mapping[str, Any]],
    contexts: Sequence[str],
    h5_files: Sequence[str],
    pattern_files: Sequence[str],
    gaf_path: Optional[Path],
    graph_assets: Mapping[str, Any],
) -> Dict[str, Any]:
    """Build an extraction manifest compatible with ``methyl_extraction_qc``.

    Emits the canonical ``metadata`` / ``summary`` / ``per_chromosome`` blocks
    expected by extraction QC guardrails, plus methylGrapher provenance
    (graph assets, coordinate system, H5/pattern paths). Read-filtering stats
    are omitted when unavailable so the discard-fraction guardrail reports a
    skipped check rather than inventing values.
    """
    contexts_list = [str(c) for c in contexts] or ["CG"]
    per_chromosome: Dict[str, Dict[str, Any]] = {}
    weighted_cov_num = 0.0
    weighted_cov_den = 0.0
    weighted_meth_num = 0.0
    weighted_meth_den = 0.0
    ctx_meth_totals: Dict[str, List[float]] = {c: [0.0, 0.0] for c in contexts_list}

    for chrom, arrays in by_chrom.items():
        chrom_key = str(chrom).lstrip("chr")
        chrom_entry: Dict[str, Any] = {}
        metrics = _context_metrics_from_calls(arrays)
        for ctx in contexts_list:
            # Linear MergeCpG / projected TSV is currently CpG-shaped; replicate
            # the same site metrics under each requested context for QC completeness.
            chrom_entry[ctx] = {
                "num_positions": metrics["num_positions"],
                "methylation_level": metrics["methylation_level"],
                "mean_coverage": metrics["mean_coverage"],
            }
            if ctx == "CG" and metrics["num_positions"]:
                weighted_cov_num += float(metrics["mean_coverage"]) * float(metrics["num_positions"])
                weighted_cov_den += float(metrics["num_positions"])
                if metrics["methylation_level"] is not None:
                    weighted_meth_num += float(metrics["methylation_level"]) * float(
                        metrics["total_coverage"]
                    )
                    weighted_meth_den += float(metrics["total_coverage"])
            mc = np.asarray(arrays.get("mC", []), dtype=np.float64)
            uc = np.asarray(arrays.get("uC", []), dtype=np.float64)
            ctx_meth_totals[ctx][0] += float(mc.sum()) if mc.size else 0.0
            ctx_meth_totals[ctx][1] += float((mc + uc).sum()) if mc.size else 0.0
        per_chromosome[chrom_key] = chrom_entry

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
        "n_pattern_files": len(pattern_files),
    }
    for ctx in contexts_list:
        if ctx == "CG":
            continue
        meth_num, meth_den = ctx_meth_totals[ctx]
        key = f"{ctx.lower()}_methylation_level"
        summary[key] = (meth_num / meth_den) if meth_den > 0 else None

    return {
        "metadata": {
            "schema_name": "methylextractor.extraction_manifest",
            "schema_version": "1.0.0",
            "sample_id": sample_id,
            "contexts_extracted": contexts_list,
            "extractor": "methylGrapher",
            "action": "sample.methylgrapher_wgbs_extract",
        },
        "summary": summary,
        "per_chromosome": per_chromosome,
        # Provenance retained for operators / archive; not required by guardrails.
        "sample_id": sample_id,
        "project": str(project),
        "tool": "methylGrapher",
        "action": "sample.methylgrapher_wgbs_extract",
        "contexts": contexts_list,
        "h5_files": list(h5_files),
        "pattern_files": list(pattern_files),
        "gaf": str(gaf_path) if gaf_path is not None and gaf_path.is_file() else None,
        "graph_assets": dict(graph_assets),
        "coordinate_system": {
            "graph": "methylGrapher segment/offset",
            "linear": "GRCh38 via projected linear_cpg_tsv / MergeCpG",
        },
    }


def _write_empty_patterns(
    sample_dir: Path,
    chromosomes: Sequence[str],
    contexts: Sequence[str],
    *,
    tile_size: int,
) -> List[str]:
    from methyl_utils.core.read_level_io import ReadLevelPatterns, write_read_level_patterns

    written: List[str] = []
    for chrom in chromosomes:
        for ctx in contexts:
            path = sample_dir / f"{chrom}-{ctx}.patterns.h5"
            data = ReadLevelPatterns(
                context=str(ctx),
                tile_size=int(tile_size),
                tile_start_pos=np.asarray([], dtype=np.uint32),
                tile_cpg_positions=np.zeros((0, tile_size), dtype=np.uint32),
                tile_n_reads=np.asarray([], dtype=np.uint32),
                pattern_tile_id=np.asarray([], dtype=np.uint32),
                pattern_id=np.asarray([], dtype=np.uint16),
                pattern_count=np.asarray([], dtype=np.uint32),
            )
            write_read_level_patterns(path, data)
            written.append(str(path))
    return written


def _maybe_run_conversion_rate(
    *,
    bundle: MethylGrapherWgbsBundle,
    sample_path: Path,
    work_dir: Path,
    index_prefix: str,
    image: str,
    mount_roots: set,
    log_path: Path,
) -> bool:
    """Invoke Mojo ConversionRate when lambda spike-in + graph.methyl exist.

    Writes ``{sampleDir}/{conversion_rate_sidecar}`` for methyl_qc. Returns True
    when a sidecar was written; False on graceful skip (no spike-in / no methyl).
    """
    graph_methyl = work_dir / "graph.methyl"
    report = Path(f"{index_prefix}.prepare.genome.report.txt")
    if not graph_methyl.is_file() or graph_methyl.stat().st_size == 0:
        _append_log(log_path, "ConversionRate skip: graph.methyl missing")
        return False
    if not report.is_file():
        _append_log(
            log_path,
            "ConversionRate skip: PrepareGenome report missing (no lambda spike-in)",
        )
        return False
    report_txt = report.read_text(encoding="utf-8", errors="replace")
    if "lambda phage" not in report_txt.lower():
        _append_log(log_path, "ConversionRate skip: no lambda phage segment in report")
        return False
    conv_cmd = [
        os.environ.get(METHYLGRAPHER_BIN_ENV, "").strip() or "methylGrapher",
        "ConversionRate",
        "-work_dir",
        str(work_dir),
        "-index_prefix",
        index_prefix,
    ]
    docker_conv = [
        _docker_bin(),
        "run",
        "--rm",
        "--user",
        _align_docker_user(),
    ]
    for root in sorted(mount_roots | {report.parent.resolve()}, key=str):
        docker_conv.extend(["-v", f"{root}:{root}"])
    docker_conv.extend([image, *_with_umask_002(conv_cmd)])
    try:
        proc = subprocess.run(
            docker_conv, capture_output=True, text=True, check=False
        )
    except Exception as exc:
        _append_log(log_path, f"ConversionRate failed: {exc}")
        return False
    _append_log(log_path, f"[{'ConversionRate'}] stdout:\n{proc.stdout or ''}")
    if proc.stderr:
        _append_log(log_path, f"[ConversionRate] stderr:\n{proc.stderr}")
    if proc.returncode != 0:
        _append_log(log_path, f"ConversionRate rc={proc.returncode}; skip sidecar")
        return False
    # Parse "context\trate" lines or JSON-ish output for a CG rate.
    rate_pct: Optional[float] = None
    for line in (proc.stdout or "").splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[0].upper() in {"CG", "CHH", "CHG", "ALL"}:
            try:
                val = float(parts[1])
            except ValueError:
                continue
            rate_pct = val * 100.0 if val <= 1.0 else val
            if parts[0].upper() == "CG":
                break
    if rate_pct is None:
        # Fallback: look for conversion_rate in work_dir reports
        for cand in work_dir.glob("*conversion*"):
            if cand.is_file():
                txt = cand.read_text(encoding="utf-8", errors="replace")
                for line in txt.splitlines():
                    if "conversion" in line.lower():
                        for tok in line.replace("=", " ").split():
                            try:
                                val = float(tok)
                                rate_pct = val * 100.0 if val <= 1.0 else val
                                break
                            except ValueError:
                                continue
            if rate_pct is not None:
                break
    if rate_pct is None:
        _append_log(log_path, "ConversionRate produced no parseable rate; skip sidecar")
        return False
    sidecar = sample_path / bundle.conversion_rate_sidecar
    sidecar.write_text(
        json.dumps(
            {
                "conversion_rate_pct": float(rate_pct),
                "source": "methylGrapher.ConversionRate",
                "engine": bundle.engine,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _append_log(log_path, f"Wrote bisulfite sidecar {sidecar} rate={rate_pct}")
    return True


def _build_patterns_from_gaf(
    sample_dir: Path,
    gaf_path: Path,
    by_chrom: Mapping[str, Mapping[str, Any]],
    contexts: Sequence[str],
    *,
    tile_size: int,
    segment_offsets: Mapping[str, Tuple[str, int, int, str]] | None = None,
) -> List[str]:
    """Build true per-read tile patterns from science GAF + linear CpG coordinates.

    For each GAF alignment that anchors on a GRCh38 segment, CpGs covered by the
    query interval contribute a methylation bit from ``os:Z`` (C=meth, T=unmeth
    on OT/C2T) or from converted query sequence tags. Falls back to empty list
    when the GAF cannot yield any fully-covered tiles (caller may use surrogate).
    """
    import re
    from collections import defaultdict

    from methyl_utils.core.read_level_io import ReadLevelPatterns, write_read_level_patterns

    if not gaf_path.is_file() or gaf_path.stat().st_size == 0:
        return []
    # chrom → sorted unique CpG positions
    cpg_pos: Dict[str, np.ndarray] = {
        chrom: np.asarray(arrays["pos"], dtype=np.uint32)
        for chrom, arrays in by_chrom.items()
    }
    # tile aggregation: (chrom, tile_idx) → Counter of pattern_id
    from collections import Counter

    tile_meta: Dict[Tuple[str, int], Tuple[int, np.ndarray]] = {}
    tile_patterns: Dict[Tuple[str, int], Counter] = defaultdict(Counter)
    tile_n_reads: Dict[Tuple[str, int], int] = defaultdict(int)

    def _tile_for(chrom: str, pos: int) -> Optional[Tuple[int, np.ndarray]]:
        arr = cpg_pos.get(chrom)
        if arr is None or arr.size == 0:
            return None
        # index of first CpG >= pos via searchsorted; tile by index // tile_size
        idx = int(np.searchsorted(arr, pos))
        if idx >= arr.size or int(arr[idx]) != int(pos):
            # exact match only
            found = np.where(arr == pos)[0]
            if found.size == 0:
                return None
            idx = int(found[0])
        t = idx // tile_size
        sl = slice(t * tile_size, min((t + 1) * tile_size, arr.size))
        positions = arr[sl]
        if positions.size < tile_size:
            return None
        return t, positions

    seg_re = re.compile(r"([><])([^><]+)")
    offsets = dict(segment_offsets or {})

    with gaf_path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 12:
                continue
            path = parts[5]
            tags = {}
            for tok in parts[12:]:
                if len(tok) > 5 and tok[2:5] == ":Z:":
                    tags[tok[:2]] = tok[5:]
            seq = str(tags.get("os") or tags.get("rq") or "")
            if not seq:
                continue
            try:
                pstart = int(parts[7])
            except ValueError:
                pstart = 0
            # Collect GRCh38 CpG positions covered by this alignment (approx: each
            # GRCh38 segment fully covered contributes its genomic span CpGs).
            covered: List[Tuple[str, int, str]] = []  # chrom, pos, base_at_query
            path_cursor = 0
            q_cursor = 0
            for m in seg_re.finditer(path):
                _orient, seg_id = m.group(1), m.group(2)
                info = offsets.get(seg_id)
                seg_len = int(info[2]) if info else 0
                if info is not None:
                    chrom, seg_genomic0, seg_len, _o = info
                    # Query bases advancing with path (gapless approximation).
                    for off in range(seg_len):
                        gpos1 = seg_genomic0 + off + 1  # 1-based CpG coords in TSV
                        if q_cursor < len(seq):
                            base = seq[q_cursor]
                        else:
                            base = "N"
                        covered.append((chrom, gpos1, base))
                        q_cursor += 1
                else:
                    q_cursor += seg_len
                path_cursor += seg_len
                _ = pstart  # reserved for finer path-start alignment
            # Group covered CpGs by tile; require all k CpGs observed.
            by_tile_bases: Dict[Tuple[str, int], Dict[int, str]] = defaultdict(dict)
            for chrom, gpos1, base in covered:
                tile = _tile_for(chrom, gpos1)
                if tile is None:
                    continue
                t_idx, positions = tile
                key = (chrom, t_idx)
                tile_meta[key] = (int(positions[0]), positions)
                by_tile_bases[key][int(gpos1)] = base
            for key, bases in by_tile_bases.items():
                _chrom, t_idx = key
                _start, positions = tile_meta[key]
                if any(int(p) not in bases for p in positions):
                    continue
                bits = 0
                for i, p in enumerate(positions):
                    b = bases[int(p)].upper()
                    # C (or G on opposite) = methylated after restore; T/A = unmeth
                    if b in {"C", "G"}:
                        bits |= 1 << (tile_size - 1 - i)
                tile_patterns[key][bits] += 1
                tile_n_reads[key] += 1

    if not tile_meta:
        return []

    written: List[str] = []
    chroms = sorted({c for c, _t in tile_meta})
    for chrom in chroms:
        tiles = sorted(t for c, t in tile_meta if c == chrom)
        tile_start = []
        tile_cpg = []
        tile_n = []
        pattern_tile_id = []
        pattern_id = []
        pattern_count = []
        for local_i, t_idx in enumerate(tiles):
            key = (chrom, t_idx)
            start, positions = tile_meta[key]
            pad = np.zeros(tile_size, dtype=np.uint32)
            pad[: positions.size] = positions
            tile_start.append(start)
            tile_cpg.append(pad)
            tile_n.append(int(tile_n_reads[key]))
            for pid, cnt in sorted(tile_patterns[key].items()):
                pattern_tile_id.append(local_i)
                pattern_id.append(int(pid))
                pattern_count.append(int(cnt))
        for ctx in contexts:
            path = sample_dir / f"{chrom}-{ctx}.patterns.h5"
            data = ReadLevelPatterns(
                context=str(ctx),
                tile_size=int(tile_size),
                tile_start_pos=np.asarray(tile_start, dtype=np.uint32),
                tile_cpg_positions=np.asarray(tile_cpg, dtype=np.uint32).reshape(
                    -1, tile_size
                )
                if tile_cpg
                else np.zeros((0, tile_size), dtype=np.uint32),
                tile_n_reads=np.asarray(tile_n, dtype=np.uint32),
                pattern_tile_id=np.asarray(pattern_tile_id, dtype=np.uint32),
                pattern_id=np.asarray(pattern_id, dtype=np.uint16),
                pattern_count=np.asarray(pattern_count, dtype=np.uint32),
            )
            write_read_level_patterns(path, data)
            written.append(str(path))
    return written


def _build_patterns_from_linear_calls(
    sample_dir: Path,
    by_chrom: Mapping[str, Mapping[str, Any]],
    contexts: Sequence[str],
    *,
    tile_size: int,
) -> List[str]:
    """Surrogate tile patterns from marginal calls (fallback when GAF patterns empty)."""
    from methyl_utils.core.read_level_io import ReadLevelPatterns, write_read_level_patterns

    written: List[str] = []
    for chrom, arrays in by_chrom.items():
        pos = np.asarray(arrays["pos"], dtype=np.uint32)
        mc = np.asarray(arrays["mC"], dtype=np.uint32)
        uc = np.asarray(arrays["uC"], dtype=np.uint32)
        if pos.size == 0:
            continue
        # Tile consecutive CpGs into groups of tile_size.
        n_tiles = int(np.ceil(pos.size / tile_size))
        tile_start = []
        tile_cpg = []
        tile_n = []
        pattern_tile_id = []
        pattern_id = []
        pattern_count = []
        for t in range(n_tiles):
            sl = slice(t * tile_size, min((t + 1) * tile_size, pos.size))
            positions = pos[sl]
            pad = np.zeros(tile_size, dtype=np.uint32)
            pad[: positions.size] = positions
            cov = mc[sl] + uc[sl]
            n_reads = int(cov.max()) if cov.size else 0
            tile_start.append(int(positions[0]))
            tile_cpg.append(pad)
            tile_n.append(n_reads)
            # Encode majority methylated bitmask as a single pattern when coverage > 0.
            if n_reads > 0:
                bits = 0
                for i, (m, u) in enumerate(zip(mc[sl], uc[sl])):
                    if m >= u and m + u > 0:
                        bits |= 1 << (tile_size - 1 - i)
                pattern_tile_id.append(t)
                pattern_id.append(bits)
                pattern_count.append(n_reads)
        for ctx in contexts:
            path = sample_dir / f"{chrom}-{ctx}.patterns.h5"
            data = ReadLevelPatterns(
                context=str(ctx),
                tile_size=int(tile_size),
                tile_start_pos=np.asarray(tile_start, dtype=np.uint32),
                tile_cpg_positions=np.asarray(tile_cpg, dtype=np.uint32).reshape(
                    -1, tile_size
                )
                if tile_cpg
                else np.zeros((0, tile_size), dtype=np.uint32),
                tile_n_reads=np.asarray(tile_n, dtype=np.uint32),
                pattern_tile_id=np.asarray(pattern_tile_id, dtype=np.uint32),
                pattern_id=np.asarray(pattern_id, dtype=np.uint16),
                pattern_count=np.asarray(pattern_count, dtype=np.uint32),
            )
            write_read_level_patterns(path, data)
            written.append(str(path))
    return written


def run_methylgrapher_wgbs_extract(
    *,
    sample_id: str,
    sample_dir: str | Path,
    project: str | Path,
    input_json: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Graph-aware extraction into existing marginal + pattern H5 contracts."""
    payload = dict(input_json or {})
    resolved = dict(payload.get("resolvedConfig") or {})
    sample_path = Path(sample_dir).resolve()
    if not sample_path.is_dir():
        raise RuntimeError(f"sampleDir not found: {sample_path}")

    force = _pick_bool(payload, "forceRealign", False)
    bundle = resolve_wgbs_bundle_from_resolved(resolved)
    contexts = list(bundle.contexts) or ["CG"]
    log_path = sample_path / f"{sample_id}.methylgrapher_extract.log"
    work_dir = sample_path / "methylgrapher_work"
    work_dir.mkdir(parents=True, exist_ok=True)
    gaf_path = sample_path / f"{sample_id}.alignment.gaf"
    if not gaf_path.is_file() and not force:
        # Still allow extract when operator supplies projected linear calls.
        pass

    # Expected H5 presence short-circuit (unless force or GAF newer than H5).
    existing = sorted(sample_path.glob("*-*.h5"))
    existing = [p for p in existing if not p.name.endswith(".patterns.h5")]
    gaf_newer = False
    if existing and gaf_path.is_file():
        gaf_mtime = gaf_path.stat().st_mtime
        gaf_newer = any(gaf_mtime > p.stat().st_mtime for p in existing)
    if existing and not force and not gaf_newer:
        pattern_files = sorted(sample_path.glob("*.patterns.h5"))
        return {
            "sampleId": sample_id,
            "h5Files": [str(p) for p in existing],
            "n_h5_files": len(existing),
            "patternFiles": [str(p) for p in pattern_files],
        }

    dry = os.environ.get("METHYL_METHYLGRAPHER_DRY_RUN", "").strip() in {"1", "true", "yes"}
    index_prefix = resolve_index_prefix(bundle)
    work_dir = work_dir.resolve()
    linear_tsv = work_dir / "linear_cpg_calls.tsv"

    if dry:
        # Synthetic single-site call for contract tests.
        linear_tsv.write_text("chrom\tpos\tmC\tuC\ttnc\n1\t1000\t10\t2\t1\n", encoding="utf-8")
    else:
        wl_gfa = assert_methylcall_assets(bundle, index_prefix)
        # Ensure work_dir has alignment.gaf (methylGrapher MethylCall hardcodes this name).
        work_gaf = work_dir / "alignment.gaf"
        if not work_gaf.is_file() and gaf_path.is_file():
            if gaf_path.resolve() != work_gaf.resolve():
                shutil.copy2(gaf_path, work_gaf)
        # Translate Mojo GBZ-node GAF → GFA named-coordinates before MethylCall.
        if work_gaf.is_file():
            ensure_gaf_named_coordinates(work_gaf, work_dir, bundle, log_path)
            if gaf_path.resolve() != work_gaf.resolve():
                shutil.copy2(work_gaf, gaf_path)
        elif gaf_path.is_file():
            ensure_gaf_named_coordinates(gaf_path, work_dir, bundle, log_path)
            shutil.copy2(gaf_path, work_gaf)

        image = _resolve_image(bundle)
        methyl_cmd = build_methylcall_command(
            bundle=bundle, work_dir=work_dir, index_prefix=index_prefix
        )
        mount_roots = {
            sample_path.resolve(),
            work_dir,
            bundle.c2t_gbz.parent.resolve(),
            Path(index_prefix).parent,
            wl_gfa.resolve().parent,
        }
        # Follow symlink targets so Docker can open {index_prefix}.wl.gfa on local disk.
        if wl_gfa.is_symlink():
            mount_roots.add(wl_gfa.resolve().parent)
        docker_cmd = [
            _docker_bin(),
            "run",
            "--rm",
            "--user",
            _align_docker_user(),
        ]
        for root in sorted(mount_roots, key=str):
            docker_cmd.extend(["-v", f"{root}:{root}"])
        # Optional host overlay so Mojo GAF / MethylCall / QC SAM hotfixes ship
        # without image rebuild.
        add_mojo_src_overlay_mounts(docker_cmd)
        docker_cmd.extend([image, *_with_umask_002(methyl_cmd)])
        _run(docker_cmd, log_path, step="methylGrapher.MethylCall")
        share_work_tree(work_dir)

        merge_cmd = [
            os.environ.get(METHYLGRAPHER_BIN_ENV, "").strip() or "methylGrapher",
            "MergeCpG",
            "-work_dir",
            str(work_dir),
            "-index_prefix",
            index_prefix,
        ]
        docker_merge = [
            _docker_bin(),
            "run",
            "--rm",
            "--user",
            _align_docker_user(),
        ]
        for root in sorted(mount_roots, key=str):
            docker_merge.extend(["-v", f"{root}:{root}"])
        docker_merge.extend([image, *_with_umask_002(merge_cmd)])
        _run(docker_merge, log_path, step="methylGrapher.MergeCpG")
        share_work_tree(work_dir)

        if bundle.conversion_rate_enabled:
            _maybe_run_conversion_rate(
                bundle=bundle,
                sample_path=sample_path,
                work_dir=work_dir,
                index_prefix=index_prefix,
                image=image,
                mount_roots=mount_roots,
                log_path=log_path,
            )

        # Prefer an operator/pre-projected linear TSV if present; else project MergeCpG.
        projected = resolved.get("linear_cpg_tsv") or (
            str(bundle.linear_cpg_tsv) if bundle.linear_cpg_tsv else None
        )
        if projected and Path(str(projected)).is_file():
            shutil.copy2(Path(str(projected)), linear_tsv)
        else:
            graph_cpg = work_dir / "graph.cpg.tsv"
            if not graph_cpg.is_file():
                candidates = list(work_dir.glob("*cpg*.tsv")) + list(
                    work_dir.glob("**/*cpg*.tsv")
                )
                if not candidates:
                    raise RuntimeError(
                        "methylGrapher MergeCpG produced no graph.cpg.tsv; "
                        "provide resolvedConfig.linear_cpg_tsv with chrom/pos/mC/uC columns"
                    )
                graph_cpg = candidates[0]
            # Already linear?
            peek = graph_cpg.read_text(encoding="utf-8", errors="replace").splitlines()[:3]
            header = peek[0].lower() if peek else ""
            if "chrom" in header and "pos" in header:
                shutil.copy2(graph_cpg, linear_tsv)
            else:
                logger.info(
                    "Projecting graph CpGs onto GRCh38 paths from %s", wl_gfa
                )
                offsets = build_grch38_segment_offsets_from_gfa(wl_gfa)
                if not offsets:
                    raise RuntimeError(
                        f"No GRCh38 path offsets parsed from {wl_gfa}; cannot project "
                        "graph.cpg.tsv — pin resolvedConfig.linear_cpg_tsv"
                    )
                n = project_graph_cpg_to_linear_tsv(
                    graph_cpg_tsv=graph_cpg,
                    cpg_registry_tsv=bundle.cpg_tsv,
                    segment_offsets=offsets,
                    out_tsv=linear_tsv,
                )
                if n <= 0:
                    raise RuntimeError(
                        "graph→GRCh38 projection wrote 0 sites (no called CpGs on "
                        f"GRCh38 paths). graph_cpg={graph_cpg} wl_gfa={wl_gfa}"
                    )
                logger.info("Projected %s graph CpG sites to %s", n, linear_tsv)

    by_chrom = _parse_linear_methyl_tsv(linear_tsv)
    if not by_chrom:
        raise RuntimeError(f"No CpG calls parsed from {linear_tsv}")

    h5_files = _write_marginal_h5(sample_path, by_chrom, contexts)
    pattern_files: List[str] = []
    patterns_source = "none"
    if bundle.read_level_enabled:
        if bundle.read_level_gaf_patterns:
            offsets = None
            try:
                wl_path = resolve_wl_gfa_path(bundle, index_prefix)
                if wl_path.is_file():
                    offsets = build_grch38_segment_offsets_from_gfa(wl_path)
            except Exception as exc:
                logger.warning("GAF pattern offsets unavailable: %s", exc)
            if gaf_path.is_file() and offsets:
                pattern_files = _build_patterns_from_gaf(
                    sample_path,
                    gaf_path,
                    by_chrom,
                    contexts,
                    tile_size=bundle.tile_size,
                    segment_offsets=offsets,
                )
                if pattern_files:
                    patterns_source = "gaf"
        else:
            logger.info(
                "read_level.gaf_patterns=false; skipping GAF pattern scan "
                "(use marginal_surrogate)"
            )
        if not pattern_files:
            pattern_files = _build_patterns_from_linear_calls(
                sample_path, by_chrom, contexts, tile_size=bundle.tile_size
            )
            patterns_source = "marginal_surrogate"
    else:
        pattern_files = _write_empty_patterns(
            sample_path, list(by_chrom.keys()), contexts, tile_size=bundle.tile_size
        )
        patterns_source = "empty"

    manifest = build_canonical_extraction_manifest(
        sample_id=sample_id,
        project=project,
        by_chrom=by_chrom,
        contexts=contexts,
        h5_files=h5_files,
        pattern_files=pattern_files,
        gaf_path=gaf_path if gaf_path.is_file() else None,
        graph_assets=fingerprint_wgbs_assets(bundle),
    )
    manifest_path = sample_path / f"{sample_id}.extraction_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if not manifest_path.is_file() or manifest_path.stat().st_size <= 0:
        raise RuntimeError(f"Extraction manifest not written: {manifest_path}")

    return {
        "sampleId": sample_id,
        "h5Files": h5_files,
        "n_h5_files": len(h5_files),
        "patternFiles": pattern_files,
        "patternsSource": patterns_source,
        "manifestPath": str(manifest_path),
    }
