# samd_research mode overlays

Named statistical axes folded into `samd_research`.

| Mode | Legacy profile |
|------|----------------|
| `dmp_raw` | `mc_dmp` |
| `dmp_fc` | `mc_dmp_fc` |
| `gene_enricher` | `mc_gene` |
| `gene_fc` | `mc_gene_fc` |
| `dual_fc` | `mc_dmp_gene_fc` (also default intent of `samd_research`) |

Loader: `pipeline_profiles.load_profile` merges `samd_research.profile.json` + `<mode>.mode.json`.

Prefer `pipelineProfile: samd_research` with `researchMode` for new studies — that path
**keeps** `samd_research` lifecycle knobs (early-stop on, research regulatory shell).

Deprecated `mc_*` aliases apply the same statistical overlay, then reset early-stop /
holdout / claim stubs so legacy research-axis behavior is preserved.
