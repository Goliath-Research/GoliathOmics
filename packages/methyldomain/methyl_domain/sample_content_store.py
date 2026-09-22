"""Sample-scoped CAAS store under ``/work/samples/{sample_id}/.caas/``.

Sample-prep and alignment actions write/read a per-sample store instead of the
study ``{project_root}/.caas/``. That store exists to skip repeated GPU/IO work
(FASTQ download, Parabricks/Mojo/methylGrapher align, QC, extract).

The store is keyed by sample identity (``sampleRoot`` / ``{samples_base}/{id}``),
not by an ``align.*`` arm leaf. Products (``{id}.bam``, ``{id}.qc-metrics.tar``)
are restored next to the bound ``sampleDir``.

CAAS is **on by default**. Operators may reject it with task
``sampleCaasEnabled: false`` / ``caasEnabled: false``, or
``METHYL_SAMPLE_CAAS_ENABLED=0`` (false/no/off). Destructive and control-flow
actions remain hard opt-outs. ``sample.archive_sample`` stays etag-only (QNAP
upload skip is not CAAS replay).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping, Optional, Set

# Hard opt-outs even when sample CAAS is enabled.
SAMPLE_CAAS_OPT_OUT: dict[str, str] = {
    "sample.delete_fastqs": "destructive",
    "sample.delete_bam": "destructive",
    "sample.archive_sample": "remote_upload_etag_only",
    "sample.qc_failed": "control_flow",
}

# Prefixes deferred to sample-scoped store (not study .caas).
SAMPLE_SCOPED_PREFIXES = (
    "sample.",
    "parabricks.",
    "proteomics.",
    "align.",
    "methylgrapher.",
)


def sample_caas_env_enabled() -> bool:
    """Return True unless ``METHYL_SAMPLE_CAAS_ENABLED`` explicitly disables."""
    env = os.environ.get("METHYL_SAMPLE_CAAS_ENABLED", "").strip().lower()
    if env in {"0", "false", "no", "off"}:
        return False
    if env in {"1", "true", "yes", "on"}:
        return True
    return True


def sample_caas_enabled(input_json: Optional[Mapping[str, Any]] = None) -> bool:
    """Return True unless the operator explicitly rejected sample-scoped CAAS."""
    if input_json is not None:
        if input_json.get("sampleCaasEnabled") is True:
            return True
        if input_json.get("sampleCaasEnabled") is False:
            return False
        if input_json.get("caasEnabled") is False:
            return False
    return sample_caas_env_enabled()


def is_sample_scoped_action(action_name: str) -> bool:
    return action_name.startswith(SAMPLE_SCOPED_PREFIXES)


def sample_caas_opt_out_reason(action_name: str) -> Optional[str]:
    return SAMPLE_CAAS_OPT_OUT.get(action_name)


def sample_caas_enabled_for_action(
    action_name: str,
    input_json: Optional[Mapping[str, Any]] = None,
) -> bool:
    """True when this sample-scoped action may use CAAS skip/commit."""
    if not is_sample_scoped_action(action_name):
        return False
    if sample_caas_opt_out_reason(action_name) is not None:
        return False
    return sample_caas_enabled(input_json)


# Align/extract arm leaves and experiment mode dirs. Products live here;
# sample identity (and ``.caas``) live on the parent sample root.
_SAMPLE_ARM_PREFIXES = ("align.", "extract.")
_SAMPLE_MODE_LEAVES = frozenset({"linear", "pangenome", "pangenome_wgbs"})


def is_sample_arm_dirname(name: str) -> bool:
    """True when ``name`` is an align/extract arm leaf or experiment mode dir."""
    try:
        from methyl_utils.sample_arm_layout import is_mode_leaf_dirname

        return is_mode_leaf_dirname(name)
    except ImportError:
        if not name or name in {".", ".."}:
            return False
        if name.startswith(_SAMPLE_ARM_PREFIXES):
            return True
        return name in _SAMPLE_MODE_LEAVES


def resolve_sample_id(input_json: Mapping[str, Any]) -> Optional[str]:
    for key in ("sampleId", "sample_id", "domainSample"):
        raw = input_json.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            return text
    sample_dir = input_json.get("sampleDir") or input_json.get("outputDir")
    if sample_dir:
        path = Path(str(sample_dir)).expanduser()
        name = path.name
        if is_sample_arm_dirname(name):
            name = path.parent.name
        if name and name not in {".", ".."} and not is_sample_arm_dirname(name):
            return name
    return None


def samples_base_dir(input_json: Optional[Mapping[str, Any]] = None) -> Path:
    if input_json:
        explicit = input_json.get("samplesBasePath") or input_json.get("samples_base_path")
        if explicit:
            return Path(str(explicit)).expanduser().resolve()
    env = os.environ.get("METHYL_SAMPLES_BASE", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path("/work/samples")


def resolve_sample_caas_root(input_json: Mapping[str, Any]) -> Optional[Path]:
    """Return the sample identity root — store lives at ``.caas/`` under this path.

    Prefer explicit ``sampleRoot`` (planner arm split). If ``sampleDir`` is an
    arm/mode leaf, use its parent so CAAS stays at sample identity, not the arm
    dirname. Otherwise ``{samples_base}/{sample_id}``.
    """
    explicit = input_json.get("sampleRoot")
    if explicit:
        return Path(str(explicit)).expanduser().resolve()
    sample_dir_raw = input_json.get("sampleDir") or input_json.get("outputDir")
    if sample_dir_raw:
        sample_dir = Path(str(sample_dir_raw)).expanduser()
        if is_sample_arm_dirname(sample_dir.name):
            return sample_dir.parent.resolve()
    sample_id = resolve_sample_id(input_json)
    if not sample_id:
        return None
    return samples_base_dir(input_json) / sample_id


def opted_out_sample_actions() -> Set[str]:
    return set(SAMPLE_CAAS_OPT_OUT)


_ALIGN_CAAS_ACTIONS = (
    "sample.parabricks_fq2bam",
    "sample.parabricks_giraffe",
    "sample.methylgrapher_wgbs_align",
)


def _caas_host_roots(sample_path: Path) -> list[Path]:
    """Directories that may hold ``.caas`` for this bound sampleDir.

    Flat layout stores CAAS beside products. Arm/mode leaves keep products in
    ``sampleDir`` while the store is ``{sampleRoot}/.caas``.
    """
    roots: list[Path] = [sample_path]
    if is_sample_arm_dirname(sample_path.name):
        parent = sample_path.parent
        if parent != sample_path:
            roots.append(parent)
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def restore_sample_align_products(sample_dir: Path | str, sample_id: str) -> list[Path]:
    """Restore BAM / qc-metrics.tar product symlinks from sample-scoped CAAS.

    After a harvest whose relink skipped dual-mount product paths, methyl_qc and
    extract look at empty ``{sampleDir}/{id}.bam`` locations even though the
    blobs remain under ``.caas/``. Call this before those actions. Products are
    always restored next to the bound ``sampleDir`` (flat root or arm leaf).
    """
    from .action_result import caas_action_safe_name, caas_root
    from .content_store import _ensure_symlink, link_entry_into_place

    sample_path = Path(sample_dir)
    restored: list[Path] = []
    if not sample_path.is_dir() or not sample_id:
        return restored

    product_names = (
        f"{sample_id}.bam",
        f"{sample_id}.qc-metrics.tar",
        f"{sample_id}.json",
        f"{sample_id}.deduplicate_metrics.txt",
    )
    for host in _caas_host_roots(sample_path):
        caas = caas_root(host)
        for action in _ALIGN_CAAS_ACTIONS:
            action_dir = caas / caas_action_safe_name(action)
            if not action_dir.is_dir():
                continue
            key_dirs = sorted(
                (
                    path
                    for path in action_dir.iterdir()
                    if path.is_dir() and (path / "manifest.json").is_file()
                ),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            for key_dir in key_dirs:
                try:
                    link_entry_into_place(
                        host,
                        action,
                        key_dir.name,
                        output_dir=sample_path,
                    )
                except OSError:
                    pass
                for name in product_names:
                    blob = key_dir / name
                    dest = sample_path / name
                    if not blob.is_file() or blob.is_symlink():
                        continue
                    if dest.is_file() and not dest.is_symlink():
                        continue
                    if dest.is_file():
                        try:
                            if dest.resolve() == blob.resolve():
                                continue
                        except OSError:
                            pass
                    try:
                        _ensure_symlink(dest, blob)
                        restored.append(dest)
                    except OSError:
                        continue
                if (sample_path / f"{sample_id}.bam").is_file() or (
                    sample_path / f"{sample_id}.qc-metrics.tar"
                ).is_file():
                    return restored
    return restored


_FASTQ_SUFFIXES = (".fastq.gz", ".fq.gz", ".fastq", ".fq")
_DOWNLOAD_CAAS_ACTION = "sample.download_fastq"


def _is_fastq_name(path: Path) -> bool:
    name = path.name.lower()
    return any(name.endswith(suffix) for suffix in _FASTQ_SUFFIXES)


def restore_sample_fastq_products(sample_dir: Path | str, sample_id: str) -> list[Path]:
    """Restore download FASTQ product symlinks from sample-scoped CAAS.

    Align discovers pairs under ``sampleDir`` and skips ``.caas``. After harvest
    moves blobs into the content-key directory, product paths can be empty even
    though an even number of FASTQs remain in the store. Clara ``--in-fq`` only
    needs that even count visible outside ``.caas``.
    """
    from .action_result import caas_action_safe_name, caas_root
    from .content_store import (
        _ensure_symlink,
        _is_durable_caas_blob,
        link_entry_into_place,
        read_caas_entry,
    )

    sample_path = Path(sample_dir)
    restored: list[Path] = []
    if not sample_path.is_dir() or not sample_id:
        return restored

    def _link_dest(dest: Path, blob: Path) -> None:
        if _is_durable_caas_blob(dest):
            return
        if dest.is_file() and not dest.is_symlink():
            try:
                if dest.stat().st_size > 0:
                    restored.append(dest)
                    return
            except OSError:
                pass
        try:
            _ensure_symlink(dest, blob)
            restored.append(dest)
        except OSError:
            pass

    for host in _caas_host_roots(sample_path):
        caas = caas_root(host)
        action_dir = caas / caas_action_safe_name(_DOWNLOAD_CAAS_ACTION)
        if not action_dir.is_dir():
            continue
        key_dirs = sorted(
            (
                path
                for path in action_dir.iterdir()
                if path.is_dir() and (path / "manifest.json").is_file()
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for key_dir in key_dirs:
            try:
                link_entry_into_place(
                    host,
                    _DOWNLOAD_CAAS_ACTION,
                    key_dir.name,
                    output_dir=sample_path,
                )
            except OSError:
                pass
            record = read_caas_entry(host, _DOWNLOAD_CAAS_ACTION, key_dir.name)
            output_paths: list[Path] = []
            if record is not None:
                task_output = record.task_output or {}
                raw_files = task_output.get("fastqFiles") or []
                if isinstance(raw_files, list):
                    output_paths = [Path(str(item)) for item in raw_files if item]
            blobs = [
                path
                for path in key_dir.rglob("*")
                if path.is_file()
                and not path.is_symlink()
                and _is_fastq_name(path)
            ]
            for blob in blobs:
                try:
                    rel = blob.relative_to(key_dir)
                except ValueError:
                    rel = Path(blob.name)
                dests = [sample_path / blob.name]
                if rel.parent != Path("."):
                    dests.append(sample_path / rel)
                for dest in output_paths:
                    if dest.name == blob.name and ".caas" not in dest.parts:
                        dests.append(dest)
                seen: set[str] = set()
                for dest in dests:
                    key = str(dest)
                    if key in seen:
                        continue
                    seen.add(key)
                    _link_dest(dest, blob)
            visible = [
                path
                for path in restored
                if path.is_file() and ".caas" not in path.parts
            ]
            if len(visible) >= 2 and len(visible) % 2 == 0:
                return restored
    return restored
