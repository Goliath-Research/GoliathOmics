# MethylPipeline Theory

Canonical mathematical and statistical reference for MethylPipeline.

The source of truth is the code under `packages/`. When code and older notes disagree, the code wins.

Theory pages are plain Markdown with MathJax (`$…$` / `$$…$$`). They are part of the MkDocs site — not a separate Quarto book.

## Preview / build

```bash
source .venv/bin/activate
pip install -r docs-requirements.txt
mkdocs serve
# or
mkdocs build --strict
```

See [`docs/CONTRIBUTING.md`](../CONTRIBUTING.md) and [`docs/reference/documentation-toolchain.md`](../reference/documentation-toolchain.md).

## Reading order

Start at [`index.md`](index.md), then Part I foundations (`chapters/01` …) and Part II configuration/workflow theory (`chapters/11`, `12`, `15`).

Operator CLIs live in [`docs/usage/`](../usage/index.md) (`methyl-workflow-run` is canonical; legacy `methyl-validation --stability/--freeze/--model` is transitional only).
