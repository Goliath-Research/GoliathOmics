"""Action execution primitives for workflow workers."""

from .base import CliAction, InProcessAction, build_action_from_catalog
from .detector import DETECTOR_ARGV_MAP, DetectorCliAction, merge_detector_step_override
from .dmp_select import DMP_SELECT_ARGV_MAP, DmpSelectCliAction
from .enricher import ENRICHER_ARGV_MAP, EnricherCliAction, merge_enricher_step_override
from .gene_select import GENE_SELECT_ARGV_MAP, GeneSelectCliAction
from .mapper import MAPPER_ARGV_MAP, MapperCliAction, merge_mapper_step_override
from .registry import ensure_providers_loaded, register_cli_provider

ensure_providers_loaded()

__all__ = [
    "CliAction",
    "DetectorCliAction",
    "DmpSelectCliAction",
    "EnricherCliAction",
    "GeneSelectCliAction",
    "DETECTOR_ARGV_MAP",
    "DMP_SELECT_ARGV_MAP",
    "ENRICHER_ARGV_MAP",
    "GENE_SELECT_ARGV_MAP",
    "MAPPER_ARGV_MAP",
    "MapperCliAction",
    "InProcessAction",
    "build_action_from_catalog",
    "ensure_providers_loaded",
    "merge_detector_step_override",
    "merge_enricher_step_override",
    "merge_mapper_step_override",
    "register_cli_provider",
]
