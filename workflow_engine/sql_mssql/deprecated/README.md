# Deprecated SQL seeds — do not deploy

Static PCa / SamplePrep / Monte Carlo seed scripts kept for historical reference only.

**Do not** apply these on production or parity databases. Use:

- Action catalog: `seed_action_catalog.py` / `scripts/bootstrap_distributed_workers.sh`
- Workflow graphs: `scripts/deploy_workflow_definitions.sh` (DomainProgram fixtures)
- Genomes: `cfg_reference_assets_seed.sql` + [reference-inventory-qnap.md](../../../docs/deployment/reference-inventory-qnap.md)

See also deprecated flow docs `PCaOvrFlow.md` / `PCaTwoGroupFlow.md` in the parent directory (if present).
