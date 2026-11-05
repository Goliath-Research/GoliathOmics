Installable GoliathOmics child-app package (`goliath-omics`).

Source lives in [`../src/goliath_omics/`](../src/goliath_omics/). Build with:

```bash
python -m pip wheel domain -w wheels/ --no-deps
```

Release builds pick up this tree via `scripts/packages.list` before `workers`.
