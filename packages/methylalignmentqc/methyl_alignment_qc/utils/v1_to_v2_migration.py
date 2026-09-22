"""
Convert AlignmentQC sample JSON to slim V2.1 guardrail-summary export.

V1 payloads match ExportedSampleQCPayload (internal, may include Picard tables).
Published V2.1 payloads match ExportedSampleQCV2Payload (no histograms).
Fat V2.0.0 files (row-oriented Picard tables) are slimmed in place to 2.1.0.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as pkg_version
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from methyl_alignment_qc.models.sample_qc import ExportedSampleQCPayload
from methyl_alignment_qc.models.sample_qc_v2 import (
    EXPORT_KIND,
    PICARD_TABLE_KEYS,
    SCHEMA_NAME,
    SCHEMA_VERSION,
    ExportedSampleQCV2Payload,
    QCV2Metadata,
    QCV2Producer,
)

from .guardrail_migration import migrate_guardrails_payload


def _producer_package_version() -> str:
    try:
        return pkg_version("methyl_alignment_qc")
    except PackageNotFoundError:
        return "0.1.0"


def is_v2_alignment_qc_payload(payload: Dict[str, Any]) -> bool:
    """Return True if payload has V2 metadata (2.x), fat or slim."""
    if not isinstance(payload, dict):
        return False
    md = payload.get("metadata")
    if not isinstance(md, dict):
        return False
    ver = str(md.get("schema_version", "")).strip()
    if ver.startswith("2."):
        return True
    name = str(md.get("schema_name", "")).strip()
    return name == SCHEMA_NAME and ver.startswith("2")


def is_slim_v2_export(payload: Dict[str, Any]) -> bool:
    """True when the file is already a 2.1+ guardrail summary (no Picard tables)."""
    if not is_v2_alignment_qc_payload(payload):
        return False
    md = payload.get("metadata") or {}
    ver = str(md.get("schema_version", "")).strip()
    if ver.startswith("2.1") or ver.startswith("2.2") or ver.startswith("3."):
        return True
    if str(md.get("export_kind", "")).strip() == EXPORT_KIND:
        return not any(k in payload for k in PICARD_TABLE_KEYS)
    return not any(k in payload for k in PICARD_TABLE_KEYS)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def drop_deamination_aliases(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Remove nested/sidecar copies of deamination_qscore (canonical: details.deamination_qscore)."""
    guard = payload.get("guardrails")
    if isinstance(guard, dict):
        details = guard.get("details")
        if isinstance(details, dict):
            bis = details.get("bisulfite_conversion")
            if isinstance(bis, dict):
                bis.pop("deamination_qscore", None)
                # Legacy heading stored as a single GuardrailMetric instead of nested keys.
                if (
                    "pass" in bis
                    and "conversion_rate_pct" not in bis
                    and "non_cpg_methylation_pct" not in bis
                ):
                    details["bisulfite_conversion"] = {"conversion_rate_pct": bis}
                    bis = details["bisulfite_conversion"]
                else:
                    for stray in (
                        "pass",
                        "value",
                        "message",
                        "normal_range",
                        "threshold",
                        "note",
                    ):
                        bis.pop(stray, None)
                if not bis:
                    details.pop("bisulfite_conversion", None)
    metrics = payload.get("bisulfite_conversion_metrics")
    if isinstance(metrics, dict):
        metrics.pop("deamination_qscore", None)
    return payload


def slim_v2_dict(payload: Dict[str, Any], *, exported_at_utc: str | None = None) -> Dict[str, Any]:
    """Drop Picard tables and deamination aliases from a V2 dict; bump to 2.1.0."""
    out = dict(payload)
    for key in PICARD_TABLE_KEYS:
        out.pop(key, None)
    drop_deamination_aliases(out)
    md = dict(out.get("metadata") or {})
    md["schema_name"] = SCHEMA_NAME
    md["schema_version"] = SCHEMA_VERSION
    md["export_kind"] = EXPORT_KIND
    if exported_at_utc is not None:
        md["exported_at_utc"] = exported_at_utc
    elif not md.get("exported_at_utc"):
        md["exported_at_utc"] = _utc_now_iso()
    producer = md.get("producer")
    if not isinstance(producer, dict):
        md["producer"] = {
            "package": "methyl_alignment_qc",
            "version": _producer_package_version(),
        }
    out["metadata"] = md
    return out


def v1_model_to_v2(
    v1: ExportedSampleQCPayload,
    *,
    exported_at_utc: str | None = None,
) -> ExportedSampleQCV2Payload:
    """Build slim V2.1 payload from a validated V1 assembly (histograms not copied)."""
    ts = exported_at_utc if exported_at_utc is not None else _utc_now_iso()
    raw = v1.model_dump(mode="python", by_alias=True, exclude_none=True)
    drop_deamination_aliases(raw)
    for key in PICARD_TABLE_KEYS:
        raw.pop(key, None)
    raw["metadata"] = QCV2Metadata(
        schema_name=SCHEMA_NAME,
        schema_version=SCHEMA_VERSION,
        export_kind="guardrail_summary",
        exported_at_utc=ts,
        producer=QCV2Producer(package="methyl_alignment_qc", version=_producer_package_version()),
    ).model_dump(mode="python", by_alias=True, exclude_none=True)
    return ExportedSampleQCV2Payload.model_validate(raw)


def v1_dict_to_v2_model(raw: Dict[str, Any]) -> ExportedSampleQCV2Payload:
    """Migrate guardrails if needed, strip aliases, validate as V1, convert to slim V2.1."""
    migrated, _ = migrate_guardrails_payload(raw)
    drop_deamination_aliases(migrated)
    v1 = ExportedSampleQCPayload.model_validate(migrated)
    return v1_model_to_v2(v1)


def v1_dict_to_v2_dict(raw: Dict[str, Any], *, exported_at_utc: str | None = None) -> Dict[str, Any]:
    v2 = v1_dict_to_v2_model(raw)
    if exported_at_utc is not None:
        v2 = v2.model_copy(
            update={
                "metadata": v2.metadata.model_copy(
                    update={"exported_at_utc": exported_at_utc},
                )
            }
        )
    return v2.model_dump(mode="python", by_alias=True, exclude_none=True)


def _to_slim_v2_dict(payload: Dict[str, Any], *, exported_at_utc: str | None = None) -> Dict[str, Any]:
    if is_v2_alignment_qc_payload(payload):
        slim = slim_v2_dict(payload, exported_at_utc=exported_at_utc)
        return ExportedSampleQCV2Payload.model_validate(slim).model_dump(
            mode="python", by_alias=True, exclude_none=True
        )
    return v1_dict_to_v2_dict(payload, exported_at_utc=exported_at_utc)


def _iter_json_files(target: Path, recursive: bool = True) -> Iterable[Path]:
    if target.is_file() and target.suffix.lower() == ".json":
        yield target
        return

    if target.is_dir():
        pattern = "**/*.json" if recursive else "*.json"
        for path in sorted(target.glob(pattern)):
            if path.is_file():
                yield path


def _default_output_path_for_input(path: Path) -> Path:
    return path.with_name(path.stem + ".v2.json")


def convert_file(
    path: Path,
    *,
    apply: bool = False,
    backup: bool = False,
    output_path: Path | None = None,
    validate_v2: bool = True,
) -> Tuple[bool, str]:
    """
    Convert one JSON file from V1 or fat V2.0 to slim V2.1.

    Returns (changed_or_success, status_message).
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as e:
        return False, f"ERROR reading {path}: {e}"

    if is_slim_v2_export(payload):
        return False, f"SKIP {path} (already slim V2.1)"

    try:
        v2_dict = _to_slim_v2_dict(payload)
    except Exception as e:
        return False, f"ERROR converting {path}: {e}"

    if validate_v2:
        try:
            ExportedSampleQCV2Payload.model_validate(v2_dict)
        except Exception as e:
            return False, f"ERROR V2 validation {path}: {e}"

    if not apply:
        return True, f"WOULD CONVERT {path}"

    out = output_path if output_path is not None else _default_output_path_for_input(path)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        if backup and out.exists():
            bak = out.with_suffix(out.suffix + ".bak")
            bak.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(v2_dict, f, indent=2)
    except Exception as e:
        return False, f"ERROR writing {out}: {e}"

    return True, f"CONVERTED {path} -> {out}"


def convert_directory(
    source_dir: Path,
    output_dir: Path,
    *,
    recursive: bool = True,
    apply: bool = False,
    validate_v2: bool = True,
) -> List[Tuple[bool, str]]:
    """Convert all V1 / fat-V2 JSON files under source_dir into output_dir."""
    results: List[Tuple[bool, str]] = []
    for src in _iter_json_files(source_dir, recursive=recursive):
        if not apply:
            try:
                with open(src, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception as e:
                results.append((False, f"ERROR reading {src}: {e}"))
                continue
            if is_slim_v2_export(payload):
                results.append((False, f"SKIP {src} (already slim V2.1)"))
                continue
            try:
                _to_slim_v2_dict(payload)
            except Exception as e:
                results.append((False, f"ERROR converting {src}: {e}"))
                continue
            results.append((True, f"WOULD CONVERT {src} -> {output_dir / (src.stem + '.v2.json')}"))
            continue

        out = output_dir / (src.stem + ".v2.json")
        ok, msg = convert_file(src, apply=True, backup=False, output_path=out, validate_v2=validate_v2)
        results.append((ok, msg))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert AlignmentQC V1 or fat V2.0 JSON files to slim V2.1 guardrail summaries.",
    )
    parser.add_argument("target", type=Path, help="Path to a JSON file or directory of JSON files")
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="When target is a directory, only scan top-level *.json files",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write V2 JSON files. Default is dry-run.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="When target is a single file and --apply is set, write to this path (default: <stem>.v2.json next to input)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="When target is a directory and --apply is set, required: directory for converted *.v2.json files",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="When used with --apply and --output, if the output file exists write a .bak copy first",
    )
    parser.add_argument(
        "--no-validate-v2",
        action="store_true",
        help="Skip validating converted payload against ExportedSampleQCV2Payload",
    )
    args = parser.parse_args()

    target = args.target
    if not target.exists():
        print(f"Error: target does not exist: {target}")
        raise SystemExit(1)

    validate_v2 = not args.no_validate_v2

    if target.is_file():
        if args.output_dir is not None:
            print("Warning: --output-dir is ignored when target is a single file")

        ok, msg = convert_file(
            target,
            apply=args.apply,
            backup=args.backup,
            output_path=args.output,
            validate_v2=validate_v2,
        )
        print(msg)
        raise SystemExit(0 if ok or msg.startswith("SKIP") else 1)

    if args.apply and args.output_dir is None:
        print("Error: --output-dir is required when converting a directory with --apply")
        raise SystemExit(1)

    if not args.apply:
        files = list(_iter_json_files(target, recursive=not args.no_recursive))
        if not files:
            print(f"No JSON files found under: {target}")
            raise SystemExit(1)
        converted = 0
        for src in files:
            try:
                with open(src, "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception as e:
                print(f"ERROR reading {src}: {e}")
                continue
            if is_slim_v2_export(payload):
                print(f"SKIP {src} (already slim V2.1)")
                continue
            try:
                _to_slim_v2_dict(payload)
            except Exception as e:
                print(f"ERROR converting {src}: {e}")
                continue
            print(f"WOULD CONVERT {src}")
            converted += 1
        print(f"\nScanned {len(files)} file(s); {converted} file(s) would be converted (dry-run).")
        raise SystemExit(0)

    out_dir = args.output_dir
    assert out_dir is not None
    results = convert_directory(
        target,
        out_dir,
        recursive=not args.no_recursive,
        apply=True,
        validate_v2=validate_v2,
    )
    ok_count = sum(1 for ok, m in results if ok and m.startswith("CONVERTED"))
    for ok, m in results:
        print(m)
    print(f"\nDone. {ok_count} file(s) written under {out_dir}.")


if __name__ == "__main__":
    main()
