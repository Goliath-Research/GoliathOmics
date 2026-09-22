# methyldomain — implementation

Shared domain types, tagged JSON (`$type`), `resolvedConfig` helpers, CAAS content store, and schema export for workflow scope variables.

## Key modules

| Module | Role |
|--------|------|
| `methyl_domain/types.py` | Tagged domain models (`MethylSampleRef`, `ExtractionQcRef`, …) |
| `methyl_domain/helpers.py` | Project/group resolution, path helpers |
| `methyl_domain/schema_export.py` | `methyl-export-domain-schemas` |
| `methyl_domain/content_store.py` | Content-addressed action store (CAAS) |

## Related

- [Domain types contract](../../workflow_engine/contract/domain_types.md)
- [Usage ch.17 CAAS](../../docs/usage/17-content-addressed-action-store.md)
