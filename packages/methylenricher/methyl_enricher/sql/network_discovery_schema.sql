PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS network_snapshot (
  snapshot_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  project_name TEXT,
  scan_roots_json TEXT NOT NULL,
  n_edges INTEGER NOT NULL,
  n_module_memberships INTEGER NOT NULL,
  config_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS network_edge (
  edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id TEXT NOT NULL,
  gene_a TEXT NOT NULL,
  gene_b TEXT NOT NULL,
  occurrence_count INTEGER NOT NULL,
  support_runs_count INTEGER NOT NULL,
  mean_observed_score REAL NOT NULL,
  max_observed_score REAL NOT NULL,
  module_support_count INTEGER NOT NULL,
  custom_score REAL NOT NULL,
  in_string INTEGER NOT NULL,
  string_score REAL NOT NULL,
  novelty_status TEXT NOT NULL,
  status TEXT NOT NULL,
  UNIQUE(snapshot_id, gene_a, gene_b),
  FOREIGN KEY(snapshot_id) REFERENCES network_snapshot(snapshot_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS edge_evidence (
  evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id TEXT NOT NULL,
  gene_a TEXT NOT NULL,
  gene_b TEXT NOT NULL,
  evidence_type TEXT NOT NULL,
  evidence_value REAL NOT NULL,
  source_run TEXT,
  source_file TEXT,
  FOREIGN KEY(snapshot_id) REFERENCES network_snapshot(snapshot_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS module_membership (
  membership_id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id TEXT NOT NULL,
  module_label TEXT NOT NULL,
  gene TEXT NOT NULL,
  source_run TEXT,
  source_file TEXT,
  FOREIGN KEY(snapshot_id) REFERENCES network_snapshot(snapshot_id) ON DELETE CASCADE
);
