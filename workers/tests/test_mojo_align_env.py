"""GOLIATH_ALIGN_* dual-read helper (GoliathOmics worker)."""

from __future__ import annotations


def test_getenv_prefers_goliath_align(monkeypatch):
    from methyl_worker.mojo_align_env import getenv

    monkeypatch.setenv("GOLIATH_ALIGN_ROOT", "/tmp/new")
    monkeypatch.setenv("MOJO_ALIGN_ROOT", "/tmp/mid")
    monkeypatch.setenv("METHYLGRAPHER_MOJO_ROOT", "/tmp/old")
    assert getenv("ROOT") == "/tmp/new"


def test_getenv_falls_back_to_mojo_align(monkeypatch):
    from methyl_worker.mojo_align_env import getenv

    monkeypatch.delenv("GOLIATH_ALIGN_ROOT", raising=False)
    monkeypatch.setenv("MOJO_ALIGN_ROOT", "/tmp/mid")
    assert getenv("ROOT") == "/tmp/mid"


def test_image_pin_prefers_goliath_align(monkeypatch):
    from methyl_worker.mojo_align_env import image_pin

    monkeypatch.setenv("GOLIATH_ALIGN_IMAGE", "goliath/methylgrapher:1.70-mojo")
    monkeypatch.setenv("METHYL_MOJO_ALIGN_IMAGE", "goliath/methylgrapher:legacy")
    assert image_pin() == "goliath/methylgrapher:1.70-mojo"


def test_image_pin_falls_back(monkeypatch):
    from methyl_worker.mojo_align_env import image_pin

    monkeypatch.delenv("GOLIATH_ALIGN_IMAGE", raising=False)
    monkeypatch.delenv("METHYL_MOJO_ALIGN_IMAGE", raising=False)
    monkeypatch.setenv("METHYL_METHYLGRAPHER_MOJO_IMAGE", "goliath/methylgrapher:legacy")
    assert image_pin() == "goliath/methylgrapher:legacy"
