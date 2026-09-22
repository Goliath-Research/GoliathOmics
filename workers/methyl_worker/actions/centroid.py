"""pipeline.centroid CLI action with optional shared seed baseline copy."""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from ..action_execution import ActionExecutionResult, execution_result_from_output, validate_input
from ..execution_handle import ExecutionHandle, WorkerStoppedError, bind_execution_handle, run_cancellable
from .base import CliAction, ExecutionTimer, finalize_output

logger = logging.getLogger(__name__)


class CentroidCliAction(CliAction):
    """Copy ``centroidSeedDir`` into ``outputDir`` before incremental methyl-centroid runs."""

    def execute(
        self,
        input_json: Mapping[str, Any],
        *,
        handle: Optional[ExecutionHandle] = None,
    ) -> ActionExecutionResult:
        from ..task_validation import extract_runtime_input, strip_runtime_input

        payload = dict(input_json)
        runtime = extract_runtime_input(payload)
        task_payload = strip_runtime_input(payload)
        input_model = validate_input(self.entry, task_payload)
        argv_payload = {**input_model.model_dump(mode="json"), **runtime}

        seed_dir = argv_payload.get("centroidSeedDir")
        output_dir = argv_payload.get("outputDir")
        if seed_dir and output_dir:
            from methyl_validation.project_gen import copy_centroid_seed_baseline

            copied = copy_centroid_seed_baseline(seed_dir, output_dir)
            if not copied:
                raise RuntimeError(
                    f"centroidSeedDir was set but the seed baseline could not be copied: "
                    f"{seed_dir} does not exist. Cohort-relative MC iterations require the "
                    "seed centroids built by the centroid_seed phase."
                )

        timer = ExecutionTimer()
        cmd = self.build_argv(argv_payload)
        logger.info("Running: %s", " ".join(cmd))
        with bind_execution_handle(handle):
            try:
                proc = run_cancellable(cmd, handle=handle)
            except WorkerStoppedError:
                raise
        finished_at, duration_ms = timer.finish()
        if proc.returncode != 0:
            raise RuntimeError(self._format_subprocess_failure(cmd, proc))
        collected = self.collector.collect(
            argv_payload,
            action_name=self.entry.action_name,
            stdout=proc.stdout or "",
        )
        collected.setdefault("tool", self.cli_tool)
        collected.setdefault("stdout_tail", (proc.stdout or "")[-500:])
        self._require_centroid_hdf5(argv_payload, output_dir, collected, proc.stdout or "")
        manifest_path = collected.get("manifest_path")
        output = finalize_output(
            self.entry,
            collected,
            started_at=timer.started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            exit_code=proc.returncode,
            manifest_path=manifest_path,
        )
        return execution_result_from_output(output)

    @staticmethod
    def _require_centroid_hdf5(
        argv_payload: Mapping[str, Any],
        output_dir: Any,
        collected: Mapping[str, Any],
        stdout: str,
    ) -> None:
        """Fail when a "successful" run produced no HDF5 at ``outputDir``.

        A zero exit code with no centroid on disk (e.g. the CLI wrote elsewhere)
        would otherwise persist a success manifest with empty ``artifacts`` that
        the idempotency layer replays as skipped, masking the real failure.
        """
        from pathlib import Path

        if collected.get("centroid_h5_path"):
            return
        if not output_dir:
            return
        out = Path(str(output_dir))
        chrom = argv_payload.get("chromosome")
        ctx = argv_payload.get("context")
        if chrom and ctx and (out / f"{chrom}-{ctx}.h5").is_file():
            return
        if any(out.glob("*.h5")):
            return
        raise RuntimeError(
            "methyl-centroid reported success but no *.h5 centroid was written to "
            f"{out} (chromosome={chrom!r}, context={ctx!r}). The CLI may have written to a "
            "different path; refusing to record a false-success manifest.\n"
            f"stdout tail:\n{stdout[-500:]}"
        )


from .collectors_registry import collector_centroid
from .registry import register_cli_provider

register_cli_provider(
    "pipeline.centroid",
    action_cls=CentroidCliAction,
    collector_factory=collector_centroid,
)
