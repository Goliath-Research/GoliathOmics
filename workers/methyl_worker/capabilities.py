"""Detect and validate worker capability sets for registration and dispatch."""

from __future__ import annotations

import logging
import os
import shutil
from typing import FrozenSet, Optional, Sequence

logger = logging.getLogger(__name__)

OMNIBUS_WILDCARD = "*"

# Capabilities that require a functional NVIDIA GPU at execute time (defense-in-depth).
# Non-Parabricks GPU Docker tools: capability -> image env var. Each requires a
# functional GPU plus its own configured Docker image (own arm64/multi-arch image for
# GH200). Do NOT overload METHYL_PARABRICKS_IMAGE (the Parabricks probe treats that as
# "Parabricks present").
DOCKER_GPU_TOOL_IMAGE_ENV: dict = {
    "proteomics.diann": "METHYL_DIANN_IMAGE",
    "proteomics.prosit": "METHYL_PROSIT_IMAGE",
    "proteomics.casanovo": "METHYL_CASANOVO_IMAGE",
}

GPU_REQUIRED_CAPABILITIES: FrozenSet[str] = frozenset(
    {
        "parabricks.fq2bam",
        "parabricks.giraffe",
        "parabricks.rna_fq2bam",
        "parabricks.kallisto",
        "methyl-centroid",
        "proteomics.diann",
        "proteomics.prosit",
        "proteomics.casanovo",
        # GH200 dual-graph Align fleet marker (config align_engine=gpu_giraffe).
        "methylgrapher.wgbs_gpu_align",
    }
)

# Host OS packages (setup_host.sh --system-deps) — not on shared /work NFS.
# Align packaging calls samtools on the worker host after Mojo dual-map / QC.
HOST_SAMTOOLS_CAPABILITIES: FrozenSet[str] = frozenset(
    {
        "methylgrapher.wgbs_align",
        "methylgrapher.wgbs_gpu_align",
        "methyl-qc",  # optional flagstat path
    }
)

HOST_BEDTOOLS_CAPABILITIES: FrozenSet[str] = frozenset(
    {
        "methyl-mapper",
    }
)

# Probe kinds for catalog-derived auto-detection (not tunable science parameters).
_PROBE_ALWAYS = "always"
_PROBE_CLI = "cli"
_PROBE_PARABRICKS = "parabricks"
_PROBE_DOCKER_GPU = "docker_gpu"
_PROBE_SAGE = "sage"
_PROBE_EXTRACTOR = "extractor"


def _capability_probe_kind(capability: str, *, execution_mode: str, cli_tool: Optional[str]) -> str:
    """Classify how auto-detect decides whether a catalog capability is available."""
    if capability in (
        "parabricks.fq2bam",
        "parabricks.giraffe",
        "parabricks.rna_fq2bam",
        "parabricks.kallisto",
    ):
        return _PROBE_PARABRICKS
    if capability in DOCKER_GPU_TOOL_IMAGE_ENV:
        return _PROBE_DOCKER_GPU
    if capability == "proteomics.sage":
        return _PROBE_SAGE
    if capability == "methyl-extract":
        return _PROBE_EXTRACTOR
    if execution_mode == "cli" and cli_tool:
        return _PROBE_CLI
    return _PROBE_ALWAYS


def _sage_available() -> bool:
    """True when Sage (CPU DDA) can run: a Docker image env or the native binary."""
    try:
        from methyl_worker.sage_runner import sage_available

        return bool(sage_available())
    except Exception:
        if os.environ.get("METHYL_SAGE_IMAGE", "").strip() and shutil.which("docker"):
            return True
        binary = os.environ.get("METHYL_SAGE_BIN", "").strip() or "sage"
        return shutil.which(binary) is not None


def _docker_gpu_tool_available(capability: str) -> bool:
    """True when the tool's image env is configured (image pulled into shared store)."""
    env = DOCKER_GPU_TOOL_IMAGE_ENV.get(capability)
    if not env:
        return False
    if not os.environ.get(env, "").strip():
        return False
    return shutil.which("docker") is not None


def _catalog_capability_rows() -> list[tuple[str, str, Optional[str]]]:
    """Return (capability, probe_kind, cli_tool) derived from ACTION_CATALOG."""
    from .action_catalog import ACTION_CATALOG

    rows: list[tuple[str, str, Optional[str]]] = []
    seen: set[str] = set()
    for entry in ACTION_CATALOG:
        if entry.capability in seen:
            continue
        seen.add(entry.capability)
        kind = _capability_probe_kind(
            entry.capability,
            execution_mode=entry.execution_mode,
            cli_tool=entry.cli_tool,
        )
        rows.append((entry.capability, kind, entry.cli_tool))
    return rows


def _gpu_available() -> bool:
    try:
        from methyl_utils.gpu_detection import is_gpu_available

        return bool(is_gpu_available())
    except Exception:
        return shutil.which("nvidia-smi") is not None


def _parabricks_available() -> bool:
    if os.environ.get("METHYL_PARABRICKS_IMAGE", "").strip():
        return True
    if shutil.which("docker") is None:
        return False
    try:
        import subprocess

        proc = subprocess.run(
            ["docker", "images", "-q", "nvcr.io/nvidia/clara-parabricks"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return bool(proc.stdout.strip())
    except Exception:
        return False


def _extractor_available() -> bool:
    if os.environ.get("METHYL_EXTRACTOR_BIN", "").strip():
        return True
    return shutil.which("MethylExtractor") is not None


def _cli_on_path(binary: str) -> bool:
    return shutil.which(binary) is not None


def _host_tools_ok_for(capability: str) -> bool:
    """True when per-VM apt CLIs required by ``capability`` are on PATH."""
    if capability in HOST_SAMTOOLS_CAPABILITIES and not _cli_on_path("samtools"):
        logger.warning(
            "Omitting %s: samtools not on PATH (run setup_host.sh --system-deps)",
            capability,
        )
        return False
    if capability in HOST_BEDTOOLS_CAPABILITIES and not _cli_on_path("bedtools"):
        logger.warning(
            "Omitting %s: bedtools not on PATH (run setup_host.sh --system-deps)",
            capability,
        )
        return False
    return True


def resolve_worker_capabilities(
    *,
    explicit: Optional[Sequence[str]] = None,
) -> list[str]:
    """
    Compute the capability set this node can serve.

    When ``explicit`` is provided, returns that set (deduplicated). A single ``*`` entry
    means omnibus (matches any task capability at dispatch).

    Otherwise probes GPU, Parabricks, extractor, host tools, and installed CLIs from
    the action catalog.
    """
    if explicit:
        normalized = [str(c).strip() for c in explicit if str(c).strip()]
        if OMNIBUS_WILDCARD in normalized:
            return [OMNIBUS_WILDCARD]
        return sorted(set(normalized))

    caps: set[str] = set()
    gpu = _gpu_available()
    parabricks_ok = _parabricks_available()
    extractor_ok = _extractor_available()

    for capability, kind, cli_tool in _catalog_capability_rows():
        if kind == _PROBE_ALWAYS:
            if _host_tools_ok_for(capability):
                caps.add(capability)
            continue
        if kind == _PROBE_CLI:
            if not cli_tool or not _cli_on_path(cli_tool):
                continue
            if capability in GPU_REQUIRED_CAPABILITIES and not gpu:
                logger.debug("Skipping %s: GPU required but not available", capability)
                continue
            if not _host_tools_ok_for(capability):
                continue
            caps.add(capability)
            continue
        if kind == _PROBE_PARABRICKS:
            if parabricks_ok and gpu:
                caps.add(capability)
            elif parabricks_ok and not gpu:
                logger.warning(
                    "Parabricks image configured but no GPU; omitting %s", capability
                )
            continue
        if kind == _PROBE_DOCKER_GPU:
            if _docker_gpu_tool_available(capability) and gpu:
                caps.add(capability)
            elif _docker_gpu_tool_available(capability) and not gpu:
                logger.warning(
                    "%s image configured but no GPU; omitting %s",
                    DOCKER_GPU_TOOL_IMAGE_ENV.get(capability),
                    capability,
                )
            continue
        if kind == _PROBE_SAGE:
            # CPU tool (Sage DDA): no GPU required.
            if _sage_available():
                caps.add(capability)
            continue
        if kind == _PROBE_EXTRACTOR:
            if extractor_ok:
                caps.add(capability)
            continue

    # Fleet marker for GH200 dual-graph Align (not a separate action capability).
    # Operators filter enroll / pools by this string when using align_engine=gpu_giraffe.
    if gpu and _host_tools_ok_for("methylgrapher.wgbs_gpu_align"):
        caps.add("methylgrapher.wgbs_gpu_align")

    if not caps:
        # Do not fall back to ["*"]: silent omnibus let half-enrolled VMs claim any
        # task. Callers that want omnibus must pass explicit=["*"] / --omnibus /
        # --capabilities-json '["*"]'.
        logger.warning(
            "resolve_worker_capabilities: no capabilities detected; returning empty set"
        )
        return []

    return sorted(caps)


def capability_requires_gpu(capability: str) -> bool:
    return capability in GPU_REQUIRED_CAPABILITIES


def assert_node_can_serve_capability(capability: str) -> None:
    """Raise RuntimeError when WORKER_CAPABILITY targets hardware this node lacks."""
    if not capability or capability == OMNIBUS_WILDCARD:
        return
    if capability_requires_gpu(capability) and not _gpu_available():
        raise RuntimeError(
            f"Worker configured for capability {capability!r} but no functional GPU was detected. "
            "Install NVIDIA drivers/CuPy, verify nvidia-smi, or run a CPU-only capability unit."
        )
    if (
        capability
        in ("parabricks.fq2bam", "parabricks.giraffe", "parabricks.rna_fq2bam", "parabricks.kallisto")
        and not _parabricks_available()
    ):
        raise RuntimeError(
            f"Worker configured for capability {capability!r} but Parabricks is not available. "
            "Set METHYL_PARABRICKS_IMAGE or install the nvcr.io Parabricks image."
        )
    if capability in DOCKER_GPU_TOOL_IMAGE_ENV and not _docker_gpu_tool_available(capability):
        env = DOCKER_GPU_TOOL_IMAGE_ENV[capability]
        raise RuntimeError(
            f"Worker configured for capability {capability!r} but its Docker image is not available. "
            f"Set {env} to an arm64/multi-arch image pulled into the shared Docker store."
        )
    if capability == "proteomics.sage" and not _sage_available():
        raise RuntimeError(
            "Worker configured for capability 'proteomics.sage' but Sage is not available. "
            "Set METHYL_SAGE_IMAGE or install the `sage` binary (METHYL_SAGE_BIN / PATH)."
        )
    if capability == "methyl-extract" and not _extractor_available():
        raise RuntimeError(
            "Worker configured for methyl-extract but MethylExtractor is not on PATH. "
            "Set METHYL_EXTRACTOR_BIN or install MethylExtractor."
        )
    if capability in HOST_SAMTOOLS_CAPABILITIES and not _cli_on_path("samtools"):
        raise RuntimeError(
            f"Worker configured for capability {capability!r} but samtools is not on PATH. "
            "Install per-VM host deps: setup_host.sh --system-deps (or "
            "install_host_tools_gpu_vm.sh). Shared /work does not provide samtools."
        )
    if capability in HOST_BEDTOOLS_CAPABILITIES and not _cli_on_path("bedtools"):
        raise RuntimeError(
            f"Worker configured for capability {capability!r} but bedtools is not on PATH. "
            "Install per-VM host deps: setup_host.sh --system-deps."
        )


def assert_execute_gpu_prereqs(capability: str, action_name: str) -> None:
    """Execute-time guard; should not trigger when dispatch is capability-based."""
    if not capability_requires_gpu(capability):
        return
    if _gpu_available():
        return
    raise RuntimeError(
        f"Action {action_name!r} (capability {capability!r}) requires a GPU but none is available. "
        "Re-register this worker with detected capabilities or move the task to a GPU pool."
    )


def assert_execute_host_tool_prereqs(capability: str, action_name: str) -> None:
    """Execute-time guard for per-VM apt CLIs (samtools/bedtools)."""
    if capability in HOST_SAMTOOLS_CAPABILITIES and not _cli_on_path("samtools"):
        raise RuntimeError(
            f"Action {action_name!r} (capability {capability!r}) requires host samtools. "
            "Install on this VM: bash …/install_host_tools_gpu_vm.sh "
            "(setup_host.sh --system-deps). Shared /work does not ship samtools."
        )
    if capability in HOST_BEDTOOLS_CAPABILITIES and not _cli_on_path("bedtools"):
        raise RuntimeError(
            f"Action {action_name!r} (capability {capability!r}) requires host bedtools. "
            "Install on this VM: setup_host.sh --system-deps."
        )
