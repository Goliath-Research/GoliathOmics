"""Typed registry that identifies real reference samples used for testing.

This is *test-infrastructure* configuration, not tunable science configuration.
It names, per analyte (e.g. ``cfdna``, ``buffy_coat``), a single real reference
sample directory (containing extracted ``{chr}-{ctx}.h5`` files) plus optional
named groups of samples (e.g. ``healthy``, ``PCa``) for cohort-level tests.

Where operators set it (precedence, highest wins):

1. ``METHYL_TEST_DATA_CONFIG`` -> path to a standalone registry JSON.
2. Site manifest ``testing`` block (``METHYL_SITE_CONFIG`` / ``/work/site/methyl_site.json``).
3. Committed repo default (``tests/real_data/registry.json``) -> points at the
   small committed real-format fixture, when present.

Paths may be absolute (``/work/samples/...``) or repo-relative (resolved against
the repository root). Tests must skip cleanly when a referenced sample is not
mounted; see ``methyl_utils.testing.real_data``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

TEST_DATA_CONFIG_ENV = "METHYL_TEST_DATA_CONFIG"
SAMPLE_PREP_CANARY_CONFIG_ENV = "METHYL_SAMPLE_PREP_CANARY_CONFIG"
SITE_CONFIG_ENV = "METHYL_SITE_CONFIG"
DEFAULT_SITE_PATH = Path("/work/site/methyl_site.json")


class TestSampleRef(BaseModel):
    """One designated real reference sample (a directory of ``{chr}-{ctx}.h5``)."""

    __test__ = False  # not a pytest test class despite the ``Test`` prefix

    model_config = ConfigDict(extra="forbid")

    sample_id: Optional[str] = Field(
        default=None, description="Stable identifier for the reference sample."
    )
    sample_dir: Optional[str] = Field(
        default=None,
        description="Directory holding extracted {chr}-{ctx}.h5 files. Absolute (e.g. "
        "/work/samples/<id>) or repo-relative (resolved from the repository root).",
    )
    analyte: Optional[str] = Field(
        default=None,
        description="Canonical analyte (cfdna, buffy_coat, combined, ...) this sample represents.",
    )
    chromosomes: Optional[List[str]] = Field(
        default=None, description="Chromosomes available for this reference sample (e.g. ['21'])."
    )
    contexts: Optional[List[str]] = Field(
        default=None, description="Methylation contexts available (e.g. ['CG'])."
    )
    description: Optional[str] = Field(
        default=None, description="Human-readable purpose/notes for the reference sample."
    )
    provenance: Optional[str] = Field(
        default=None,
        description="Source, consent basis, and producing MethylExtractor release for "
        "regulatory traceability (non-PHI sources only).",
    )


class TestSampleGroup(BaseModel):
    """A named cohort of real reference samples for cohort-level tests."""

    __test__ = False  # not a pytest test class despite the ``Test`` prefix

    model_config = ConfigDict(extra="forbid")

    label: Optional[str] = Field(
        default=None, description="Cohort label (e.g. healthy, PCa, PCa1)."
    )
    analyte: Optional[str] = Field(
        default=None, description="Canonical analyte for the group (cfdna, buffy_coat, ...)."
    )
    sample_dirs: Optional[List[str]] = Field(
        default=None,
        description="Sample directories in the group (absolute or repo-relative).",
    )
    description: Optional[str] = Field(
        default=None, description="Human-readable purpose/notes for the group."
    )


class SamplePrepCanarySource(BaseModel):
    """Provenance for the pinned public SamplePrep canary FASTQ pair."""

    __test__ = False
    model_config = ConfigDict(extra="forbid")

    geo_series: Optional[str] = Field(default=None, description="GEO series accession (e.g. GSE261315).")
    geo_sample: Optional[str] = Field(default=None, description="GEO sample accession (e.g. GSM8140413).")
    sra_run: Optional[str] = Field(default=None, description="SRA run accession (e.g. SRR28293403).")
    bioproject: Optional[str] = Field(default=None, description="BioProject accession.")
    individual_id: Optional[str] = Field(
        default=None, description="Donor / HPRC individual id when known (e.g. HG00621)."
    )
    title: Optional[str] = Field(default=None, description="GEO/SRA sample title.")
    library_strategy: Optional[str] = Field(default=None, description="SRA library strategy (Bisulfite-Seq).")
    library_layout: Optional[str] = Field(default=None, description="PAIRED or SINGLE.")
    platform: Optional[str] = Field(default=None, description="Sequencing platform string.")
    license_note: Optional[str] = Field(
        default=None, description="Public/consent basis and citation requirements (non-PHI only)."
    )
    pubmed_id: Optional[str] = Field(default=None, description="Primary publication PubMed id when available.")
    source_urls: Optional[List[str]] = Field(default=None, description="Canonical GEO/SRA/BioProject URLs.")
    notes: Optional[str] = Field(default=None, description="Operator notes / why this run was pinned.")


class SamplePrepCanaryFastqPair(BaseModel):
    """Checksummed FASTQ pair layout under fastqStorage (full or subset tier)."""

    __test__ = False
    model_config = ConfigDict(extra="forbid")

    prefix: Optional[str] = Field(
        default=None,
        description="Object prefix under fastqStorage for this tier (e.g. canary/.../subset/).",
    )
    r1_name: Optional[str] = Field(default=None, description="R1 filename under prefix.")
    r2_name: Optional[str] = Field(default=None, description="R2 filename under prefix.")
    r1_sha256: Optional[str] = Field(
        default=None, description="SHA-256 of R1 after provisioning (operator-filled)."
    )
    r2_sha256: Optional[str] = Field(
        default=None, description="SHA-256 of R2 after provisioning (operator-filled)."
    )
    expected_read_pairs: Optional[int] = Field(
        default=None, ge=1, description="Expected paired-read count for preflight checks."
    )
    size_bytes_r1: Optional[int] = Field(default=None, ge=0, description="R1 size in bytes after provisioning.")
    size_bytes_r2: Optional[int] = Field(default=None, ge=0, description="R2 size in bytes after provisioning.")


class SamplePrepCanaryThresholds(BaseModel):
    """Operator-set acceptance bounds for linear vs methylGrapher comparison.

    Stock Giraffe (``pangenome``) is an engineering comparator and does not use
    these biological parity thresholds.
    """

    __test__ = False
    model_config = ConfigDict(extra="forbid")

    mapping_rate_delta: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Max absolute mapping-rate delta between linear and pangenome_wgbs.",
    )
    duplication_rate_delta: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Max absolute duplication-rate delta between linear and pangenome_wgbs.",
    )
    cpg_sites_min_fraction_of_linear: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Minimum CpG sites called by pangenome_wgbs as a fraction of linear.",
    )
    mean_coverage_delta: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Max absolute mean-coverage delta between linear and pangenome_wgbs.",
    )
    require_extraction_qc_pass: Optional[bool] = Field(
        default=None,
        description="When true, every mode must pass extraction QC guardrails.",
    )
    require_read_level_patterns: Optional[bool] = Field(
        default=None,
        description="When true, methylGrapher must emit non-empty patterns.h5 sidecars.",
    )
    alignment_time_ratio_max: Optional[float] = Field(
        default=None,
        ge=0.0,
        description=(
            "Max allowed (pangenome_wgbs alignment_ms / linear alignment_ms). "
            "Operator-set cost gate for the dual-mode compare experiment."
        ),
    )
    cpg_coverage_min_fraction_of_linear: Optional[float] = Field(
        default=None,
        ge=0.0,
        description=(
            "Minimum pangenome_wgbs CpG weighted-mean coverage as a fraction of linear. "
            "Hypothesis: usable CpG read support should not collapse vs linear."
        ),
    )
    cpg_depth_thresholds: Optional[List[int]] = Field(
        default=None,
        description=(
            "Operator-set coverage depths for optional H5 fraction-of-CpGs stats "
            "(e.g. [1, 5, 10]). Empty/unset skips H5 depth fractions."
        ),
    )
    require_alignment_qc: Optional[bool] = Field(
        default=None,
        description=(
            "When false, missing alignment QC does not fail the mode report "
            "(science-only / report-only compares). Unset/true keeps the hard check."
        ),
    )
    overlap_recall_min_of_linear: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum fraction of linear CG H5 sites also present in pangenome_wgbs "
            "(shared chrom/pos). Operator-set site-overlap recall gate."
        ),
    )
    overlap_mean_abs_meth_delta_max: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Max mean |meth_wgbs − meth_linear| on overlapping CG sites. "
            "Operator-set methylation concordance gate."
        ),
    )
    overlap_meth_pearson_min: Optional[float] = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        description=(
            "Minimum Pearson correlation of methylation fractions on overlapping "
            "CG sites. Operator-set concordance gate."
        ),
    )
    overlap_median_cov_ratio_max: Optional[float] = Field(
        default=None,
        ge=0.0,
        description=(
            "Max median (wgbs_cov / linear_cov) on overlapping sites. Optional — "
            "MethylCall vs MethylExtractor coverage scales differ; leave unset "
            "unless comparing like-for-like extractors."
        ),
    )
    overlap_chromosomes: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional chromosome list for overlap stats (e.g. [\"1\",\"2\"]). "
            "Unset = all *-CG.h5 chromosomes present in both arms."
        ),
    )


class SamplePrepCanaryAssetPins(BaseModel):
    """Non-secret reference / image pins required for the three-mode canary."""

    __test__ = False
    model_config = ConfigDict(extra="forbid")

    linear_genome_key: Optional[str] = Field(
        default=None, description="Site genomes.linear selection key / release pin."
    )
    pangenome_key: Optional[str] = Field(
        default=None, description="Site genomes.pangenome (stock Giraffe) pin."
    )
    pangenome_wgbs_key: Optional[str] = Field(
        default=None, description="Site genomes.pangenome_wgbs (methylGrapher BS) pin."
    )
    methylgrapher_image_env: Optional[str] = Field(
        default=None,
        description="Env var name holding the methylGrapher image pin (e.g. METHYL_METHYLGRAPHER_IMAGE).",
    )


class SamplePrepCanaryConfig(BaseModel):
    """Typed SamplePrep real-data canary definition (site/testing or standalone JSON)."""

    __test__ = False
    model_config = ConfigDict(extra="forbid")

    sample_id: Optional[str] = Field(
        default=None, description="Stable canary sample id base (modes append a suffix)."
    )
    analyte: Optional[str] = Field(
        default=None, description="Canonical analyte label for the canary (e.g. buffy_coat)."
    )
    source: Optional[SamplePrepCanarySource] = Field(
        default=None, description="Pinned public provenance for the FASTQ pair."
    )
    fastq_storage: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Non-secret FastqStorageRef-shaped metadata (type/bucket/basePath/endpointRef). "
        "Credentials stay in cfg.credential and are injected at schedule time.",
    )
    full: Optional[SamplePrepCanaryFastqPair] = Field(
        default=None, description="Full-run FASTQ pair under fastqStorage."
    )
    subset: Optional[SamplePrepCanaryFastqPair] = Field(
        default=None, description="Deterministic paired-read subset under fastqStorage."
    )
    subset_read_pairs: Optional[int] = Field(
        default=None,
        ge=1,
        description="Target paired-read count used when provisioning the subset tier "
        "(operator-set; not a Python science default).",
    )
    subset_seed: Optional[int] = Field(
        default=None,
        description="Reserved for alternate subset strategies; first-N recipe ignores seed.",
    )
    modes: Optional[List[str]] = Field(
        default=None,
        description="Alignment modes to run: linear, pangenome, pangenome_wgbs.",
    )
    thresholds: Optional[SamplePrepCanaryThresholds] = Field(
        default=None,
        description="Operator-set acceptance bounds for linear vs pangenome_wgbs.",
    )
    asset_pins: Optional[SamplePrepCanaryAssetPins] = Field(
        default=None, description="Reference genome / image pins for preflight."
    )
    directional: Optional[bool] = Field(
        default=None,
        description="Preferred methylGrapher directional flag for this library chemistry.",
    )
    library_protocol: Optional[str] = Field(
        default=None,
        description="Procedure libraryProtocol hint (e.g. wgbs_linear / wgbs_pangenome).",
    )
    provenance_path: Optional[str] = Field(
        default=None,
        description="Repo-relative or absolute path to the committed provenance JSON.",
    )


class TestDataRegistry(BaseModel):
    """Registry of real reference samples and groups used by ``real_data`` tests."""

    __test__ = False  # not a pytest test class despite the ``Test`` prefix

    model_config = ConfigDict(extra="forbid")

    committed_fixture_dir: Optional[str] = Field(
        default=None,
        description="Repo-relative directory holding the small committed real-format H5 "
        "fixture (runs on hosted CI). Absent/empty when only /work samples are used.",
    )
    samples: Optional[Dict[str, TestSampleRef]] = Field(
        default=None,
        description="Reference samples keyed by analyte or logical name "
        "(e.g. {'cfdna': {...}, 'buffy_coat': {...}}).",
    )
    groups: Optional[Dict[str, TestSampleGroup]] = Field(
        default=None,
        description="Named cohorts keyed by group name (e.g. {'healthy': {...}, 'PCa': {...}}).",
    )
    sample_prep_canary: Optional[SamplePrepCanaryConfig] = Field(
        default=None,
        description="Optional SamplePrep real-WGBS canary definition (full + subset tiers, "
        "three alignment modes). Set under site testing or METHYL_TEST_DATA_CONFIG.",
    )


def _load_json(path: Path) -> Dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def repo_root() -> Path:
    """Repository root (packages/methylutils/methyl_utils -> parents[3])."""
    return Path(__file__).resolve().parents[3]


def load_test_data_registry(path: Optional[str | Path] = None) -> TestDataRegistry:
    """Resolve the test-data registry from env, site manifest, or the repo default.

    Precedence (highest wins): explicit ``path`` -> ``METHYL_TEST_DATA_CONFIG`` ->
    site manifest ``testing`` block -> committed ``tests/real_data/registry.json`` ->
    empty registry.
    """
    explicit = path or os.environ.get(TEST_DATA_CONFIG_ENV)
    if explicit:
        p = Path(str(explicit)).expanduser()
        if p.is_file():
            return TestDataRegistry.model_validate(_load_json(p))

    site_raw = os.environ.get(SITE_CONFIG_ENV) or str(DEFAULT_SITE_PATH)
    site_path = Path(site_raw).expanduser()
    if site_path.is_file():
        site = _load_json(site_path)
        testing = site.get("testing")
        if isinstance(testing, dict):
            return TestDataRegistry.model_validate(testing)

    default_path = repo_root() / "tests" / "real_data" / "registry.json"
    if default_path.is_file():
        return TestDataRegistry.model_validate(_load_json(default_path))

    return TestDataRegistry()


def resolve_sample_dir(sample_dir: Optional[str]) -> Optional[Path]:
    """Resolve a registry ``sample_dir`` to an absolute path (repo-relative allowed)."""
    if not sample_dir:
        return None
    p = Path(sample_dir).expanduser()
    if p.is_absolute():
        return p
    return (repo_root() / p).resolve()


def sample_has_h5(sample_dir: Optional[Path]) -> bool:
    """True when the directory exists and holds at least one ``{chr}-{ctx}.h5`` file."""
    if sample_dir is None or not sample_dir.is_dir():
        return False
    return any(sample_dir.glob("*-*.h5"))


def load_sample_prep_canary(path: Optional[str | Path] = None) -> Optional[SamplePrepCanaryConfig]:
    """Resolve SamplePrep canary config from env, registry, or committed example.

    Precedence (highest wins):
    1. explicit ``path``
    2. ``METHYL_SAMPLE_PREP_CANARY_CONFIG`` standalone JSON
    3. ``sample_prep_canary`` on the resolved :class:`TestDataRegistry`
    4. committed ``tests/real_data/sample_prep_canary/registry.example.json``
    """
    explicit = path or os.environ.get(SAMPLE_PREP_CANARY_CONFIG_ENV)
    if explicit:
        p = Path(str(explicit)).expanduser()
        if p.is_file():
            raw = _load_json(p)
            if "sample_prep_canary" in raw and isinstance(raw["sample_prep_canary"], dict):
                return SamplePrepCanaryConfig.model_validate(raw["sample_prep_canary"])
            return SamplePrepCanaryConfig.model_validate(raw)

    registry = load_test_data_registry()
    if registry.sample_prep_canary is not None:
        return registry.sample_prep_canary

    example = repo_root() / "tests" / "real_data" / "sample_prep_canary" / "registry.example.json"
    if example.is_file():
        raw = _load_json(example)
        if "sample_prep_canary" in raw and isinstance(raw["sample_prep_canary"], dict):
            return SamplePrepCanaryConfig.model_validate(raw["sample_prep_canary"])
        if raw:
            return SamplePrepCanaryConfig.model_validate(raw)
    return None
