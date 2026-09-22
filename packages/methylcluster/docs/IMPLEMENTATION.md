# Centroid-Based Clustering Implementation

> **ARCHIVED (2026-06):** `methylcluster` is deprecated. See [Architecture: pipeline stages](../../../docs/architecture/pipeline-stages.md) for the canonical interpretation path (mapper → enricher).

> Legacy note: this document is retained for historical/technical reference only.

## Overview

Implemented a new centroid-based clustering method for MethylCluster that uses `MethylSample` instances as dynamic cluster centroids with Beta distribution log-likelihood for probabilistic sample assignment.

## Key Features

1. **Dynamic Centroids**: Each cluster maintains a centroid as a `MethylSample` instance that automatically recalculates when samples are added or removed via `PositionAligner`.

2. **Probabilistic Assignment**: Uses Beta distribution log-likelihood to assign samples to centroids, providing statistically rigorous cluster membership.

3. **EM-like Algorithm**: Iteratively refines clusters through:
   - **E-step**: Assign each sample to the centroid with highest log-likelihood
   - **M-step**: Update centroids by adding/removing samples (automatic via PositionAligner)

4. **Farthest-Point Initialization**: Selects initial centroids using the farthest-point heuristic for maximum separation.

## Implementation Details

### New Files

#### `packages/methylcluster/methyl_cluster/centroid_manager.py`

New module containing the `ClusterCentroid` class:

```python
class ClusterCentroid:
    """Manages a single cluster centroid with add/remove operations."""
    
    def __init__(self, cluster_id, chrom, ctx, min_coverage=4, use_gpu=True, max_samples=1000)
    def add_sample(self, sample_idx, sample, sample_path) -> bool
    def remove_sample(self, sample_idx, sample) -> bool
    def get_centroid(self) -> MethylSample
    def compute_log_likelihood(self, sample) -> float
```

**Key Methods**:
- `add_sample()`: Adds a sample to the cluster and auto-recalculates centroid
- `remove_sample()`: Removes a sample and auto-recalculates centroid
- `compute_log_likelihood()`: Computes Beta distribution log-likelihood for sample assignment
- `_ensure_cpu_sample()`: Converts GPU arrays to CPU for PositionAligner compatibility

### Modified Files

#### `packages/methylcluster/methyl_cluster/config.py`

Added new clustering method and parameters:

```python
class ClusteringMethod(str, Enum):
    HDBSCAN = "hdbscan"
    HIERARCHICAL = "hierarchical"
    CENTROID = "centroid"  # NEW

# New parameters
max_em_iterations: int = 50
convergence_threshold: float = 0.01
```

#### `packages/methylcluster/methyl_cluster/cluster.py`

Added new methods for centroid-based clustering:

1. **`_load_all_samples()`**: Returns already-loaded samples
2. **`_centroid_based_clustering(k)`**: Main EM algorithm
3. **`_centroid_based_clustering_auto_k()`**: Automatic K selection with silhouette analysis
4. **`_initialize_centroids_farthest(samples, k)`**: Farthest-point initialization
5. **`_assign_samples_to_centroids(samples, centroids)`**: E-step assignment
6. **`_update_centroids(samples, assignments, centroids)`**: M-step update
7. **`_compute_distance_matrix_from_samples(samples)`**: Distance matrix for initialization
8. **`_compile_centroid_results(centroids)`**: Results compilation

Updated `cluster()` method to dispatch to centroid-based clustering when `clustering_method="centroid"`.

## Algorithm Flow

```
1. Load samples as MethylSample instances (already done by run())
2. Initialize K centroids:
   a. Compute/load distance matrix
   b. Select K farthest samples as initial centroids
   c. Create ClusterCentroid for each, add initial sample
3. EM Iteration:
   a. E-step: For each sample, compute log-likelihood to each centroid
   b. Assign sample to centroid with highest log-likelihood
   c. Check convergence (fraction of samples changing clusters)
   d. M-step: Update centroids by adding/removing samples
   e. Repeat until convergence or max iterations
4. Compile and save results
```

## Log-Likelihood Computation

Uses Beta distribution to model methylation patterns:

```python
def compute_log_likelihood(sample, centroid):
    # 1. Find common positions between sample and centroid
    common_pos = intersect(sample.pos, centroid.pos)
    
    # 2. Get sample methylation levels
    sample_meth = sample.mC / (sample.mC + sample.uC)
    
    # 3. Get centroid Beta parameters (α, β)
    centroid_alpha, centroid_beta = centroid._compute_beta_parameters()
    
    # 4. Compute log P(sample_meth | α, β) for each position
    log_probs = beta_log_pdf(sample_meth, centroid_alpha, centroid_beta)
    
    # 5. Sum log-likelihoods across all positions
    return sum(log_probs) / len(common_pos)  # Normalized
```

## Configuration Example

```json
{
  "clustering_method": "centroid",
  "force_k": 2,
  "max_em_iterations": 50,
  "convergence_threshold": 0.01,
  "metric": "jensen_shannon",
  "use_gpu": true
}
```

## Test Results

Tested on `cluster-pb-hc1_config.json` (47 samples, 2 cohorts):

- **Method**: Centroid-based clustering
- **K**: 2 (forced)
- **EM Iterations**: 2 (converged)
- **Results**:
  - Cluster 0: 1 sample (AN00026368)
  - Cluster 1: 46 samples (34 AN00026368, 12 AN00026418)

The algorithm correctly identified one outlier sample and grouped the remaining samples.

## Technical Notes

### GPU/CPU Handling

- **PositionAligner**: Uses CPU-only mode (`use_gpu=False`) to avoid GPU/CPU conversion issues
- **Distance Computation**: Uses GPU acceleration when available
- **Sample Conversion**: `_ensure_cpu_sample()` converts CuPy arrays to NumPy for PositionAligner

### Performance

- **Initialization**: O(n²) for distance matrix (cached)
- **E-step**: O(n × k × m) where n=samples, k=clusters, m=avg positions
- **M-step**: O(n × m) for centroid updates
- **Convergence**: Typically 2-10 iterations

### Advantages over Distance-Matrix Methods

1. **Dynamic Centroids**: Centroids update as samples move between clusters
2. **Statistical Rigor**: Uses proper Beta distribution probabilities
3. **Interpretable**: Centroids are actual MethylSample objects that can be saved/analyzed
4. **Memory Efficient**: PositionAligner handles sparse data efficiently
5. **GPU Accelerated**: Uses existing methyl_utils GPU support for distance computations

## Future Enhancements

1. **Automatic K Selection**: Implement silhouette analysis for optimal K
2. **Convergence Diagnostics**: Track log-likelihood changes across iterations
3. **Centroid Export**: Save final centroids as H5 files for downstream analysis
4. **Parallel E-step**: Parallelize log-likelihood computations across samples
5. **Adaptive Convergence**: Use log-likelihood change instead of assignment change

## References

- MethylCentroid implementation for centroid management patterns
- ProbabilisticBetaClassifier for Beta distribution log-likelihood
- PositionAligner for dynamic centroid updates

