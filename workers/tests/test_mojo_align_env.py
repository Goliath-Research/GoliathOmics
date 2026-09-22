"""MOJO_ALIGN_* dual-read helper (MethylPipeline worker)."""

from __future__ import annotations


def test_getenv_prefers_new(monkeypatch):
    from methyl_worker.mojo_align_env import getenv

    monkeypatch.setenv("MOJO_ALIGN_ROOT", "/tmp/new")
    monkeypatch.setenv("METHYLGRAPHER_MOJO_ROOT", "/tmp/old")
    assert getenv("ROOT") == "/tmp/new"


def test_image_pin_falls_back(monkeypatch):
    from methyl_worker.mojo_align_env import image_pin

    monkeypatch.delenv("METHYL_MOJO_ALIGN_IMAGE", raising=False)
    monkeypatch.setenv("METHYL_METHYLGRAPHER_MOJO_IMAGE", "goliath/methylgrapher:legacy")
    assert image_pin() == "goliath/methylgrapher:legacy"
