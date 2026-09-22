"""HiTIMED-style hierarchical (tree-structured) cell-type deconvolution.

Each internal node splits a parent compartment into its children with a
node-specific marker basis, solved by the same constrained projection used for
flat Houseman (:func:`methyl_deconv.core.houseman.houseman_qp`). Child masses
are multiplied down each path so the collected leaf proportions sum to 1.

The tree is analyte-driven (``analyte_trees`` in the basis JSON):

- ``buffy_coat`` -> immune/leukocyte subtree only (no tumor compartment).
- ``cfdna``      -> plasma ``tumor`` vs ``non_tumor`` top split, descending into
                    the shared immune subtree under ``non_tumor``; the tumor
                    child is a lumped leaf (``tumor_fraction``).
- ``tissue``     -> full tumor / immune / stromal tree over the immune subtree.

Bases are wheel-packaged JSON (same distribution model as the flat IDOL basis).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..config import CellDeconvRuntimeParams
from .houseman import houseman_qp

DEFAULT_HIERARCHY_BASIS = (
    Path(__file__).resolve().parent.parent / "data" / "hitimed_blood_extended_v1.json"
)


@dataclass(frozen=True)
class HierarchyNode:
    """One internal split: parent compartment -> ordered children.

    ``children`` are child compartment ids (either other node ids or terminal
    leaf ids). ``M`` is (n_markers, n_children) with columns aligned to
    ``children``; ``probe_ids`` / ``chroms`` / ``contexts`` / ``positions``
    describe the node's marker rows.
    """

    name: str
    children: Tuple[str, ...]
    probe_ids: Tuple[str, ...]
    chroms: Tuple[str, ...]
    contexts: Tuple[str, ...]
    positions: np.ndarray  # int64 (n_markers,)
    M: np.ndarray  # float64 (n_markers, n_children)


@dataclass(frozen=True)
class HierarchyBasis:
    nodes: Dict[str, HierarchyNode]
    analyte_trees: Dict[str, str]  # canonical analyte -> root node id
    provenance: Dict[str, Any]

    def root_for_analyte(self, analyte: Optional[str]) -> str:
        key = str(analyte or "").strip().lower()
        try:
            from methyl_utils.analyte_profiles import normalize_primary_analyte

            canonical = normalize_primary_analyte(key)
            if canonical:
                key = canonical
        except Exception:
            pass
        if key in self.analyte_trees:
            return self.analyte_trees[key]
        if "default" in self.analyte_trees:
            return self.analyte_trees["default"]
        raise ValueError(
            f"No hierarchy tree for analyte '{analyte}'. "
            f"Known analytes: {sorted(self.analyte_trees)}"
        )

    def is_internal(self, node_id: str) -> bool:
        return node_id in self.nodes

    def leaf_order(self, root: str) -> List[str]:
        """Deterministic depth-first leaf ordering under ``root``."""
        leaves: List[str] = []

        def _walk(node_id: str) -> None:
            node = self.nodes[node_id]
            for child in node.children:
                if self.is_internal(child):
                    _walk(child)
                else:
                    leaves.append(child)

        _walk(root)
        return leaves


def default_hierarchy_basis_path() -> Path:
    return DEFAULT_HIERARCHY_BASIS


def load_hierarchy_basis(path: Optional[str | Path] = None) -> HierarchyBasis:
    """Load a v2 hierarchical basis JSON (nodes grouped by split)."""
    p = Path(path) if path else default_hierarchy_basis_path()
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw_nodes = raw.get("nodes") or {}
    if not raw_nodes:
        raise ValueError(f"Hierarchy basis has no nodes: {p}")

    nodes: Dict[str, HierarchyNode] = {}
    for name, spec in raw_nodes.items():
        children = tuple(str(c) for c in (spec.get("children") or []))
        if not children:
            raise ValueError(f"Hierarchy node '{name}' has no children: {p}")
        markers = spec.get("markers") or []
        if not markers:
            raise ValueError(f"Hierarchy node '{name}' has no markers: {p}")
        probe_ids: List[str] = []
        chroms: List[str] = []
        contexts: List[str] = []
        positions: List[int] = []
        rows: List[List[float]] = []
        for m in markers:
            betas = m.get("betas") or {}
            missing = [c for c in children if c not in betas]
            if missing:
                raise ValueError(
                    f"Hierarchy node '{name}' marker {m.get('probe_id')} missing betas "
                    f"for children {missing}"
                )
            probe_ids.append(str(m["probe_id"]))
            chroms.append(str(m["chrom"]))
            contexts.append(str(m.get("context") or "CG").strip().upper() or "CG")
            positions.append(int(m["pos"]))
            rows.append([float(betas[c]) for c in children])
        nodes[name] = HierarchyNode(
            name=str(name),
            children=children,
            probe_ids=tuple(probe_ids),
            chroms=tuple(chroms),
            contexts=tuple(contexts),
            positions=np.asarray(positions, dtype=np.int64),
            M=np.asarray(rows, dtype=np.float64),
        )

    analyte_trees = {
        str(k).strip().lower(): str(v) for k, v in (raw.get("analyte_trees") or {}).items()
    }
    if not analyte_trees:
        raise ValueError(f"Hierarchy basis missing analyte_trees: {p}")
    for analyte, root in analyte_trees.items():
        if root not in nodes:
            raise ValueError(f"analyte_trees['{analyte}'] root '{root}' is not a node: {p}")

    provenance = {
        "hierarchy_basis_path": str(p.resolve()),
        "source_package": raw.get("source_package"),
        "citation": raw.get("citation"),
        "genome_build": raw.get("genome_build"),
        "coordinate_convention": raw.get("coordinate_convention"),
        "analyte_trees": dict(analyte_trees),
        "n_nodes": len(nodes),
    }
    return HierarchyBasis(nodes=nodes, analyte_trees=analyte_trees, provenance=provenance)


def _nodes_in_tree(basis: HierarchyBasis, root: str) -> List[str]:
    order: List[str] = []
    seen: set[str] = set()

    def _walk(node_id: str) -> None:
        if node_id in seen:
            return
        seen.add(node_id)
        order.append(node_id)
        for child in basis.nodes[node_id].children:
            if basis.is_internal(child):
                _walk(child)

    _walk(root)
    return order


def _tree_probes(basis: HierarchyBasis, root: str) -> List[Tuple[str, str, str, int]]:
    """Union of ``(probe_id, chrom, context, pos)`` across all node markers under ``root``."""
    seen: set[str] = set()
    probes: List[Tuple[str, str, str, int]] = []
    for node_id in _nodes_in_tree(basis, root):
        node = basis.nodes[node_id]
        for i, pid in enumerate(node.probe_ids):
            if pid in seen:
                continue
            seen.add(pid)
            ctx = str(node.contexts[i] if i < len(node.contexts) else "CG").strip().upper() or "CG"
            probes.append((str(pid), str(node.chroms[i]), ctx, int(node.positions[i])))
    return probes


def _resolve_h5_path(sample_dir: Path, chrom: str, ctx: str) -> Optional[Path]:
    path = sample_dir / f"{chrom}-{ctx}.h5"
    if path.is_file():
        return path
    alt = sample_dir / f"chr{chrom}-{ctx}.h5"
    return alt if alt.is_file() else None


def extract_beta_map(
    sample_dir: str | Path,
    probes: List[Tuple[str, str, str, int]],
    cfg: CellDeconvRuntimeParams,
) -> Dict[str, float]:
    """Read sample H5 per (chrom, context); return ``{probe_id: beta}`` for
    markers observed with coverage >= ``marker_min_coverage``."""
    from methyl_utils import MethylSample

    sample_dir = Path(sample_dir)
    min_coverage = int(cfg.marker_min_coverage)
    by_key: Dict[Tuple[str, str], List[Tuple[str, int]]] = {}
    for pid, chrom, ctx, pos in probes:
        by_key.setdefault((str(chrom), str(ctx).upper()), []).append((pid, int(pos)))

    beta_map: Dict[str, float] = {}

    def _fill(path: Path, entries: List[Tuple[str, int]]) -> None:
        want_pos = np.asarray([p for _, p in entries], dtype=np.uint32)
        sample = MethylSample.load_from_h5(path, positions=want_pos, align_positions=True)
        beta = np.asarray(sample.get_methylation_levels(), dtype=np.float64)
        cov = np.asarray(sample.get_coverage(), dtype=np.float64)
        pos_arr = np.asarray(sample.pos, dtype=np.uint32)
        pos_to_i = {int(p): i for i, p in enumerate(pos_arr)}
        for pid, pos in entries:
            if pid in beta_map:
                continue
            ii = pos_to_i.get(int(pos))
            if ii is None:
                continue
            if not np.isfinite(beta[ii]) or cov[ii] < min_coverage:
                continue
            beta_map[pid] = float(beta[ii])

    for (chrom, marker_ctx), entries in by_key.items():
        path = _resolve_h5_path(sample_dir, chrom, marker_ctx)
        if path is not None:
            _fill(path, entries)
        for ctx in cfg.contexts:
            ctx_u = str(ctx).strip().upper()
            if ctx_u == marker_ctx:
                continue
            path = _resolve_h5_path(sample_dir, chrom, ctx_u)
            if path is None:
                continue
            pending = [(pid, pos) for pid, pos in entries if pid not in beta_map]
            if not pending:
                break
            _fill(path, pending)
    return beta_map


def _solve_node(
    node: HierarchyNode,
    beta_map: Dict[str, float],
    cfg: CellDeconvRuntimeParams,
    *,
    use_gpu: Optional[bool],
) -> Tuple[Optional[np.ndarray], int, int]:
    """QP for one node against observed markers.

    Returns ``(omega|None, n_observed, n_total)``. ``None`` omega means the node
    could not be solved (insufficient markers); caller applies an even split.
    """
    n_total = node.M.shape[0]
    obs_idx = [i for i, pid in enumerate(node.probe_ids) if pid in beta_map]
    n_obs = len(obs_idx)
    n_children = len(node.children)
    if n_total == 0 or n_obs < n_children or (n_obs / n_total) < float(cfg.min_marker_fraction):
        return None, n_obs, n_total
    rows = np.asarray(obs_idx, dtype=np.int64)
    M_obs = node.M[rows, :]
    y_obs = np.asarray([beta_map[node.probe_ids[i]] for i in obs_idx], dtype=np.float64)
    omega = houseman_qp(M_obs, y_obs, use_gpu=use_gpu)
    return omega, n_obs, n_total


def hitimed_deconvolve(
    beta_map: Dict[str, float],
    basis: HierarchyBasis,
    root: str,
    cfg: CellDeconvRuntimeParams,
) -> Dict[str, Any]:
    """Recursively solve the tree from ``root``; return leaf proportions + status.

    Leaf masses are accumulated products of node QP weights along each path and
    renormalized to sum to 1. If a node cannot be solved, its parent mass is
    split evenly across that node's children and the result is flagged
    ``partial``.
    """
    leaves: Dict[str, float] = {leaf: 0.0 for leaf in basis.leaf_order(root)}
    status = {"partial": False, "solved_nodes": 0, "fallback_nodes": 0}

    def _descend(node_id: str, mass: float) -> None:
        node = basis.nodes[node_id]
        omega, _n_obs, _n_total = _solve_node(node, beta_map, cfg, use_gpu=cfg.use_gpu)
        if omega is None:
            status["partial"] = True
            status["fallback_nodes"] += 1
            share = mass / float(len(node.children)) if node.children else 0.0
            weights = [share] * len(node.children)
        else:
            status["solved_nodes"] += 1
            weights = [float(mass * w) for w in omega]
        for child, child_mass in zip(node.children, weights):
            if basis.is_internal(child):
                _descend(child, child_mass)
            else:
                leaves[child] = leaves.get(child, 0.0) + child_mass

    # Root cannot be solved at all -> insufficient.
    root_omega, root_obs, root_total = _solve_node(
        basis.nodes[root], beta_map, cfg, use_gpu=cfg.use_gpu
    )
    total_probes = len(_tree_probes(basis, root))
    n_observed = int(sum(1 for _ in beta_map))
    if root_omega is None and root_obs < len(basis.nodes[root].children):
        row: Dict[str, Any] = {leaf: float("nan") for leaf in leaves}
        row["n_markers_observed"] = n_observed
        row["n_markers_total"] = total_probes
        row["marker_fraction"] = (n_observed / total_probes) if total_probes else 0.0
        row["qp_status"] = "insufficient_markers"
        return row

    _descend(root, 1.0)
    total = float(sum(leaves.values()))
    if total > 0.0:
        for leaf in leaves:
            leaves[leaf] = leaves[leaf] / total

    row = {leaf: float(val) for leaf, val in leaves.items()}
    row["n_markers_observed"] = n_observed
    row["n_markers_total"] = total_probes
    row["marker_fraction"] = (n_observed / total_probes) if total_probes else 0.0
    row["qp_status"] = "partial" if status["partial"] else "ok"
    return row


def deconvolve_sample_hierarchical(
    sample_dir: str | Path,
    basis: HierarchyBasis,
    root: str,
    cfg: CellDeconvRuntimeParams,
) -> Dict[str, Any]:
    """Estimate hierarchical leaf proportions for one sample."""
    probes = _tree_probes(basis, root)
    beta_map = extract_beta_map(sample_dir, probes, cfg)
    return hitimed_deconvolve(beta_map, basis, root, cfg)
