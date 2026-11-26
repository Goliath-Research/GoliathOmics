"""Regression tests for the Grok completion model constant and request payload."""

from methyl_mapper.gene_disease_enricher import (
    GROK_COMPLETION_MODEL,
    GeneDiseaseEnricher,
)


def test_grok_completion_model_pinned_to_grok_4_3() -> None:
    assert GROK_COMPLETION_MODEL == "grok-4.3"


def test_grok_chat_completion_payload_uses_pinned_model() -> None:
    enricher = GeneDiseaseEnricher.__new__(GeneDiseaseEnricher)
    payload = enricher._grok_chat_completion_payload("Test prompt")
    assert payload["model"] == "grok-4.3"
    assert payload["messages"] == [{"role": "user", "content": "Test prompt"}]
    assert payload["stream"] is False
