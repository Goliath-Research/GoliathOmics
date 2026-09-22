"""Export versioned JSON Schema for AlignmentQC sample payload."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from methyl_alignment_qc.models.sample_qc import ExportedSampleQCPayload
from methyl_alignment_qc.models.sample_qc_v2 import ExportedSampleQCV2Payload


def _schema_dict_from_model(model, *, title: str) -> dict:
    schema = model.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema.setdefault("title", title)
    return schema


def generate_schema_dict_v1() -> dict:
    """Generate JSON Schema from strict Pydantic model (V1 columnar export)."""
    return _schema_dict_from_model(ExportedSampleQCPayload, title="ExportedSampleQCPayload")


def generate_schema_dict_v2() -> dict:
    """Generate JSON Schema for the published slim V2.1 guardrail-summary export."""
    return _schema_dict_from_model(ExportedSampleQCV2Payload, title="ExportedSampleQCV2Payload")


def _write_schema_file(output_path: Path, schema: dict) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


def write_schema_v1(output_path: Path) -> Path:
    return _write_schema_file(output_path, generate_schema_dict_v1())


def write_schema_v2(output_path: Path) -> Path:
    return _write_schema_file(output_path, generate_schema_dict_v2())


def _default_schema_output_path(variant: str) -> Path:
    pkg_root = Path(__file__).resolve().parents[2]
    if variant == "v2":
        return pkg_root / "schemas" / "exported_sample_qc_v2.schema.json"
    return pkg_root / "schemas" / "exported_sample_qc.schema.json"


def _central_schema_path(variant: str) -> Path | None:
    try:
        from methyl_validation.config_schema_registry import CONFIG_SCHEMA_SPECS
        from methyl_validation.schema_export import repo_schemas_config_dir

        spec_id = "alignment_qc_export_v2" if variant == "v2" else "alignment_qc_export_v1"
        spec = next(s for s in CONFIG_SCHEMA_SPECS if s.schema_id == spec_id)
        return repo_schemas_config_dir() / spec.filename
    except ImportError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Export JSON Schema for exported AlignmentQC payload.")
    parser.add_argument(
        "--variant",
        choices=["v1", "v2"],
        default="v1",
        help="Schema variant: v1 internal columnar assembly or v2.1 slim export (default: v1)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSON schema path (default depends on --variant)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if schema artifacts are stale (uses central exporter when methyl-validation is installed).",
    )
    args = parser.parse_args()

    if args.check:
        try:
            from methyl_validation.config_schema_registry import CONFIG_SCHEMA_SPECS
            from methyl_validation.schema_export import check_config_schema_drift
        except ImportError as exc:
            print(
                "methyl-qc-export-schema --check requires methyl-validation in the environment.",
                file=sys.stderr,
            )
            raise SystemExit(1) from exc
        spec_id = "alignment_qc_export_v2" if args.variant == "v2" else "alignment_qc_export_v1"
        spec = next(s for s in CONFIG_SCHEMA_SPECS if s.schema_id == spec_id)
        drift = check_config_schema_drift(specs=[spec])
        if drift:
            for msg in drift:
                print(msg, file=sys.stderr)
            raise SystemExit(1)
        print(f"Schema drift check passed ({spec_id}).")
        return

    out = args.output if args.output is not None else _default_schema_output_path(args.variant)
    if args.variant == "v2":
        write_schema_v2(out)
    else:
        write_schema_v1(out)
    print(f"Wrote schema ({args.variant}): {out}")

    central = _central_schema_path(args.variant)
    if central is not None and central.resolve() != out.resolve():
        central.parent.mkdir(parents=True, exist_ok=True)
        central.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Wrote schema ({args.variant}) central: {central}")


if __name__ == "__main__":
    main()
