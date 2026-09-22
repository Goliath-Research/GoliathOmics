"""
Pathway name normalization: map Enrichr pathway terms to canonical biological themes
for module labeling and consistent interpretation.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Type alias for theme -> tier (High/Medium/Low) or theme -> description string
ThemeTierMap = Dict[str, str]
ThemeDescriptionsMap = Dict[str, str]

# Default mapping path (package data)
_DEFAULT_MAPPING_PATH = Path(__file__).parent / "data" / "pathway_theme_mapping.json"


def load_theme_mapping(path: Optional[Path] = None) -> Tuple[List[Tuple[str, str]], List[str]]:
    """
    Load pattern -> theme list and canonical PCa themes from JSON.
    Returns ([(pattern, theme), ...], canonical_theme_names).
    """
    p = path or _DEFAULT_MAPPING_PATH
    if not p.exists():
        logger.warning(f"Theme mapping not found at {p}; using empty mapping.")
        return [], []
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    patterns = [
        (item["pattern"].strip(), item["theme"].strip())
        for item in data.get("patterns", [])
        if "pattern" in item and "theme" in item
    ]
    canonical = data.get("canonical_pca_themes", [])
    return patterns, canonical


def load_theme_extras(path: Optional[Path] = None) -> Tuple[ThemeTierMap, ThemeDescriptionsMap]:
    """
    Load pca_relevance_tier and theme_descriptions from the mapping JSON.
    Returns (pca_relevance_tier, theme_descriptions).
    Used by module_pipeline for PCa_relevance override and Main_theme column.
    """
    p = path or _DEFAULT_MAPPING_PATH
    if not p.exists():
        return {}, {}
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    tier = data.get("pca_relevance_tier", {})
    descriptions = data.get("theme_descriptions", {})
    return tier, descriptions


class PathwayNormalizer:
    """
    Normalize pathway names (Enrichr Term) to biological themes using a curated mapping.
    First matching pattern (case-insensitive substring) wins.
    """

    def __init__(self, mapping_path: Optional[Path] = None):
        self.patterns, self.canonical_themes = load_theme_mapping(mapping_path)

    def normalize(self, term: str) -> str:
        """
        Map a pathway term to a theme. Returns the theme if any pattern matches, else the original term.
        """
        if not term or not isinstance(term, str):
            return term
        term_lower = term.lower()
        for pattern, theme in self.patterns:
            if pattern.lower() in term_lower:
                return theme
        return term

    def normalize_series(self, terms: List[str]) -> Dict[str, str]:
        """Return dict mapping each term to its normalized theme."""
        return {t: self.normalize(t) for t in terms}


def add_theme_column_to_merged(merged_df, term_column: str = "Term", theme_column: str = "theme"):
    """
    Add a theme column to a merged enrichment DataFrame.
    Requires 'Term' (or term_column) in the DataFrame.
    """
    import pandas as pd
    if term_column not in merged_df.columns:
        return merged_df
    normalizer = PathwayNormalizer()
    out = merged_df.copy()
    out[theme_column] = out[term_column].astype(str).map(normalizer.normalize)
    return out
