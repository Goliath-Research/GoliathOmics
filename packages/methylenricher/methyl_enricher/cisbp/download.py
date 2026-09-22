"""
Acquire and cache a CIS-BP per-species bulk archive.

CIS-BP has no query API. The "Bulk downloads" page builds a per-species ZIP via
a cart POST and returns an HTML page linking to a generated ``tmp/<name>.zip``.
This module reproduces that flow, caches the extracted archive, and exposes a
:class:`CisbpBundle` (TF_Information table + ``pwms_all_motifs/`` directory).
"""

from __future__ import annotations

import logging
import re
import time
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Iterator, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

TF_INFO_FILENAME = "TF_Information.txt"
PWM_DIRNAME = "pwms_all_motifs"

# CIS-BP TF_Status -> human-readable motif evidence.
_STATUS_TO_EVIDENCE = {"D": "Direct", "I": "Inferred", "N": "None"}


@dataclass
class CisbpBundle:
    """A resolved CIS-BP species bundle on local disk."""

    root: Path
    species: str
    build: str

    @property
    def tf_info_path(self) -> Path:
        return self.root / TF_INFO_FILENAME

    @property
    def pwm_dir(self) -> Path:
        return self.root / PWM_DIRNAME

    def is_complete(self) -> bool:
        return self.tf_info_path.is_file() and self.pwm_dir.is_dir()

    def load_tf_info(self, motif_evidence: Optional[List[str]] = None) -> pd.DataFrame:
        """
        Load TF_Information.txt, keeping rows that have a usable motif.

        ``motif_evidence`` filters by readable status names ("Direct",
        "Inferred", "None"); default keeps Direct + Inferred.
        """
        df = pd.read_csv(self.tf_info_path, sep="\t", dtype=str).fillna("")
        required = {"Motif_ID", "TF_Name", "TF_Status"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(
                f"CIS-BP TF_Information.txt missing expected columns {sorted(missing)}; "
                f"found {list(df.columns)[:10]}..."
            )
        # Drop rows without a real motif id (".", empty, or no PWM file).
        df = df[df["Motif_ID"].astype(str).str.strip().replace({".": ""}) != ""]
        df["motif_evidence"] = df["TF_Status"].map(_STATUS_TO_EVIDENCE).fillna(df["TF_Status"])
        wanted = motif_evidence or ["Direct", "Inferred"]
        wanted_norm = {str(w).strip().lower() for w in wanted}
        df = df[df["motif_evidence"].str.lower().isin(wanted_norm)]
        return df.reset_index(drop=True)


def _cache_root(cache_dir: Path, species: str, build: str) -> Path:
    return Path(cache_dir) / "cisbp" / f"{species}_{build}".replace(" ", "_")


@contextmanager
def _bundle_download_lock(
    lock_path: Path,
    *,
    timeout_seconds: int = 1800,
    poll_seconds: float = 0.5,
) -> Iterator[None]:
    """
    Acquire an inter-process advisory lock for CIS-BP bundle materialization.

    Uses ``fcntl.flock`` on Unix-like systems; if unavailable, falls back to a
    no-op context (best effort for non-Unix platforms).
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        try:
            import fcntl  # type: ignore
        except Exception:
            # Non-Unix environment; proceed without cross-process lock.
            yield
            return

        start = time.monotonic()
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if (time.monotonic() - start) >= float(timeout_seconds):
                    raise TimeoutError(
                        f"Timed out waiting for CIS-BP cache lock: {lock_path}"
                    )
                time.sleep(float(poll_seconds))

        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def resolve_bundle(
    *,
    species: str = "Homo_sapiens",
    build: str = "3.10",
    data_dir: Optional[str] = None,
    cache_dir: Optional[str] = None,
    auto_download: bool = True,
    base_url: str = "https://cisbp.ccbr.utoronto.ca",
    archive_url: Optional[str] = None,
    components: Optional[List[str]] = None,
) -> CisbpBundle:
    """
    Return a usable CIS-BP bundle, downloading/extracting on demand.

    Resolution order:
      1. ``data_dir`` pointing at a pre-extracted bundle.
      2. Cached extraction under ``cache_dir``.
      3. Download (cart POST or explicit ``archive_url``) when ``auto_download``.
    """
    if data_dir:
        root = Path(data_dir)
        # Allow either the bundle root or a parent containing the species dir.
        candidates = [root, root / f"{species}_{build}", _cache_root(root, species, build)]
        for cand in candidates:
            bundle = CisbpBundle(root=cand, species=species, build=build)
            if bundle.is_complete():
                logger.info("[CIS-BP] using local bundle: %s", cand)
                return bundle
        raise FileNotFoundError(
            f"CIS-BP data_dir '{data_dir}' does not contain {TF_INFO_FILENAME} and "
            f"{PWM_DIRNAME}/ (looked in: {[str(c) for c in candidates]})."
        )

    if cache_dir is None:
        cache_dir = str(Path.home() / ".methyl_enricher")
    root = _cache_root(Path(cache_dir), species, build)
    bundle = CisbpBundle(root=root, species=species, build=build)
    if bundle.is_complete():
        logger.info("[CIS-BP] using cached bundle: %s", root)
        return bundle

    if not auto_download:
        raise FileNotFoundError(
            f"CIS-BP bundle not cached at {root} and auto_download is disabled. "
            f"Set cisbp.data_dir to a pre-extracted archive or enable auto_download."
        )

    lock_path = root.parent / f"{root.name}.download.lock"
    with _bundle_download_lock(lock_path):
        # Another worker may have completed the bundle while we waited for lock.
        if bundle.is_complete():
            logger.info("[CIS-BP] using cached bundle (post-lock): %s", root)
            return bundle

        root.mkdir(parents=True, exist_ok=True)
        zip_bytes = _download_species_zip(
            species=species,
            base_url=base_url,
            archive_url=archive_url,
            components=components or ["TF_Information", "PWMs"],
        )
        _extract_bundle(zip_bytes, root)
        if not bundle.is_complete():
            raise RuntimeError(
                f"CIS-BP archive extracted to {root} but is missing {TF_INFO_FILENAME} "
                f"or {PWM_DIRNAME}/."
            )
        logger.info("[CIS-BP] downloaded + cached bundle: %s", root)
        return bundle


def _download_species_zip(
    *,
    species: str,
    base_url: str,
    archive_url: Optional[str],
    components: List[str],
) -> bytes:
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - requests is a hard dep
        raise RuntimeError("CIS-BP download requires the 'requests' package.") from exc

    base_url = base_url.rstrip("/")
    session = requests.Session()
    session.headers.update({"User-Agent": "MethylEnricher-CISBP/1.0"})

    if archive_url:
        logger.info("[CIS-BP] downloading explicit archive_url: %s", archive_url)
        resp = session.get(archive_url, timeout=600)
        resp.raise_for_status()
        return resp.content

    # Cart POST builds the per-species archive and returns an HTML link page.
    form = [("selSpec", species)]
    form += [("Spec[]", c) for c in components]
    form.append(("submit", "Download Species Archive!"))
    logger.info("[CIS-BP] requesting %s archive (%s)...", species, ", ".join(components))
    post = session.post(f"{base_url}/bulk_archive.php", data=form, timeout=600)
    post.raise_for_status()

    match = re.search(r'href="(tmp/[^"]+\.zip)"', post.text)
    if not match:
        snippet = post.text[:400].replace("\n", " ")
        raise RuntimeError(
            "CIS-BP bulk download did not return an archive link. "
            f"Response started with: {snippet!r}"
        )
    archive_path = match.group(1)
    download_url = f"{base_url}/{archive_path}"
    logger.info("[CIS-BP] fetching generated archive: %s", download_url)
    resp = session.get(download_url, timeout=900)
    resp.raise_for_status()
    return resp.content


def _extract_bundle(zip_bytes: bytes, root: Path) -> None:
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        if not any(n.endswith(TF_INFO_FILENAME) or PWM_DIRNAME in n for n in names):
            raise RuntimeError(
                f"CIS-BP archive does not look right; entries: {names[:5]}..."
            )
        zf.extractall(root)
    # Some archives may nest content one level deep; flatten if needed.
    if not (root / TF_INFO_FILENAME).is_file():
        for child in root.iterdir():
            if child.is_dir() and (child / TF_INFO_FILENAME).is_file():
                for item in child.iterdir():
                    item.rename(root / item.name)
                break
