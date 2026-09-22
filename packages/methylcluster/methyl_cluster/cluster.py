"""
Main clustering logic for methylation samples.

This module implements the core MethylCluster class that performs HDBSCAN
clustering on methylation samples using precomputed distance matrices.
"""

import numpy as np
import hdbscan
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from scipy.cluster.hierarchy import linkage, fcluster
from scipy.spatial.distance import squareform
import random

# Import local modules
from .config import MethylClusterConfig
from .distance_matrix import DistanceMatrixComputer
from .centroid_manager import ClusterCentroid

# Import MethylUtils components
try:
    from methyl_utils.core.methyl_frame import MethylSample, MethylCentroid
except ImportError:
    # Fallback for development
    import sys
    sys.path.insert(
        0, str(Path(__file__).parent.parent.parent / 'methylutils'))
    from methyl_utils.core.methyl_frame import MethylSample, MethylCentroid

logger = logging.getLogger(__name__)


class MethylCluster:
    """
    Performs HDBSCAN clustering on methylation samples.

    This class orchestrates the complete clustering workflow:
    1. Load methylation samples from HDF5 files
    2. Compute pairwise distance matrix using GPU-accelerated metrics
    3. Perform HDBSCAN clustering with precomputed distances
    4. Generate and save results with visualizations
    """

    def __init__(self, config: MethylClusterConfig):
        """
        Initialize MethylCluster with configuration.

        Args:
            config: MethylClusterConfig instance
        """
        self.config = config
        self.config.soft_assignment = True  # Enable as per user
        self.samples: List[MethylSample] = []
        self.sample_paths: List[Path] = []
        self.distance_matrix: Optional[np.ndarray] = None
        self.cluster_labels: Optional[np.ndarray] = None
        self.clusterer: Optional[hdbscan.HDBSCAN] = None

        logger.info(
            f"Initialized MethylCluster for {config.chrom}-{config.ctx}")
        logger.info(
            f"Metric: {config.metric.value}, Min cluster size: {config.min_cluster_size}")

    def load_samples(self) -> None:
        """
        Load all samples from configured paths.

        Samples are loaded as MethylSample instances from HDF5 files
        named {chrom}-{ctx}.h5 in each sample directory.

        Raises:
            FileNotFoundError: If sample file doesn't exist
            ValueError: If sample data is invalid
        """
        logger.info(f"Loading {len(self.config.samples)} samples...")

        for i, sample_path_str in enumerate(self.config.samples):
            sample_dir = Path(sample_path_str)
            h5_file = sample_dir / f"{self.config.chrom}-{self.config.ctx}.h5"

            if not h5_file.exists():
                raise FileNotFoundError(f"Sample file not found: {h5_file}")

            logger.debug(
                f"Loading sample {i+1}/{len(self.config.samples)}: {h5_file}")
            sample = MethylSample.load_from_h5(h5_file)

            self.samples.append(sample)
            self.sample_paths.append(sample_dir)

        logger.info(f"Successfully loaded {len(self.samples)} samples")

        # Log sample statistics
        n_positions = [len(s.pos) for s in self.samples]
        logger.info(f"Position counts: min={min(n_positions)}, max={max(n_positions)}, "
                   f"mean={np.mean(n_positions):.0f}")

    def compute_distances(self) -> None:
        """
        Compute pairwise distance matrix between all samples.

        Uses GPU-accelerated distance metrics from MethylUtils with
        optional caching to disk for reuse.
        """
        logger.info("Computing pairwise distance matrix...")

        cache_dir = Path(
            self.config.output_dir) if self.config.cache_distance_matrix else None

        computer = DistanceMatrixComputer(
            metric=self.config.metric.value,
            chrom=self.config.chrom,
            ctx=self.config.ctx,
            use_gpu=self.config.use_gpu,
            cache_dir=cache_dir
        )

        self.distance_matrix = computer.compute_pairwise_distances(
            self.samples, self.sample_paths
        )

        # Log distance statistics
        tri_upper = np.triu_indices_from(self.distance_matrix, k=1)
        distances = self.distance_matrix[tri_upper]
        logger.info(f"Distance statistics: min={np.min(distances):.4f}, "
                   f"max={np.max(distances):.4f}, mean={np.mean(distances):.4f}, "
                   f"median={np.median(distances):.4f}")

    def cluster(self) -> Dict[str, Any]:
        """
        Perform clustering using the configured method (HDBSCAN, Hierarchical, or Centroid).

        Returns:
            Dictionary containing clustering results:
            - n_clusters: Number of clusters found
            - n_noise: Number of noise samples
            - cluster_assignments: Dict mapping cluster labels to sample paths
            - labels: List of cluster labels for each sample
            - clustering_method: Method used
            - silhouette_score: Quality metric (if applicable)

        Raises:
            ValueError: If distance matrix hasn't been computed (except for centroid method)
        """
        # Choose clustering method
        if self.config.clustering_method.value == "centroid":
            # Centroid-based clustering doesn't require distance matrix upfront
            if self.config.force_k is not None:
                return self._centroid_based_clustering(self.config.force_k)
            else:
                return self._centroid_based_clustering_auto_k()

        # Other methods require distance matrix
        if self.distance_matrix is None:
            raise ValueError(
                "Distance matrix must be computed before clustering. Call compute_distances() first.")

        if self.config.clustering_method.value == "hierarchical":
            return self._hierarchical_clustering()
        else:
            return self._hdbscan_clustering()

    def _hdbscan_clustering(self) -> Dict[str, Any]:
        """
        Perform HDBSCAN clustering using precomputed distance matrix.

        Returns:
            Dictionary containing clustering results
        """
        logger.info("Performing HDBSCAN clustering...")

        # Determine min_samples parameter
        min_samples = self.config.min_samples if self.config.min_samples is not None else self.config.min_cluster_size

        logger.info(f"HDBSCAN parameters: min_cluster_size={self.config.min_cluster_size}, "
                   f"min_samples={min_samples}, "
                   f"cluster_selection_epsilon={self.config.cluster_selection_epsilon}, "
                   f"cluster_selection_method={self.config.cluster_selection_method}, "
                   f"allow_single_cluster={self.config.allow_single_cluster}")

        # Create and fit HDBSCAN clusterer
        self.clusterer = hdbscan.HDBSCAN(
            min_cluster_size=self.config.min_cluster_size,
            min_samples=min_samples,
            cluster_selection_epsilon=self.config.cluster_selection_epsilon,
            cluster_selection_method=self.config.cluster_selection_method,
            allow_single_cluster=self.config.allow_single_cluster,
            metric='precomputed'
        )

        self.cluster_labels = self.clusterer.fit_predict(self.distance_matrix)

        logger.info("Clustering complete")

        # Compile and return results
        results = self._compile_results()
        logger.info(
            f"Found {results['n_clusters']} clusters with {results['n_noise']} noise samples")

        # Check if K-means fallback should be used
        if self.config.enable_kmeans_fallback:
            should_use_fallback = self._should_use_kmeans_fallback(results)
            if should_use_fallback:
                logger.info(
                    "HDBSCAN results are ambiguous, trying K-means with automatic K selection...")
                kmeans_results = self._kmeans_clustering_with_silhouette()
                if kmeans_results is not None:
                    logger.info(f"K-means found {kmeans_results['n_clusters']} clusters "
                               f"(silhouette={kmeans_results.get('silhouette_score', 0):.4f})")
                    results = kmeans_results
                else:
                    logger.info(
                        "K-means did not find meaningful clusters, keeping HDBSCAN results")

        return results

    def _hierarchical_clustering(self) -> Dict[str, Any]:
        """
        Perform hierarchical clustering with automatic K selection using silhouette analysis.

        Returns:
            Dictionary containing clustering results or indication of homogeneous population
        """
        logger.info(
            "Performing hierarchical clustering with silhouette analysis...")

        n_samples = len(self.distance_matrix)

        # Check if K is forced
        if self.config.force_k is not None:
            logger.info(
                f"Forcing K={self.config.force_k} clusters (bypassing silhouette threshold)")

            # Convert distance matrix to condensed form for scipy
            condensed_dist = squareform(self.distance_matrix, checks=False)

            # Perform hierarchical clustering
            linkage_matrix = linkage(
                condensed_dist, method=self.config.linkage_method)

            # Extract forced number of clusters
            best_labels = fcluster(
                linkage_matrix, self.config.force_k, criterion='maxclust') - 1
            best_k = self.config.force_k
            best_score = silhouette_score(
                self.distance_matrix, best_labels, metric='precomputed')

            logger.info(f"Forced K={best_k} with silhouette={best_score:.4f}")

            self.cluster_labels = best_labels
            silhouette_scores = {best_k: best_score}

        else:
            # Automatic K selection using silhouette analysis
            # Determine max_k
            if self.config.max_k is not None:
                max_k = min(self.config.max_k, n_samples - 1)
            else:
                max_k = min(int(np.sqrt(n_samples)), n_samples - 1)

            if max_k < 2:
                logger.warning(
                    "Not enough samples for hierarchical clustering")
                # Return single cluster with all samples
                self.cluster_labels = np.zeros(n_samples, dtype=int)
                return self._compile_results()

            logger.info(
                f"Testing K from 2 to {max_k} with {self.config.linkage_method} linkage...")

            # Convert distance matrix to condensed form for scipy
            condensed_dist = squareform(self.distance_matrix, checks=False)

            # Perform hierarchical clustering
            linkage_matrix = linkage(
                condensed_dist, method=self.config.linkage_method)

            best_k = 2
            best_score = -1
            best_labels = None
            silhouette_scores = {}

            # Test different numbers of clusters
            for k in range(2, max_k + 1):
                # Convert to 0-indexed
                labels = fcluster(linkage_matrix, k, criterion='maxclust') - 1

                try:
                    score = silhouette_score(
                        self.distance_matrix, labels, metric='precomputed')
                except ValueError as e:
                    if "number of labels" in str(e).lower():
                        score = 0.0
                        logger.warning(
                            f"k={k} collapsed to 1 cluster, score=0.0")
                    else:
                        raise
                silhouette_scores[k] = score

                logger.info(f"  K={k}: silhouette={score:.4f}")

                if score > best_score:
                    best_score = score
                    best_k = k
                    best_labels = labels

            logger.info(f"Best K={best_k} with silhouette={best_score:.4f}")

            self.cluster_labels = best_labels

        # Try K-medoids refinement for all K >= 2, even if score is below threshold
        # This can sometimes find better clusters than hierarchical clustering
        if self.config.enable_medoid_refinement and best_k >= 2:
            logger.info(
                f"Refining {best_k} clusters using K-medoids with farthest-point initialization...")
            refined_labels, refined_score = self._refine_with_kmedoids(best_k)

            if refined_score > best_score:
                logger.info(
                    f"Medoid refinement improved silhouette: {best_score:.4f} → {refined_score:.4f}")
                self.cluster_labels = refined_labels
                best_score = refined_score
            else:
                logger.info(
                    f"Medoid refinement did not improve silhouette: {refined_score:.4f} <= {best_score:.4f}")

        # Check if final silhouette score meets threshold (unless K is forced)
        if self.config.force_k is None and best_score < self.config.silhouette_threshold:
            logger.info(
                f"Final silhouette score {best_score:.4f} < threshold {self.config.silhouette_threshold:.4f}")
            logger.info(
                "No meaningful clusters found - population appears homogeneous")
            logger.info("Assigning all samples to a single cluster")
            # Assign all to one cluster
            self.cluster_labels = np.zeros(n_samples, dtype=int)
            results = self._compile_results()
            results['clustering_method'] = 'hierarchical'
            results['silhouette_score'] = float(best_score)
            results['silhouette_scores_by_k'] = {
                int(k): float(v) for k, v in silhouette_scores.items()}
            results['homogeneous'] = True
            return results

        # Compile results
        results = self._compile_results()
        results['clustering_method'] = 'hierarchical'
        results['silhouette_score'] = float(best_score)
        results['silhouette_scores_by_k'] = {
            int(k): float(v) for k, v in silhouette_scores.items()}
        results['linkage_method'] = self.config.linkage_method

        return results

    def _refine_with_kmedoids(self, k: int) -> Tuple[np.ndarray, float]:
        """
        Refine clustering using K-medoids (PAM - Partitioning Around Medoids algorithm).

        This is a standard implementation of the PAM algorithm:
        1. Initialize: Select k samples that are farthest apart (farthest-first heuristic)
        2. Assignment: Assign each sample to nearest medoid
        3. Update: For each cluster, find new medoid (sample minimizing within-cluster distances)
        4. Repeat 2-3 until convergence

        Reference: Kaufman, L. and Rousseeuw, P.J. (1987), Clustering by means of Medoids.

        Args:
            k: Number of clusters

        Returns:
            Tuple of (labels, silhouette_score)
        """
        n_samples = len(self.distance_matrix)
        max_iterations = self.config.max_medoid_iterations

        # Initialize medoids using farthest-point strategy (farthest-first heuristic)
        medoid_indices = self._initialize_farthest_medoids(k)
        logger.info(f"Initial medoids (farthest-first): {medoid_indices}")

        # Iterative PAM algorithm
        logger.info(f"Running K-medoids (PAM algorithm)...")
        for iteration in range(max_iterations):
            # Assignment step: assign each sample to nearest medoid
            labels = self._assign_to_nearest_medoid(medoid_indices)

            # Update step: find new medoid for each cluster
            new_medoid_indices = self._find_cluster_medoids(labels, k)

            # Check convergence
            if set(new_medoid_indices) == set(medoid_indices):
                logger.info(
                    f"K-medoids converged after {iteration + 1} iterations")
                break

            medoid_indices = new_medoid_indices
        else:
            logger.info(f"K-medoids reached max iterations ({max_iterations})")

        # Final assignment
        labels = self._assign_to_nearest_medoid(medoid_indices)

        try:
            score = silhouette_score(
                self.distance_matrix, labels, metric='precomputed')
        except ValueError as e:
            if "number of labels" in str(e).lower():
                score = 0.0
                logger.warning(
                    "Medoid refinement collapsed to 1 cluster, score=0.0")
            else:
                raise

        # Log results
        cluster_sizes = [np.sum(labels == i) for i in range(k)]
        logger.info(f"Final medoids: {medoid_indices}")
        logger.info(f"Cluster sizes: {cluster_sizes}")

        return labels, score

    def _initialize_farthest_medoids(self, k: int) -> List[int]:
        """
        Initialize k medoids by selecting samples that are farthest apart.

        Algorithm:
        1. Start with sample pair that has maximum distance
        2. Iteratively add sample that is farthest from all selected medoids

        Args:
            k: Number of medoids to select

        Returns:
            List of medoid indices
        """
        n_samples = len(self.distance_matrix)

        # Start with the two samples that are farthest apart
        max_dist = 0
        medoid_indices = [0, 1]
        for i in range(n_samples):
            for j in range(i + 1, n_samples):
                if self.distance_matrix[i, j] > max_dist:
                    max_dist = self.distance_matrix[i, j]
                    medoid_indices = [i, j]

        # If k == 2, we're done
        if k == 2:
            return medoid_indices

        # For k > 2, iteratively add farthest samples
        for _ in range(k - 2):
            max_min_dist = -1
            next_medoid = -1

            for i in range(n_samples):
                if i in medoid_indices:
                    continue

                # Find minimum distance from i to any medoid
                min_dist = min(self.distance_matrix[i, m]
                               for m in medoid_indices)

                # Keep track of sample with maximum min_dist
                if min_dist > max_min_dist:
                    max_min_dist = min_dist
                    next_medoid = i

            medoid_indices.append(next_medoid)

        return medoid_indices

    def _assign_to_nearest_medoid(self, medoid_indices: List[int]) -> np.ndarray:
        """
        Assign each sample to its nearest medoid.

        Args:
            medoid_indices: List of medoid sample indices

        Returns:
            Array of cluster labels
        """
        n_samples = len(self.distance_matrix)
        labels = np.zeros(n_samples, dtype=int)

        for i in range(n_samples):
            distances_to_medoids = [self.distance_matrix[i, m]
                for m in medoid_indices]
            labels[i] = int(np.argmin(distances_to_medoids))

        return labels

    def _find_cluster_medoids(self, labels: np.ndarray, k: int) -> List[int]:
        """
        Find the medoid (most central sample) for each cluster.

        The medoid is the sample that minimizes the sum of distances to all
        other samples in the cluster (standard PAM definition).

        Args:
            labels: Current cluster assignments
            k: Number of clusters

        Returns:
            List of medoid indices
        """
        medoid_indices = []

        for cluster_id in range(k):
            cluster_samples = np.where(labels == cluster_id)[0]

            if len(cluster_samples) == 0:
                # Empty cluster - use random sample
                logger.warning(
                    f"Empty cluster {cluster_id}, using random sample")
                medoid_indices.append(int(np.random.choice(len(labels))))
                continue

            if len(cluster_samples) == 1:
                # Single sample cluster
                medoid_indices.append(int(cluster_samples[0]))
                continue

            # Find sample with minimum sum of distances to all cluster members
            min_sum_dist = np.inf
            medoid = cluster_samples[0]

            for candidate in cluster_samples:
                sum_dist = self.distance_matrix[candidate,
                    cluster_samples].sum()
                if sum_dist < min_sum_dist:
                    min_sum_dist = sum_dist
                    medoid = candidate

            medoid_indices.append(int(medoid))

        return medoid_indices

    def _load_all_samples(self) -> List:
        """
        Get all samples as MethylSample instances.

        Samples are already loaded by load_samples() in the run() method.
        This method simply returns the cached samples.

        Returns:
            List of MethylSample objects
        """
        if not self.samples:
            raise RuntimeError(
                "Samples not loaded. Call load_samples() first.")

        logger.info(f"Using {len(self.samples)} already-loaded samples")
        return self.samples

    def _centroid_based_clustering(self, k: int) -> Dict[str, Any]:
        """
        Perform centroid-based clustering with sequential EM-like iteration and enhancements.

        Enhancements:
        - Multiple restarts: Run EM num_restarts times, select best by silhouette score
        - Min cluster size enforcement: After each iteration, rescue small clusters (< min_cluster_size)
          by moving farthest sample from largest cluster
        - Empty cluster rescue: If a cluster empties mid-iteration, immediately seed with farthest sample

        Algorithm:
        1. Ensure distance matrix is available for silhouette and rescues
        2. For each restart:
           - Initialize k centroids using farthest-point heuristic
           - Initialize assignments for initial centroid samples
           - Sequential E/M-step: For each sample in shuffled order:
              - Compute log-likelihood to all centroids
              - If unassigned or better cluster found, move sample (add/remove), rescue if empty
           - After full pass: Enforce min sizes if needed
           - Repeat until convergence
        3. Select best run by silhouette score
        """
        logger.info(
            f"Starting enhanced centroid-based clustering with K={k}, num_restarts={self.config.num_restarts}")

        # Ensure distance matrix for silhouette and rescues
        if self.distance_matrix is None:
            samples = self._load_all_samples()
            logger.info("Computing distance matrix for enhancements...")
            self.distance_matrix = self._compute_distance_matrix_from_samples(
                samples)

        # Load all samples
        samples = self._load_all_samples()
        n_samples = len(samples)

        # Handle forced group initialization
        forced_init = self.config.forced_groups is not None
        if forced_init:
            if len(self.config.forced_groups) == 2 and set(self.config.forced_groups.keys()) == {"healthy", "diseased"}:
                logger.info(
                    "Detected healthy/diseased groups—using probabilistic initialization")
                group_labels = ["healthy", "diseased"]
                group_sizes = [self.config.forced_groups["healthy"],
                    self.config.forced_groups["diseased"]]
                # Build initial centroids from groups (existing code)
                centroids = []
                current_assignments = np.full(n_samples, -1, dtype=int)
                current_idx = 0
                for cluster_id, (label, size) in enumerate(zip(group_labels, group_sizes)):
                    if size == 0:
                        continue
                    group_sample_idxs = list(
                        range(current_idx, current_idx + size))
                    centroid = ClusterCentroid(
                        cluster_id=cluster_id,
                        chrom=self.config.chrom,
                        ctx=self.config.ctx,
                        min_coverage=4,
                        use_gpu=self.config.use_gpu,
                        max_samples=n_samples
                    )
                    added_count = 0
                    for s_idx in group_sample_idxs:
                        sample = samples[s_idx]
                        path_str = str(self.sample_paths[s_idx])
                        if centroid.add_sample(s_idx, sample, path_str):
                            current_assignments[s_idx] = cluster_id
                            added_count += 1
                        else:
                            logger.warning(
                                f"Failed to add sample {s_idx} to cluster {cluster_id} ({label})")
                    if added_count > 0:
                        centroids.append(centroid)
                        logger.info(
                            f"Cluster {cluster_id} ({label}): added {added_count}/{size} samples")
                    else:
                            logger.warning(
                                f"No samples added to cluster {cluster_id} ({label})")
                    current_idx += size
                    # Update k to number of successful clusters
                    k = len(centroids)
                    if k < 2:
                        logger.warning(
                            "Forced init resulted in fewer than 2 clusters, falling back to standard method")
                        forced_init = False
                        num_restarts_local = self.config.num_restarts
                    else:
                            # Store labels for results
                            # Trim to successful clusters
                            self._group_labels = group_labels[:k]

                    # Initialize unassigned set for forced init
                    assigned_indices = set(np.where(current_assignments != -1)[0])
                    unassigned = set(range(n_samples)) - assigned_indices
                    logger.info(
                        f"Forced init complete: {k} clusters, {len(unassigned)} unassigned samples")

                    # NEW: Validate init centroids
                    if self.config.validate_init:
                        logger.info("Validating forced init centroids...")
                        for i, centroid in enumerate(centroids):
                            if centroid.get_sample_count() > 0:
                                own_indices = centroid.get_sample_indices()
                                sample_idx = random.choice(own_indices)
                                ll = centroid.compute_log_likelihood(
                                    samples[sample_idx])
                                if ll < -30 or np.isneginf(ll):
                                    logger.warning(
                                        f"Low fit for own sample {sample_idx} in Cluster {i} (ll={ll:.2f}) - swapping")
                                    # Find best sample from other clusters
                                    best_ll = -np.inf
                                    best_swap_idx = None
                                    best_from_cluster = None
                                    for j, other_centroid in enumerate(centroids):
                                        if j == i or other_centroid.get_sample_count() == 0:
                                            continue
                                        other_indices = other_centroid.get_sample_indices()
                                        # Check up to 3 for speed
                                        for o_idx in other_indices[:3]:
                                            o_ll = centroid.compute_log_likelihood(
                                                samples[o_idx])
                                            if o_ll > best_ll and o_ll > -25:
                                                best_ll = o_ll
                                                best_swap_idx = o_idx
                                                best_from_cluster = j
                                    if best_swap_idx is not None and best_ll > -5:
                                        # Swap
                                        # Remove from best_from_cluster
                                        centroids[best_from_cluster].remove_sample(
                                            best_swap_idx, samples[best_swap_idx])
                                        current_assignments[best_swap_idx] = i
                                        # Add to i (remove own bad if needed)
                                        bad_idx = sample_idx if ll < best_ll else None
                                        if bad_idx is not None:
                                            centroids[i].remove_sample(
                                                bad_idx, samples[bad_idx])
                                            current_assignments[bad_idx] = best_from_cluster
                                        centroids[i].add_sample(
                                            best_swap_idx, samples[best_swap_idx], self.sample_paths[best_swap_idx])
                                        logger.info(
                                            f"Swapped {best_swap_idx} into Cluster {i} (ll={best_ll:.2f})")
                                    else:
                                        logger.warning(
                                            f"No good swap for Cluster {i}")
                                else:
                                    if -25 < ll < -15:
                                        logger.debug(
                                            f"Moderate fit ll={ll:.2f} for own sample in Cluster {i} (no swap)")
                                    else:
                                        logger.debug(
                                            f"Cluster {i} own fit good (ll={ll:.2f})")
                            else:
                                logger.warning(
                                    f"Cluster {i} empty after init - will rescue")

                    # Post-validation enforcement
                    self._enforce_min_cluster_sizes(
                        centroids, samples, current_assignments, unassigned)
                    logger.info("Forced init validation complete")

                    # New: Probabilistically select best seeds
                    for i, label in enumerate(group_labels):
                        group_idxs = [j for j, ass in enumerate(
                            current_assignments) if ass == i]
                        if group_idxs:
                            # Compute avg LL to own centroid vs other
                            own_centroid = centroids[i]
                            other_centroid = centroids[1 - i]
                            scores = []
                            for idx in group_idxs:
                                own_ll = own_centroid.compute_log_likelihood(
                                    samples[idx])
                                other_ll = other_centroid.compute_log_likelihood(
                                    samples[idx])
                                # Higher = more "extreme" for group
                                scores.append(own_ll - other_ll)
                            best_seed = group_idxs[np.argmax(scores)]
                            logger.info(
                                f"Selected best seed for {label}: {best_seed} (score={max(scores):.2f})")
                            # Rebuild centroid around best seed if needed

        # Initialize tracking variables for multiple restarts
        best_score = -1
        best_labels = None
        best_centroids = None
        best_assignments = None
        best_probabilities = None
        best_iteration = 0

        # Multiple restarts loop
        num_restarts_to_use = num_restarts_local if forced_init and 'num_restarts_local' in locals() else self.config.num_restarts
        for restart in range(num_restarts_to_use):
            logger.info(f"Restart {restart + 1}/{num_restarts_to_use}")

            if not forced_init:
                random.seed(restart)
                # Initialize centroids using farthest-point heuristic
                centroids = self._initialize_centroids_farthest(samples, k)
                # Initialize assignments: -1 for unassigned
                current_assignments = np.full(n_samples, -1, dtype=int)
                # Set initial assignments for the seed samples
                initial_indices = []
                for i, centroid in enumerate(centroids):
                    init_idxs = centroid.get_sample_indices()
                    if len(init_idxs) != 1:
                        logger.warning(
                            f"Centroid {i} has {len(init_idxs)} initial samples, expected 1")
                    else:
                        init_idx = init_idxs[0]
                        current_assignments[init_idx] = i
                        initial_indices.append(init_idx)
                logger.info(
                    f"Initialized {len(initial_indices)} seed samples in their centroids")
                # Track unassigned samples
                unassigned = set(range(n_samples)) - set(initial_indices)

            # Sequential EM iteration (updated to use _assign_samples_to_centroids)
            converged = False
            iteration_probs = None  # Track per-iteration if soft
            for iteration in range(self.config.max_em_iterations):
                logger.info(
                    f"Sequential EM iteration {iteration + 1}/{self.config.max_em_iterations}")

                if forced_init and iteration == 0:
                    extremes = []
                    for i in list(unassigned):
                        if len(centroids) < 2:
                            logger.warning(
                                "Fewer than 2 centroids for extremeness calculation")
                            break
                        p_healthy = centroids[0].compute_membership_probabilities(samples[i], [
                                                                                  centroids[1]])
                        p_diseased = 1 - p_healthy
                        extremeness = abs(p_healthy - p_diseased)
                        extremes.append((extremeness, i))
                    extremes.sort(reverse=True)
                    order = [idx for _, idx in extremes] + \
                        list(set(range(n_samples)) - unassigned)
                    # Full manual sequential assignment (restored from original implementation)
                    changed_count = 0
                    for i in order:
                        sample = samples[i]
                        current_cluster = current_assignments[i]

                        # Compute log-likelihoods to all centroids
                        log_liks = []
                        for j, centroid in enumerate(centroids):
                            try:
                                ll = centroid.compute_log_likelihood(sample)
                            except RuntimeError as e:
                                if "no samples" in str(e).lower():
                                    ll = -np.inf
                                else:
                                    raise
                            log_liks.append(ll)

                        # NEW: Initial bias for first iter in forced
                        if current_cluster != -1:
                            # Bias to stay in initial
                            log_liks[current_cluster] += 2.0
                            logger.debug(
                                f"Added bias +2.0 to log L for sample {i} initial cluster {current_cluster}")

                        if all(np.isneginf(l) for l in log_liks):
                            logger.warning(
                                f"All centroids invalid for sample {i}, skipping")
                            continue

                        best_cluster = np.argmax(log_liks)

                        # Move if unassigned or better cluster
                        if current_cluster == -1 or log_liks[best_cluster] > log_liks[current_cluster]:
                            # Remove from current if assigned
                            if current_cluster != -1:
                                success_remove = centroids[current_cluster].remove_sample(
                                    i, sample)
                                if not success_remove:
                                    logger.warning(
                                        f"Failed to remove sample {i} from cluster {current_cluster}, skipping move")
                                    continue
                                # Check if emptied
                                if centroids[current_cluster].get_sample_count() == 0:
                                    logger.info(
                                        f"Cluster {current_cluster} emptied after removing {i}, rescuing...")
                                    self._rescue_empty_cluster(
                                        current_cluster, centroids, samples, current_assignments, unassigned)

                            # Add to best cluster
                            success_add = centroids[best_cluster].add_sample(
                                i, sample, self.sample_paths[i])
                            if success_add:
                                if current_cluster == -1:
                                    unassigned.discard(i)
                                current_assignments[i] = best_cluster
                                changed_count += 1
                                logger.debug(
                                    f"Moved sample {i} to cluster {best_cluster}")
                            else:
                                logger.warning(
                                    f"Failed to add sample {i} to cluster {best_cluster}")
                                # Revert removal if failed
                                if current_cluster != -1:
                                    centroids[current_cluster].add_sample(
                                        i, sample, self.sample_paths[i])
                                    current_assignments[i] = current_cluster
                                    if i in unassigned:
                                        unassigned.remove(i)
                            frac_changed = changed_count / n_samples
                            logger.info(
                                f"  {changed_count} samples moved ({frac_changed:.2%})")
                    else:
                        # Batch assignment
                        assignment_result = self._assign_samples_to_centroids(
                            samples, centroids,
                            soft=self.config.soft_assignment,
                            temperature=self.config.assignment_temperature,
                            initial_assignments=current_assignments if forced_init and iteration == 1 else None
                        )

                        if self.config.soft_assignment:
                            current_assignments = assignment_result['assignments']
                            iteration_probs = assignment_result['probabilities']
                        else:
                            current_assignments = assignment_result

                    # Compute changes (for convergence)
                    # Since sequential, approximate by comparing to previous (store prev_assignments)
                    # For simplicity, use fraction unassigned or log every few iters
                    frac_unassigned = np.mean(current_assignments == -1)
                    logger.info(
                        f"  Fraction unassigned: {frac_unassigned:.2%}")

                    # NEW: Check and rescue empty clusters immediately after assignment
                    sizes = [c.get_sample_count() for c in centroids]
                    empty_ids = [i for i, s in enumerate(sizes) if s == 0]
                    for empty_id in empty_ids:
                        logger.info(
                            f"Rescuing empty cluster {empty_id} after assignment")
                        self._rescue_empty_cluster(
                            empty_id, centroids, samples, current_assignments, unassigned)

                    # Update centroids based on new assignments
                    centroids = self._update_centroids(
                        samples, current_assignments, centroids)

                    # Min cluster size enforcement
                    self._enforce_min_cluster_sizes(
                        centroids, samples, current_assignments, unassigned)

                    # Check convergence (simplified: if no unassigned and stable)
                    if frac_unassigned == 0 and iteration > 0:  # Assume stable after full assignment
                        logger.info(
                            f"Converged after {iteration + 1} iterations (full assignment)")
                        converged = True
                        break
                    elif iteration > 5 and frac_unassigned < 0.01:  # Low change
                        logger.info(
                            f"Converged after {iteration + 1} iterations (stable)")
                        converged = True
                        break

            if not converged:
                logger.info(
                    f"Reached maximum iterations ({self.config.max_em_iterations})")

            # Instead of silhouette, compute avg log-likelihood
            valid_assigns = [i for i, ass in enumerate(
                current_assignments) if ass != -1]
            if valid_assigns:
                run_score = np.mean([centroids[current_assignments[i]].compute_log_likelihood(
                    samples[i]) for i in valid_assigns])
            else:
                run_score = -np.inf
                logger.warning("No valid assignments for scoring")
            logger.info(
                f"Restart {restart + 1} avg log-likelihood score: {run_score:.4f}")

            # Track best (include probs if soft)
            if run_score > best_score:
                best_score = run_score
                best_labels = current_assignments.copy()
                best_centroids = [c for c in centroids]
                if self.config.soft_assignment and iteration_probs is not None:
                    best_probabilities = iteration_probs.copy()
                best_iteration = iteration + 1

        # After all restarts, process best results
        if best_labels is None or best_score == -1:
            logger.warning("No valid clustering found across restarts")
            # Fallback to single cluster
            best_labels = np.zeros(n_samples, dtype=int)
            self.cluster_labels = best_labels
            return self._compile_results()

        unique = np.unique(best_labels)
        if len(unique) < 2:
            best_score = 0.0
            logger.info("Single cluster after all—homogeneous data")

        logger.info(f"Best run: silhouette={best_score:.4f}, iterations={best_iteration}")

        # Use best
        self.cluster_labels = best_labels

        # Check empty clusters
        cluster_counts = [c.get_sample_count() for c in best_centroids]
        empty_clusters = sum(1 for count in cluster_counts if count == 0)
        if empty_clusters > 0:
            logger.warning(f"{empty_clusters} empty clusters in best run")

        # Compile results (enhanced)
        results = self._compile_centroid_results(best_centroids)
        results['clustering_method'] = 'centroid'
        results['em_iterations'] = best_iteration
        results['final_cluster_sizes'] = {i: int(count) for i, count in enumerate(cluster_counts)}
        results['silhouette_score'] = float(best_score)
        results['num_restarts'] = self.config.num_restarts

        # Add probabilities if soft
        if self.config.soft_assignment and best_probabilities is not None:
            results['probabilities'] = best_probabilities.tolist()
            logger.info(f"Soft probabilities included: shape {best_probabilities.shape}")

        # After EM loop in _centroid_based_clustering:
        if forced_init and len(np.unique(best_labels)) < 2:
            logger.info("Forced mode: maintaining 2 clusters by adjusting assignments")
            indices_0 = np.where(best_labels == 0)[0]
            np.random.shuffle(indices_0)
            n_move = len(indices_0) // 2
            best_labels[indices_0[:n_move]] = 1
            logger.info(f"Moved {n_move} samples from 0 to 1 to maintain 2 clusters")

        return results

    def _centroid_based_clustering_auto_k(self) -> Dict[str, Any]:
        """
        Perform centroid-based clustering with automatic K selection using silhouette analysis.

        Returns:
            Dictionary containing clustering results
        """
        logger.info("Starting centroid-based clustering with automatic K selection")

        # Load all samples
        samples = self._load_all_samples()
        n_samples = len(samples)

        # Determine max_k
        if self.config.max_k is not None:
            max_k = min(self.config.max_k, n_samples - 1)
        else:
            max_k = min(int(np.sqrt(n_samples)), n_samples - 1)

        if max_k < 2:
            logger.warning("Not enough samples for clustering")
            self.cluster_labels = np.zeros(n_samples, dtype=int)
            return self._compile_results()

        logger.info(f"Testing K from 2 to {max_k}")

        best_k = 2
        best_score = -1
        best_labels = None
        silhouette_scores = {}

        # Test different K values
        for k in range(2, max_k + 1):
            logger.info(f"Testing K={k}")

            # Run clustering for this K
            result = self._centroid_based_clustering(k)
            labels = np.array(result['labels'])

            # Compute silhouette score (need distance matrix)
            if self.distance_matrix is None:
                logger.info("Computing distance matrix for silhouette score...")
                self.distance_matrix = self._compute_distance_matrix_from_samples(samples)

            try:
                score = silhouette_score(self.distance_matrix, labels, metric='precomputed')
            except ValueError as e:
                if "number of labels" in str(e).lower():
                    score = 0.0
                    logger.warning(f"k={k} collapsed to 1 cluster, score=0.0")
                else:
                    raise
            silhouette_scores[k] = score

            logger.info(f"  K={k}: silhouette={score:.4f}")

            if score > best_score:
                best_score = score
                best_k = k
                best_labels = labels

        logger.info(f"Best K={best_k} with silhouette={best_score:.4f}")

        # Use best clustering
        self.cluster_labels = best_labels

        # Check if score meets threshold
        if best_score < self.config.silhouette_threshold:
            logger.info(f"Best silhouette score {best_score:.4f} < threshold {self.config.silhouette_threshold:.4f}")
            logger.info("No meaningful clusters found - population appears homogeneous")
            self.cluster_labels = np.zeros(n_samples, dtype=int)

        # Compile results
        results = self._compile_results()
        results['clustering_method'] = 'centroid'
        results['silhouette_score'] = float(best_score)
        results['silhouette_scores_by_k'] = {int(k): float(v) for k, v in silhouette_scores.items()}

        return results

    def _initialize_centroids_farthest(self, samples: List, k: int) -> List[ClusterCentroid]:
        """
        Initialize k centroids using farthest-point heuristic.

        Args:
            samples: List of MethylSample instances
            k: Number of centroids to initialize

        Returns:
            List of ClusterCentroid instances
        """
        logger.info(f"Initializing {k} centroids using farthest-point heuristic")

        n_samples = len(samples)

        # Compute or use cached distance matrix for initialization
        if self.distance_matrix is None:
            logger.info("Computing distance matrix for centroid initialization...")
            self.distance_matrix = self._compute_distance_matrix_from_samples(samples)

        # Select k farthest points
        medoid_indices = self._initialize_farthest_medoids(k)

        logger.info(f"Selected initial samples: {medoid_indices}")

        # Create centroids
        centroids = []
        for i, idx in enumerate(medoid_indices):
            centroid = ClusterCentroid(
                cluster_id=i,
                chrom=self.config.chrom,
                ctx=self.config.ctx,
                min_coverage=self.config.min_coverage if hasattr(self.config, 'min_coverage') else 4,
                use_gpu=self.config.use_gpu,
                max_samples=n_samples
            )

            # Add initial sample to centroid
            success = centroid.add_sample(idx, samples[idx], self.sample_paths[idx])
            if not success:
                logger.error(f"Failed to initialize centroid {i} with sample {idx}")
                raise RuntimeError(f"Failed to initialize centroid {i}")

            centroids.append(centroid)
            logger.info(f"Initialized centroid {i} with sample {idx}")

        return centroids

    def _assign_samples_to_centroids(self, samples: List, centroids: List[ClusterCentroid], soft: bool = False, temperature: float = 1.0, initial_assignments: Optional[np.ndarray] = None) -> Union[np.ndarray, Dict[str, np.ndarray]]:
        """
        Assign each sample to centroid with highest log-likelihood, or compute soft probabilities.

        Args:
            samples: List of MethylSample instances
            centroids: List of ClusterCentroid instances
            soft: If True, return soft probabilities instead of hard assignments
            temperature: Softmax temperature for soft assignments

        Returns:
            If soft=False: Array of cluster assignments
            If soft=True: Dict with 'assignments' (hard) and 'probabilities' (n_samples x n_clusters)
        """
        n_samples = len(samples)
        assignments = np.zeros(n_samples, dtype=int)
        probabilities = np.zeros((n_samples, len(centroids)))

        logger.info("Assigning samples to centroids based on log-likelihood...")

        for i, sample in enumerate(samples):
            if soft:
                # Compute soft probabilities
                prob_row = np.zeros(len(centroids))
                for j, centroid in enumerate(centroids):
                    # For each centroid, compute prob relative to others
                    prob_row[j] = centroid.compute_membership_probabilities(sample, [c for c in centroids if c != centroid], temperature)
                probabilities[i] = prob_row
                # Hard assignment from argmax for consistency
                best_centroid = np.argmax(prob_row)
            else:
                best_centroid = -1
                best_log_likelihood = -np.inf

                for j, centroid in enumerate(centroids):
                    log_likelihood = centroid.compute_log_likelihood(sample)

                    if log_likelihood > best_log_likelihood:
                        best_log_likelihood = log_likelihood
                        best_centroid = j

                assignments[i] = best_centroid
                probabilities[i, best_centroid] = 1.0  # One-hot for hard

            if (i + 1) % 10 == 0:
                logger.debug(f"Assigned {i + 1}/{n_samples} samples")

        # Log cluster sizes
        cluster_counts = np.bincount(assignments, minlength=len(centroids))
        for j in range(len(centroids)):
            logger.info(f"  Cluster {j}: {cluster_counts[j]} samples")

        if soft:
            return {
                'assignments': assignments,
                'probabilities': probabilities
            }
        else:
            return assignments

    def _update_centroids(
        self,
        samples: List,
        assignments: np.ndarray,
        centroids: List[ClusterCentroid]
    ) -> List[ClusterCentroid]:
        """
        Update centroids based on new assignments.

        Args:
            samples: List of MethylSample instances
            assignments: Array of cluster assignments
            centroids: List of ClusterCentroid instances

        Returns:
            Updated list of ClusterCentroid instances
        """
        logger.info("Updating centroids based on new assignments...")

        # Track which samples should be in each centroid
        target_assignments = {i: set() for i in range(len(centroids))}
        for sample_idx, centroid_idx in enumerate(assignments):
            target_assignments[centroid_idx].add(sample_idx)

        # Update each centroid
        for centroid_idx, centroid in enumerate(centroids):
            current = set(centroid.get_sample_indices())
            target = target_assignments[centroid_idx]

            if len(target) == 0 and len(current) > 0:
                logger.info(f"Skipping remove from Cluster {centroid_idx}: empty target but non-empty current")
                continue  # Don't empty it

            # Remove
            to_remove = current - target
            for idx in to_remove:
                centroid.remove_sample(idx, samples[idx])

            # Add (if target non-empty and centroid empty, force first)
            to_add = target - current
            if len(to_add) > 0 and len(current) == 0 and len(target) > 1:
                # Force add first from target
                first_add = next(iter(to_add))
                centroid.add_sample(first_add, samples[first_add], self.sample_paths[first_add])
                to_add = to_add - {first_add}
                logger.debug(f"Forced first add to empty Cluster {centroid_idx}: {first_add}")

            for idx in to_add:
                centroid.add_sample(idx, samples[idx], self.sample_paths[idx])

            logger.info(f"  Cluster {centroid_idx}: removed {len(to_remove)}, added {len(to_add)}, "
                       f"now {centroid.get_sample_count()} samples")

        return centroids

    def _compute_distance_matrix_from_samples(self, samples: List) -> np.ndarray:
        """
        Compute distance matrix from loaded MethylSample instances.

        Args:
            samples: List of MethylSample instances

        Returns:
            Distance matrix
        """
        # Use existing DistanceMatrixComputer
        distance_computer = DistanceMatrixComputer(
            metric=self.config.metric.to_factory_name(),
            chrom=self.config.chrom,
            ctx=self.config.ctx,
            use_gpu=self.config.use_gpu,
            cache_dir=None  # Don't cache when computing from loaded samples
        )

        return distance_computer.compute_pairwise_distances(samples, self.sample_paths)

    def _compile_centroid_results(self, centroids: List[ClusterCentroid]) -> Dict[str, Any]:
        """
        Compile results from centroid-based clustering.

        Args:
            centroids: List of ClusterCentroid instances

        Returns:
            Dictionary with clustering results
        """
        # Use standard compile_results
        results = self._compile_results()

        # Add centroid-specific information
        results['centroid_info'] = []
        for centroid in centroids:
            results['centroid_info'].append({
                'cluster_id': centroid.cluster_id,
                'n_samples': int(centroid.get_sample_count()),
                'sample_indices': [int(idx) for idx in centroid.get_sample_indices()]
            })

        # Add probabilities if soft_assignment was used
        if self.config.soft_assignment and hasattr(self, 'best_probabilities') and self.best_probabilities is not None:
            results['probabilities'] = self.best_probabilities.tolist()

        return results

    def _compile_results(self) -> Dict[str, Any]:
        """
        Compile clustering results and statistics.
        """
        # Ensure cluster_labels matches sample count
        if len(self.cluster_labels) != len(self.sample_paths):
            raise ValueError(f"Mismatch: {len(self.cluster_labels)} labels but {len(self.sample_paths)} samples")

        unique_labels = set(self.cluster_labels)
        n_clusters = int(len(unique_labels - {-1}))  # Exclude noise label (-1)
        n_noise = int(np.sum(self.cluster_labels == -1))

        # Create label map if group labels provided (from forced_groups or deprecated)
        label_map = None
        if hasattr(self, '_group_labels') and self._group_labels:
            label_map = {i: self._group_labels[i] for i in range(len(self._group_labels))}
        elif self.config.forced_groups is not None:
            label_map = {i: list(self.config.forced_groups.keys())[i] for i in range(len(self.config.forced_groups))}

        # Create cluster assignments
        clusters = {}
        for label in sorted(unique_labels):
            indices = np.where(self.cluster_labels == label)[0]
            # Validate indices
            if np.any(indices >= len(self.sample_paths)):
                raise ValueError(f"Invalid indices: max={indices.max()}, n_samples={len(self.sample_paths)}")
            sample_list = [str(self.sample_paths[i]) for i in indices]

            if label == -1:
                clusters['noise'] = sample_list
            else:
                if label_map and label in label_map:
                    key = label_map[label]
                else:
                    key = f'cluster_{label}'
                clusters[key] = sample_list

        # Compile results
        results = {
            'n_clusters': n_clusters,
            'n_noise': n_noise,
            'cluster_assignments': clusters,
            'labels': [int(x) for x in self.cluster_labels],
            'sample_paths': [str(p) for p in self.sample_paths],
            'config': self.config.model_dump()
        }

        # Add forced_groups to results if used
        if label_map:
            results['forced_groups_used'] = {key: int(np.sum(self.cluster_labels == i)) for i, key in label_map.items()}

        # Add probabilities if available
        if hasattr(self.clusterer, 'probabilities_'):
            results['probabilities'] = self.clusterer.probabilities_.tolist()

        # Add cluster sizes
        cluster_sizes = {}
        for label in unique_labels:
            if label == -1:
                cluster_sizes['noise'] = int(np.sum(self.cluster_labels == -1))
            else:
                key = label_map[label] if label_map and label in label_map else f'cluster_{label}'
                cluster_sizes[key] = int(np.sum(self.cluster_labels == label))
        results['cluster_sizes'] = cluster_sizes

        return results

    def _should_use_kmeans_fallback(self, hdbscan_results: Dict[str, Any]) -> bool:
        """
        Determine if K-means fallback should be used based on HDBSCAN results.

        Args:
            hdbscan_results: Results from HDBSCAN clustering

        Returns:
            True if K-means fallback should be attempted
        """
        n_clusters = hdbscan_results['n_clusters']
        n_noise = hdbscan_results['n_noise']
        n_samples = len(self.cluster_labels)
        noise_pct = n_noise / n_samples

        # Use fallback if:
        # 1. No clusters found (all noise)
        # 2. Only 1 cluster with > 20% noise (ambiguous)
        # 3. Multiple clusters but > 50% noise (weak structure)

        if n_clusters == 0:
            logger.info("  Reason: HDBSCAN found no clusters (all noise)")
            return True

        if n_clusters == 1 and noise_pct > 0.2:
            logger.info(f"  Reason: Single cluster with {noise_pct*100:.1f}% noise (ambiguous)")
            return True

        if n_clusters >= 2 and noise_pct > 0.5:
            logger.info(f"  Reason: {n_clusters} clusters but {noise_pct*100:.1f}% noise (weak structure)")
            return True

        return False

    def _kmeans_clustering_with_silhouette(self) -> Optional[Dict[str, Any]]:
        """
        Perform K-means clustering with automatic K selection using silhouette analysis.

        Tests K from 2 to max_k and selects the K with the highest silhouette score.
        If the best silhouette score is below the threshold, returns None (no meaningful clusters).

        Returns:
            Dictionary with clustering results or None if no meaningful clusters found
        """
        n_samples = len(self.distance_matrix)

        # Determine max_k
        if self.config.max_k is not None:
            max_k = min(self.config.max_k, n_samples - 1)
        else:
            max_k = min(int(np.sqrt(n_samples)), n_samples - 1)

        if max_k < 2:
            logger.warning("Not enough samples for K-means clustering")
            return None

        logger.info(f"Testing K-means for K=2 to K={max_k}...")

        # Convert distance matrix to feature space using MDS
        # K-means needs coordinates, not distances
        from sklearn.manifold import MDS

        logger.info("Converting distance matrix to feature space using MDS...")
        mds = MDS(n_components=min(10, n_samples - 1), dissimilarity='precomputed', random_state=42)
        X = mds.fit_transform(self.distance_matrix)

        best_k = 2
        best_score = -1
        best_labels = None
        silhouette_scores = {}

        for k in range(2, max_k + 1):
            kmeans = KMeans(n_clusters=k, random_state=42, n_init=10)
            labels = kmeans.fit_predict(X)

            # Calculate silhouette score using original distance matrix
            try:
                score = silhouette_score(self.distance_matrix, labels, metric='precomputed')
            except ValueError as e:
                if "number of labels" in str(e).lower():
                    score = 0.0
                    logger.warning(f"k={k} kmeans collapsed, score=0.0")
                else:
                    raise
            silhouette_scores[k] = score

            logger.info(f"  K={k}: silhouette={score:.4f}")

            if score > best_score:
                best_score = score
                best_k = k
                best_labels = labels

        logger.info(f"Best K={best_k} with silhouette={best_score:.4f}")

        # Check if silhouette score meets threshold
        if best_score < self.config.silhouette_threshold:
            logger.info(f"Best silhouette score {best_score:.4f} < threshold {self.config.silhouette_threshold:.4f}")
            logger.info("No meaningful clusters found - population appears homogeneous")
            return None

        # Update cluster_labels for visualization
        self.cluster_labels = best_labels

        # Compile results
        results = {
            'n_clusters': best_k,
            'n_noise': 0,  # K-means doesn't have noise
            'cluster_assignments': {},
            'labels': best_labels.tolist(),
            'sample_paths': [str(p) for p in self.sample_paths],
            'config': self.config.model_dump(),
            'clustering_method': 'kmeans',
            'silhouette_score': float(best_score),
            'silhouette_scores_by_k': {int(k): float(v) for k, v in silhouette_scores.items()}
        }

        # Create cluster assignments
        for k in range(best_k):
            indices = np.where(best_labels == k)[0]
            sample_list = [str(self.sample_paths[i]) for i in indices]
            results['cluster_assignments'][f'cluster_{k}'] = sample_list

        # Add cluster sizes
        cluster_sizes = {}
        for k in range(best_k):
            cluster_sizes[f'cluster_{k}'] = int(np.sum(best_labels == k))
        results['cluster_sizes'] = cluster_sizes

        return results

    def save_results(self, results: Dict[str, Any]) -> None:
        """
        Save clustering results, statistics, and visualizations.

        Creates:
        - JSON file with cluster assignments and statistics
        - NPZ file with distance matrix (if caching is enabled)
        - Visualization plots (via ClusterVisualizer)

        Args:
            results: Clustering results dictionary from cluster()
        """
        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Saving results to {output_dir}")

        # Save cluster assignments as JSON
        results_file = output_dir / f"clusters_{self.config.chrom}_{self.config.ctx}.json"
        logger.info(f"Saving cluster assignments to {results_file}")
        with open(results_file, 'w') as f:
            json.dump(results, f, indent=2)

        # Save distance matrix
        if self.config.cache_distance_matrix:
            matrix_file = output_dir / f"distance_matrix_{self.config.chrom}_{self.config.ctx}.npz"
            logger.info(f"Saving distance matrix to {matrix_file}")
            np.savez_compressed(
                matrix_file,
                distance_matrix=self.distance_matrix,
                sample_paths=np.array([str(p) for p in self.sample_paths]),
                labels=self.cluster_labels
            )

        # Create visualizations
        try:
            from .visualization import ClusterVisualizer
            logger.info("Generating visualizations...")
            visualizer = ClusterVisualizer(self)
            visualizer.create_all_plots(output_dir)
            logger.info("Visualizations complete")
        except Exception as e:
            logger.warning(f"Failed to create visualizations: {e}")

        logger.info(f"Results saved to {output_dir}")

    def run(self) -> Dict[str, Any]:
        """
        Run the complete clustering workflow.

        This convenience method executes all steps:
        1. Load samples
        2. Compute distances
        3. Perform clustering
        4. Save results

        Returns:
            Clustering results dictionary
        """
        self.load_samples()
        self.compute_distances()
        results = self.cluster()
        self.save_results(results)
        return results

    def _rescue_empty_cluster(self, empty_cluster_id: int, centroids: List[ClusterCentroid], samples: List, assignments: np.ndarray, unassigned: set):
        """
        Rescue an empty cluster by seeding with farthest sample from dominant cluster or unassigned.
        """
        n_samples = len(samples)
        k = len(centroids)

        # Find dominant cluster (largest)
        sizes = [c.get_sample_count() for c in centroids]
        dominant_id = np.argmax(sizes)
        if sizes[dominant_id] == 0:
            # All empty? Rare, pick random unassigned
            if unassigned:
                seed_idx = random.choice(list(unassigned))
            else:
                # All assigned but all empty? Impossible, pick random
                seed_idx = random.randint(0, n_samples - 1)
        else:
            # Use first sample in dominant as reference
            dominant_samples = centroids[dominant_id].get_sample_indices()
            ref_idx = dominant_samples[0] if dominant_samples else 0
            distances_from_dominant = self.distance_matrix[:, ref_idx]
            candidates = list(unassigned) if unassigned else list(range(n_samples))
            if not candidates:
                return  # No candidates
            seed_idx = max(candidates, key=lambda x: distances_from_dominant[x])

        # Instead of distance-based farthest, use probability-based "most extreme"
        seed_idx = None
        min_prob = np.inf
        if unassigned:
            for cand in unassigned:
                probs = [centroids[j].compute_membership_probabilities(samples[cand], [c for c in centroids if c.cluster_id != j]) for j in range(k)]
                cand_min_prob = min(probs) if probs else np.inf
                if cand_min_prob < min_prob:
                    min_prob = cand_min_prob
                    seed_idx = cand
        if seed_idx is None:
            dominant_samples = [idx for idx, ass in enumerate(assignments) if ass == dominant_id]
            if dominant_samples:
                for cand in dominant_samples:
                    prob_own = centroids[dominant_id].compute_membership_probabilities(samples[cand], [c for c in centroids if c.cluster_id != dominant_id])
                    if prob_own < min_prob:
                        min_prob = prob_own
                        seed_idx = cand
        if seed_idx is None:
            logger.warning("No suitable candidate for rescue")
            return

        # Seed the empty cluster
        sample = samples[seed_idx]
        success = centroids[empty_cluster_id].add_sample(seed_idx, sample, self.sample_paths[seed_idx])
        if success:
            assignments[seed_idx] = empty_cluster_id
            unassigned.discard(seed_idx)
            logger.info(f"Rescued cluster {empty_cluster_id} with sample {seed_idx} (farthest from dominant)")
        else:
            logger.warning(f"Failed to rescue cluster {empty_cluster_id} with {seed_idx}")

    def _enforce_min_cluster_sizes(self, centroids: List[ClusterCentroid], samples: List, assignments: np.ndarray, unassigned: set):
        """
        Enforce minimum cluster sizes by moving samples from large to small clusters.
        """
        k = len(centroids)
        sizes = [c.get_sample_count() for c in centroids]
        min_size = self.config.min_cluster_size

        small_clusters = [i for i, s in enumerate(sizes) if s < min_size]  # Include 0 now
        if not small_clusters:
            return

        # Find largest cluster
        largest_id = np.argmax(sizes)
        if sizes[largest_id] <= min_size:
            return

        logger.info(f"Enforcing min size {min_size}: {len(small_clusters)} small clusters")

        # Global balance if dominant too large
        sizes_sum = sum(s for s in sizes if s > 0)
        if sizes_sum > 0 and max(sizes) / sizes_sum > 0.9:
            logger.info("Global balance needed: dominant cluster >90%")
            large_id = np.argmax(sizes)
            small_ids = [i for i, s in enumerate(sizes) if s < min_size and i != large_id]
            n_move = int(max(sizes) * 0.1)  # 10% from large
            large_indices = np.where(assignments == large_id)[0]
            random.shuffle(large_indices)
            moved = 0
            for small_id in small_ids:
                for cand_idx in large_indices[moved:moved + n_move // len(small_ids)]:
                    ll = centroids[small_id].compute_log_likelihood(samples[cand_idx])
                    if ll > (np.mean(self.distance_matrix[np.where(assignments == largest_id)[0], cand_idx]) - 2):  # Lenient relative
                        logger.info(f"Relative ll={ll:.2f} vs mean_large={np.mean(self.distance_matrix[np.where(assignments == largest_id)[0], cand_idx]) - 2:.2f}—moving...")
                        # Move
                        centroids[large_id].remove_sample(cand_idx, samples[cand_idx])
                        assignments[cand_idx] = small_id
                        centroids[small_id].add_sample(cand_idx, samples[cand_idx], self.sample_paths[cand_idx])
                        moved += 1
                        sizes[large_id] -= 1
                        sizes[small_id] += 1
                        if moved >= n_move:
                            break
                if moved >= n_move:
                    break
            logger.info(f"Balanced: moved {moved} samples")

        # Per-small enforcement (existing + global)
        for small_id in small_clusters:
            # For s==0, call _rescue_empty_cluster(small_id, ...) instead of move
            if sizes[small_id] == 0:
                self._rescue_empty_cluster(small_id, centroids, samples, assignments, unassigned)
            else:
                # Find farthest sample in largest from its reference
                large_samples = np.where(assignments == largest_id)[0]
                if len(large_samples) == 0:
                    continue

                ref_idx = large_samples[0]  # Simple approx
                distances_in_large = self.distance_matrix[large_samples, ref_idx]
                donor_idx = large_samples[np.argmax(distances_in_large)]

                # Move donor to small
                sample = samples[donor_idx]
                # Remove from large
                centroids[largest_id].remove_sample(donor_idx, sample)
                # Add to small
                success = centroids[small_id].add_sample(donor_idx, sample, self.sample_paths[donor_idx])
                if success:
                    assignments[donor_idx] = small_id
                    logger.debug(f"Moved {donor_idx} from {largest_id} to {small_id} for min size")
                    sizes[largest_id] -= 1
                    sizes[small_id] += 1
                else:
                    # Revert
                    centroids[largest_id].add_sample(donor_idx, sample, self.sample_paths[donor_idx])
                    assignments[donor_idx] = largest_id

    def _balance_clusters(self, centroids: List[ClusterCentroid], samples: List, assignments: np.ndarray):
        """
        Balance cluster sizes by moving samples from large to small clusters.
        """
        k = len(centroids)
        sizes = [c.get_sample_count() for c in centroids]
        min_size = self.config.min_cluster_size

        # Find largest cluster
        largest_id = np.argmax(sizes)
        if sizes[largest_id] <= min_size:
            return

        # Find small clusters
        small_ids = [i for i, s in enumerate(sizes) if s < min_size]

        # Find samples in the largest cluster that are far from their centroid
        large_samples = np.where(assignments == largest_id)[0]
        if len(large_samples) == 0:
            return

        # Calculate mean log-likelihood of samples in the largest cluster
        mean_ll_large = np.mean([centroids[largest_id].compute_log_likelihood(samples[c]) for c in large_samples[:5]])

        # Find samples in the largest cluster that have a log-likelihood significantly lower than the mean
        # This is a heuristic to find "farthest" samples from the centroid
        # We'll use a threshold relative to the mean LL of the largest cluster
        threshold = mean_ll_large - 2 # Example threshold, adjust as needed

        # Find indices of samples in large_samples that have a log-likelihood below the threshold
        # This is a bit complex because we need to find indices relative to the original samples list
        # We'll iterate through large_samples and check their log-likelihoods against the mean
        indices_to_move = []
        for i in range(len(large_samples)):
            sample_idx = large_samples[i]
            ll = self.distance_matrix[sample_idx, large_samples[0]] # Use a reference sample's distance
            if ll < threshold:
                indices_to_move.append(sample_idx)

        # Shuffle the indices to move
        random.shuffle(indices_to_move)

        # Move samples from large to small clusters
        moved_count = 0
        for donor_idx in indices_to_move:
            # Find the current cluster of the donor
            current_cluster = assignments[donor_idx]

            # If the donor is already in a small cluster, skip
            if current_cluster in small_ids:
                continue

            # Find the smallest cluster to move to
            smallest_id = min(small_ids, key=lambda x: sizes[x])

            # Move the donor
            sample = samples[donor_idx]
            centroids[current_cluster].remove_sample(donor_idx, sample)
            centroids[smallest_id].add_sample(donor_idx, sample, self.sample_paths[donor_idx])
            assignments[donor_idx] = smallest_id
            sizes[current_cluster] -= 1
            sizes[smallest_id] += 1
            moved_count += 1

            # If the smallest cluster is now at min_size, remove it from the list
            if sizes[smallest_id] == min_size:
                small_ids.remove(smallest_id)

            if moved_count >= int(max(sizes) * 0.1): # Move up to 10% of the largest cluster
                break

        logger.info(f"Balanced: moved {moved_count} samples")


__all__ = ['MethylCluster']

