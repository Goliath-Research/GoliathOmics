# methyl-dmp-select

Select a minimal discriminatory DMP panel from `dmps-{chr}-discovery.csv` exports using
effect-size elbow trim and/or validation-driven FeatureCuts (balanced-accuracy k-search).

Outputs `dmps-{chr}-selected.csv` (primary panel), legacy `dmps-{chr}-classifier.csv`,
`dmps-{chr}-classifier-extended.csv`, and `dmp_selection-{chr}.json` audit metadata.
