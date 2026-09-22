"""Tests for shared logging utilities (imported across multiple packages)."""

from __future__ import annotations

import logging
from pathlib import Path

from methyl_utils.logging_utils import (
    PerformanceLogger,
    create_log_file_path,
    get_logger,
    setup_logging,
    setup_module_logging,
)


def test_get_logger_sets_level():
    debug_logger = get_logger("methyl_test.debug", verbose=True)
    assert debug_logger.level == logging.DEBUG
    info_logger = get_logger("methyl_test.info", verbose=False)
    assert info_logger.level == logging.INFO


def test_setup_module_logging_returns_named_logger():
    logger = setup_module_logging("methyl_test.module", verbose=True)
    assert logger.name == "methyl_test.module"
    assert logger.level == logging.DEBUG


def test_setup_logging_configures_console_handler():
    setup_logging(verbose=True)
    root = logging.getLogger()
    assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)


def test_setup_logging_adds_file_handler(tmp_path: Path):
    log_file = tmp_path / "logs" / "run.log"
    setup_logging(verbose=False, log_file=log_file)
    root = logging.getLogger()
    logging.getLogger("methyl_test.file").info("hello")
    assert any(isinstance(h, logging.FileHandler) for h in root.handlers)
    assert log_file.exists()
    # Clean up file handlers so we don't leak descriptors into later tests.
    for h in list(root.handlers):
        if isinstance(h, logging.FileHandler):
            h.close()
            root.removeHandler(h)


def test_setup_logging_respects_explicit_log_level():
    setup_logging(log_level="WARNING")
    root = logging.getLogger()
    console = next(h for h in root.handlers if isinstance(h, logging.StreamHandler))
    assert console.level == logging.WARNING


def test_create_log_file_path_uses_timestamp_and_app():
    path = create_log_file_path(Path("/tmp/base"), "methyl-centroid", timestamp="20260101_000000")
    assert path == Path("/tmp/base/logs/methyl-centroid_20260101_000000.log")


def test_performance_logger_emits_records(caplog):
    perf = PerformanceLogger("methyl_test.perf")
    with caplog.at_level(logging.INFO, logger="methyl_test.perf"):
        perf.log_operation_start("op", chrom="1")
        perf.log_operation_end("op", 1.25, chrom="1")
        perf.log_performance_metric("rows", 100, unit="count")
    messages = " ".join(r.message for r in caplog.records)
    assert "START op" in messages
    assert "END op" in messages
    assert "METRIC rows" in messages
