from pathlib import Path
from types import SimpleNamespace

from methyl_validation import pipeline_runner


def test_run_pipeline_for_production_includes_progression_when_enabled(monkeypatch):
    calls = []

    def _ok(name):
        def _f(*args, **kwargs):
            calls.append(name)
            return 0, "ok", ""

        return _f

    monkeypatch.setattr(pipeline_runner, "run_centroid", _ok("methyl-centroid"))
    monkeypatch.setattr(pipeline_runner, "run_detector", _ok("methyl-detector"))
    monkeypatch.setattr(pipeline_runner, "run_mapper", _ok("methyl-mapper"))
    monkeypatch.setattr(pipeline_runner, "run_enricher", _ok("methyl-enricher"))
    monkeypatch.setattr(pipeline_runner, "run_progression", _ok("methyl-disease-progression"))
    monkeypatch.setattr(pipeline_runner, "_progression_settings", lambda _p: {"enabled": True})

    ok, errors, timings = pipeline_runner.run_pipeline_for_production(
        Path("/tmp/project.json"),
        config=None,
    )
    assert ok
    assert not errors
    assert [t["step_name"] for t in timings] == calls
    assert calls[-1] == "methyl-disease-progression"


def test_run_pipeline_for_production_skips_progression_when_enricher_skip(monkeypatch):
    calls = []

    def _ok(name):
        def _f(*args, **kwargs):
            calls.append(name)
            return 0, "ok", ""

        return _f

    monkeypatch.setattr(pipeline_runner, "run_centroid", _ok("methyl-centroid"))
    monkeypatch.setattr(pipeline_runner, "run_detector", _ok("methyl-detector"))
    monkeypatch.setattr(pipeline_runner, "run_mapper", _ok("methyl-mapper"))
    monkeypatch.setattr(pipeline_runner, "run_enricher", _ok("methyl-enricher"))
    monkeypatch.setattr(pipeline_runner, "run_progression", _ok("methyl-disease-progression"))
    monkeypatch.setattr(pipeline_runner, "_progression_settings", lambda _p: {"enabled": True})
    monkeypatch.setattr(pipeline_runner, "_enricher_config", lambda _p: {"skip": True})

    ok, errors, timings = pipeline_runner.run_pipeline_for_production(
        Path("/tmp/project.json"),
        config=None,
    )
    assert ok
    assert not errors
    assert "methyl-enricher" not in calls
    assert "methyl-disease-progression" not in calls
    assert [t["step_name"] for t in timings] == calls


def test_run_pipeline_for_production_supports_skip_centroid(monkeypatch):
    calls = []

    def _ok(name):
        def _f(*args, **kwargs):
            calls.append(name)
            return 0, "ok", ""

        return _f

    monkeypatch.setattr(pipeline_runner, "run_centroid", _ok("methyl-centroid"))
    monkeypatch.setattr(pipeline_runner, "run_detector", _ok("methyl-detector"))
    monkeypatch.setattr(pipeline_runner, "run_mapper", _ok("methyl-mapper"))
    monkeypatch.setattr(pipeline_runner, "run_enricher", _ok("methyl-enricher"))
    monkeypatch.setattr(pipeline_runner, "_progression_settings", lambda _p: {"enabled": False})

    ok, errors, timings = pipeline_runner.run_pipeline_for_production(
        Path("/tmp/project.json"),
        skip_centroid=True,
        config=None,
    )
    assert ok
    assert not errors
    assert "methyl-centroid" not in calls
    assert calls == ["methyl-detector", "methyl-mapper", "methyl-enricher"]
    assert [t["step_name"] for t in timings] == calls


def test_run_pipeline_for_production_supports_skip_detection(monkeypatch):
    calls = []

    def _ok(name):
        def _f(*args, **kwargs):
            calls.append(name)
            return 0, "ok", ""

        return _f

    monkeypatch.setattr(pipeline_runner, "run_centroid", _ok("methyl-centroid"))
    monkeypatch.setattr(pipeline_runner, "run_detector", _ok("methyl-detector"))
    monkeypatch.setattr(pipeline_runner, "run_mapper", _ok("methyl-mapper"))
    monkeypatch.setattr(pipeline_runner, "run_enricher", _ok("methyl-enricher"))
    monkeypatch.setattr(pipeline_runner, "_progression_settings", lambda _p: {"enabled": False})

    ok, errors, timings = pipeline_runner.run_pipeline_for_production(
        Path("/tmp/project.json"),
        skip_detection=True,
        config=None,
    )
    assert ok
    assert not errors
    assert "methyl-centroid" not in calls
    assert "methyl-detector" not in calls
    assert calls == ["methyl-mapper", "methyl-enricher"]
    assert [t["step_name"] for t in timings] == calls


def test_run_pipeline_for_production_excludes_fragmentomics(monkeypatch):
    calls = []

    def _ok(name):
        def _f(*args, **kwargs):
            calls.append(name)
            return 0, "ok", ""

        return _f

    monkeypatch.setattr(pipeline_runner, "run_centroid", _ok("methyl-centroid"))
    monkeypatch.setattr(pipeline_runner, "run_detector", _ok("methyl-detector"))
    monkeypatch.setattr(pipeline_runner, "run_mapper", _ok("methyl-mapper"))
    monkeypatch.setattr(pipeline_runner, "run_enricher", _ok("methyl-enricher"))
    monkeypatch.setattr(pipeline_runner, "_progression_settings", lambda _p: {"enabled": False})

    ok, errors, timings = pipeline_runner.run_pipeline_for_production(
        Path("/tmp/project.json"),
        config=None,
    )
    assert ok
    assert not errors
    step_names = [t["step_name"] for t in timings]
    assert "methyl-fragmentomics" not in step_names
    assert step_names == calls
