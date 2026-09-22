"""
Visualization module for clustering results.

This module creates comprehensive visualizations of clustering results including
distance matrices, cluster trees, and dimensionality reduction projections.
"""

import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.manifold import TSNE
from sklearn.cluster import AgglomerativeClustering
from scipy.cluster.hierarchy import dendrogram, linkage
from pathlib import Path
import numpy as np
import logging

from .cluster import MethylCluster

logger = logging.getLogger(__name__)


class ClusterVisualizer:
    """
    Generates visualizations for clustering results.
    """
    
    def __init__(self, clusterer: MethylCluster):
        self.clusterer = clusterer
        self.distance_matrix = clusterer.distance_matrix
        self.labels = clusterer.cluster_labels
        self.sample_paths = [p.name for p in clusterer.sample_paths]  # Use sample names for labels
    
    def create_all_plots(self, output_dir: Path):
        """
        Create all visualizations.
        
        Args:
            output_dir: Directory to save plots
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        
        self.plot_dendrogram(output_dir / "dendrogram.png")
        self.plot_tsne(output_dir / "tsne.png")
        self.plot_heatmap(output_dir / "heatmap.png")
    
    def plot_dendrogram(self, output_path: Path):
        """
        Create hierarchical clustering dendrogram.
        """
        # Compute linkage matrix
        condensed_dist = np.triu(self.distance_matrix)
        linkage_matrix = linkage(condensed_dist, method='average')
        
        plt.figure(figsize=(12, 8))
        dendrogram(linkage_matrix, labels=self.sample_paths)
        plt.title("Hierarchical Clustering Dendrogram")
        plt.xlabel("Samples")
        plt.ylabel("Distance")
        plt.xticks(rotation=90)
        plt.tight_layout()
        plt.savefig(output_path)
        plt.close()
        logger.info(f"Dendrogram saved to {output_path}")
    
    def plot_tsne(self, output_path: Path):
        """
        Create t-SNE visualization of clusters.
        """
        tsne = TSNE(n_components=2, metric="precomputed", random_state=42)
        tsne_results = tsne.fit_transform(self.distance_matrix)
        
        plt.figure(figsize=(10, 8))
        unique_labels = np.unique(self.labels)
        for label in unique_labels:
            idx = self.labels == label
            plt.scatter(tsne_results[idx, 0], tsne_results[idx, 1], label=f"Cluster {label}")
        
        plt.title("t-SNE visualization of clusters")
        plt.xlabel("t-SNE 1")
        plt.ylabel("t-SNE 2")
        plt.legend()
        plt.tight_layout()
        plt.savefig(output_path)
        plt.close()
        logger.info(f"t-SNE plot saved to {output_path}")
    
    def plot_heatmap(self, output_path: Path):
        """
        Create distance matrix heatmap with cluster labels.
        """
        # Sort samples by cluster labels
        sort_idx = np.argsort(self.labels)
        sorted_matrix = self.distance_matrix[sort_idx][:, sort_idx]
        sorted_labels = [self.sample_paths[i] for i in sort_idx]
        
        plt.figure(figsize=(12, 10))
        sns.heatmap(sorted_matrix, xticklabels=sorted_labels, yticklabels=sorted_labels)
        plt.title("Distance Matrix Heatmap (sorted by clusters)")
        plt.tight_layout()
        plt.savefig(output_path)
        plt.close()
        logger.info(f"Heatmap saved to {output_path}")


__all__ = ['ClusterVisualizer']

