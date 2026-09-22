# CI smoke fixture: Alzheimer cfDNA staged pack

Minimal staged (Control -> MCI -> AD) cfDNA methylation manifest used to regression-test
the Alzheimer disease pack: staged comparison resolution (`AD_MCI`, `AD_AD`), progression
ordering, and modality/analyte fields. Placeholder sample IDs only; single chromosome.

Consumed by [`workflow_engine/tests/test_alzheimer_cfdna_pack.py`](../../../tests/test_alzheimer_cfdna_pack.py).
The runnable/operator example (with the disease overlay and SaMD ladder instructions) lives
under [`docs/examples/samd/alzheimer-cfdna/`](../../../../docs/examples/samd/alzheimer-cfdna/README.md).
