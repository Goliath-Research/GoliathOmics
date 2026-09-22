"""Grok (xAI) advisory review for stability-freeze readiness reports."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_GROK_MODEL = "grok-4.3"
XAI_CHAT_COMPLETIONS_URL = "https://api.x.ai/v1/chat/completions"

_PATH_LIKE = re.compile(r"(?:/|\\)(?:work|home|Users|lambda)[/\\]|^[A-Za-z]:\\", re.I)
# Redact common absolute path prefixes embedded in free-text fields (verdict strings, missing_inputs, etc.).
_PATH_FRAGMENT = re.compile(
    r"(?:/home/[^\s]+|/work/[^\s]+|/lambda/[^\s]+|/Users/[^\s]+|[A-Za-z]:\\[^\s\\]+)",
    re.I,
)


def _scrub_path_fragments_in_obj(obj: Any) -> Any:
    """Deep-copy scrub path-like fragments from every string (for LLM-bound payloads)."""
    if isinstance(obj, str):
        return _PATH_FRAGMENT.sub("[redacted]", obj)
    if isinstance(obj, dict):
        return {k: _scrub_path_fragments_in_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_path_fragments_in_obj(v) for v in obj]
    return obj


def resolve_grok_api_key(
    *,
    explicit_key: Optional[str] = None,
    azure_key_vault_url: Optional[str] = None,
    azure_secret_name: Optional[str] = None,
    methyl_mapper_home: Optional[Path] = None,
    encrypted_file_path: Optional[Path] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Resolve Grok API key using MethylMapper SecureCredentialManager precedence.

    Returns ``(api_key, hint)``. ``hint`` is a short diagnostic when ``api_key`` is missing
    (e.g. decrypt failure, ImportError, or expected path for the encrypted file).
    """
    exp = (explicit_key or "").strip() or None
    try:
        from methyl_mapper.secure_credentials import SecureCredentialManager
    except ImportError:
        return (exp, None if exp else "methyl_mapper is not installed; cannot read ~/.methyl_mapper credentials.")

    kw: Dict[str, Any] = {
        "credential_name": "grok_api_key",
        "env_var_name": "GROK_API_KEY",
        "azure_key_vault_url": azure_key_vault_url,
        "encrypted_file_path": encrypted_file_path.expanduser() if encrypted_file_path else None,
    }
    if azure_secret_name:
        kw["azure_secret_name"] = azure_secret_name
    if methyl_mapper_home is not None:
        kw["methyl_mapper_home"] = methyl_mapper_home
    mgr = SecureCredentialManager(**kw)
    key = mgr.get_credential(explicit_key=exp)
    out = (key or "").strip() or None
    if out:
        return (out, None)

    hint_parts: List[str] = []
    if mgr.last_resolution_error:
        hint_parts.append(mgr.last_resolution_error)
    ep = mgr.encrypted_file_path
    if ep is not None:
        if ep.exists():
            if not hint_parts:
                hint_parts.append(
                    f"Encrypted file exists ({ep}) but no key was returned — check decrypt/password "
                    "(use same host/user as when saving; set METHYL_MAPPER_CREDENTIAL_PASSWORD if used at save time)."
                )
        else:
            hint_parts.append(
                f"No encrypted Grok credential at expected path {ep} "
                "(save with: methyl_mapper_credentials save --credential-type grok --api-key ...)."
            )
    if not mgr.azure_key_vault_url and not os.environ.get("GROK_API_KEY"):
        hint_parts.append("GROK_API_KEY is unset.")
    combined = " ".join(hint_parts) if hint_parts else None
    return (None, combined)


def build_sanitized_ai_payload(
    report: Dict[str, Any],
    *,
    disease_context: Optional[str],
    top_n: int,
) -> Dict[str, Any]:
    """Minimal, path-free payload for external LLM review."""
    v = report.get("verdict") or {}
    stab = report.get("stability") or {}
    fr = report.get("freeze") or {}
    prog = report.get("progression") or {}
    reg = report.get("regulatory") or {}
    traj = dict(prog.get("module_trajectory") or {})
    traj_variant = dict(prog.get("module_trajectory_variant") or {})
    traj_detailed = dict(prog.get("module_trajectory_detailed") or {})
    traj.pop("modules_csv", None)
    traj.pop("error", None)
    traj_variant.pop("modules_csv", None)
    traj_variant.pop("error", None)
    traj_detailed.pop("modules_csv", None)
    traj_detailed.pop("error", None)

    top_trend = list((traj.get("top_by_abs_trend") or [])[:top_n])
    top_trend_variant = list((traj_variant.get("top_by_abs_trend") or [])[:top_n])
    top_trend_detailed = list((traj_detailed.get("top_by_abs_trend") or [])[:top_n])
    track_comparison = {
        "canonical_entities_all_stages": traj.get("entities_all_stages"),
        "variant_entities_all_stages": traj_variant.get("entities_all_stages"),
        "detailed_entities_all_stages": traj_detailed.get("entities_all_stages"),
        "canonical_fraction_monotone_up": traj.get("fraction_monotone_up"),
        "canonical_fraction_monotone_down": traj.get("fraction_monotone_down"),
        "variant_fraction_monotone_up": traj_variant.get("fraction_monotone_up"),
        "variant_fraction_monotone_down": traj_variant.get("fraction_monotone_down"),
        "detailed_fraction_monotone_up": traj_detailed.get("fraction_monotone_up"),
        "detailed_fraction_monotone_down": traj_detailed.get("fraction_monotone_down"),
        "canonical_median_abs_pearson": traj.get("median_abs_pearson_stage_vs_score"),
        "variant_median_abs_pearson": traj_variant.get("median_abs_pearson_stage_vs_score"),
        "detailed_median_abs_pearson": traj_detailed.get("median_abs_pearson_stage_vs_score"),
    }
    labels = prog.get("entity_labels") or {}
    lc = labels.get("label_counts") or {}
    top_labels = dict(list(lc.items())[:top_n]) if isinstance(lc, dict) else {}

    payload: Dict[str, Any] = {
        "task": "readiness_progression_review",
        "disease_context": (disease_context or "").strip() or None,
        "analyte_context": {
            "primary_analyte": reg.get("primary_analyte"),
            "sample_type": reg.get("sample_type"),
            "intended_use_summary": reg.get("intended_use_summary"),
            "fragmentomics": (report.get("fragmentomics") or {}),
        },
        "deterministic_verdict": {
            "overall": v.get("overall"),
            "stability": v.get("stability"),
            "freeze": v.get("freeze"),
            "progression": v.get("progression"),
            "reasons": v.get("reasons") or [],
            "warnings": v.get("warnings") or [],
        },
        "stability_summary": {
            "present": stab.get("present"),
            "dmp_stability": stab.get("dmp_stability"),
            "stable_panel_rows": stab.get("stable_panel_rows"),
        },
        "freeze_summary": {
            "present": fr.get("present"),
            "success": fr.get("success"),
            "merged_panel_rows": fr.get("merged_panel_rows"),
            "timings": fr.get("timings"),
            "removed_detector_keys_in_production_project": fr.get("removed_detector_keys_in_production_project") or [],
        },
        "progression_summary": {
            "ordered_comparison_labels": prog.get("ordered_comparison_labels"),
            "missing_inputs": prog.get("missing_inputs"),
            "row_counts": prog.get("row_counts"),
            "module_trajectory": {
                k: traj[k]
                for k in (
                    "n_rows",
                    "n_unique_entities",
                    "entities_all_stages",
                    "median_abs_pearson_stage_vs_score",
                    "fraction_monotone_up",
                    "fraction_monotone_down",
                )
                if k in traj
            },
            "module_trajectory_variant": {
                k: traj_variant[k]
                for k in (
                    "n_rows",
                    "n_unique_entities",
                    "entities_all_stages",
                    "median_abs_pearson_stage_vs_score",
                    "fraction_monotone_up",
                    "fraction_monotone_down",
                )
                if k in traj_variant
            },
            "module_trajectory_detailed": {
                k: traj_detailed[k]
                for k in (
                    "n_rows",
                    "n_unique_entities",
                    "entities_all_stages",
                    "median_abs_pearson_stage_vs_score",
                    "fraction_monotone_up",
                    "fraction_monotone_down",
                )
                if k in traj_detailed
            },
            "module_top_trends": top_trend,
            "module_top_trends_variant": top_trend_variant,
            "module_top_trends_detailed": top_trend_detailed,
            "module_track_comparison": track_comparison,
            "progression_label_counts_top": top_labels,
            "entity_counts_by_type": labels.get("by_type"),
            "ordered_stage_narratives": prog.get("ordered_stage_narratives") or [],
        },
        "panel_balance": {
            "n_positions": (report.get("panel_balance") or {}).get("n_positions"),
            "max_chrom_share": (report.get("panel_balance") or {}).get("max_chrom_share"),
            "source_filename_only": (report.get("panel_balance") or {}).get("source"),
        },
    }
    return _scrub_path_fragments_in_obj(payload)


def payload_contains_path_like_strings(obj: Any) -> List[str]:
    """Return offending string snippets if any look like host paths (for tests)."""
    bad: List[str] = []

    def walk(x: Any) -> None:
        if isinstance(x, str):
            if _PATH_LIKE.search(x):
                bad.append(x[:120])
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(obj)
    return bad


def redact_report_for_export(report: Dict[str, Any]) -> Dict[str, Any]:
    """Deep copy removing path-heavy fields from exported JSON/markdown source."""
    import copy

    out = copy.deepcopy(report)
    out.pop("project_root", None)
    out.pop("paths", None)
    freeze = out.get("freeze")
    if isinstance(freeze, dict):
        freeze.pop("fixed_dmp_panel_in_project", None)
    prog = out.get("progression")
    if isinstance(prog, dict):
        mt = prog.get("module_trajectory")
        if isinstance(mt, dict):
            mt.pop("modules_csv", None)
        mt_var = prog.get("module_trajectory_variant")
        if isinstance(mt_var, dict):
            mt_var.pop("modules_csv", None)
        mt_det = prog.get("module_trajectory_detailed")
        if isinstance(mt_det, dict):
            mt_det.pop("modules_csv", None)
    return out


def _parse_grok_json_content(content: str) -> Dict[str, Any]:
    text = (content or "").strip()
    if not text:
        return {}
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {"raw_text": text}
    except json.JSONDecodeError:
        return {"narrative_text": text}


def call_grok_chat(
    *,
    api_key: str,
    user_prompt: str,
    system_prompt: str,
    model: str,
    temperature: float,
    timeout_seconds: float,
    max_retries: int,
) -> Dict[str, Any]:
    """POST chat/completions; return dict with keys ok, status_code, content, error."""
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": float(temperature),
            "max_tokens": 2500,
            "stream": False,
        }
    ).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    last_err: Optional[str] = None
    for attempt in range(max(1, int(max_retries))):
        req = urllib.request.Request(XAI_CHAT_COMPLETIONS_URL, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)
            choices = data.get("choices") or []
            if not choices:
                return {"ok": False, "error": "empty_choices", "raw_preview": raw[:500]}
            msg = (choices[0].get("message") or {})
            content = msg.get("content") or ""
            return {"ok": True, "content": content, "model_used": data.get("model")}
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}: {(e.read() or b'').decode('utf-8', errors='replace')[:800]}"
        except urllib.error.URLError as e:
            last_err = f"URL error: {e.reason}"
        except Exception as e:
            last_err = str(e)
        if attempt < max_retries - 1:
            time.sleep(1.5 * (attempt + 1))
    return {"ok": False, "error": last_err or "unknown"}


def run_grok_readiness_review(
    *,
    sanitized_payload: Dict[str, Any],
    api_key: str,
    model: str,
    temperature: float,
    timeout_seconds: float,
    max_retries: int,
    include_raw_response: bool,
    raw_max_chars: int,
) -> Dict[str, Any]:
    """Call Grok with structured instructions; return ai_review block for the report."""
    system_prompt = (
        "You are a biomedical analyst assisting with QA of bioinformatics pipeline readiness reports. "
        "You receive ONLY aggregated metrics and module/pathway progression summaries — no raw patient data "
        "and no file paths. Respond with valid JSON only (no markdown fences). Keys:\n"
        '{"consistency_assessment":"high|mixed|low",'
        '"summary_bullets":["string",...],'
        '"caveats":["string",...],'
        '"suggested_human_checks":["string",...],'
        '"canonical_track_assessment":"short paragraph",'
        '"variant_track_assessment":"short paragraph",'
        '"detailed_track_assessment":"short paragraph",'
        '"track_divergence_assessment":"short paragraph",'
        '"impact_on_confidence":"high|medium|low",'
        '"disease_progression_alignment":"short paragraph"} '
        "Assess whether the progression/module signals appear broadly plausible for the stated disease context "
        "and ordered stages; flag contradictions and uncertainty. "
        "You MUST explicitly compare canonical module trajectory vs variant module trajectory, "
        "and when a detailed cluster-level module trajectory is provided, compare that too. "
        "If tracks diverge, explain why and how that should affect confidence. "
        "Use analyte_context.primary_analyte and analyte_context.sample_type to frame interpretation "
        "(for example, host-response dominant patterns may be plausible for buffy-coat, while tumor-derived "
        "progression specificity may be expected for cfDNA). "
        "When analyte_context.fragmentomics is present, incorporate cohort insert-size / nucleosome-peak "
        "and optional WPS/end-motif summaries; note missing fragmentomics as reduced confidence for cfDNA. "
        "If analyte context is missing, say that confidence is limited by missing analyte framing. "
        "Do not invent unseen statistics."
    )
    user_prompt = (
        "Review this readiness summary JSON and assess biological narrative consistency.\n\n"
        + json.dumps(sanitized_payload, indent=2, default=str)
    )

    result = call_grok_chat(
        api_key=api_key,
        user_prompt=user_prompt,
        system_prompt=system_prompt,
        model=model,
        temperature=temperature,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
    )
    review: Dict[str, Any] = {
        "provider": "xai",
        "model_requested": model,
        "advisory_only": True,
    }
    if not result.get("ok"):
        review["status"] = "error"
        review["error"] = result.get("error")
        return review

    content = str(result.get("content") or "")
    if not content.strip():
        review["status"] = "error"
        review["error"] = "empty_model_response"
        return review

    parsed = _parse_grok_json_content(content)
    review["status"] = "ok"
    review["model_used"] = result.get("model_used")
    review["structured"] = parsed

    def _structured_usable(d: Dict[str, Any]) -> bool:
        ca = (d.get("consistency_assessment") or "").strip()
        bullets = d.get("summary_bullets") if isinstance(d.get("summary_bullets"), list) else []
        narrative = (d.get("narrative_text") or "").strip()
        align = (d.get("disease_progression_alignment") or "").strip()
        canonical_track = (d.get("canonical_track_assessment") or "").strip()
        variant_track = (d.get("variant_track_assessment") or "").strip()
        divergence = (d.get("track_divergence_assessment") or "").strip()
        impact = (d.get("impact_on_confidence") or "").strip()
        return bool(
            ca
            or bullets
            or narrative
            or align
            or canonical_track
            or variant_track
            or divergence
            or impact
        )

    if not _structured_usable(parsed):
        preview = content.strip()[:2000]
        review["response_preview"] = preview
        review["structured_parse_note"] = (
            "Model reply had no expected JSON fields (or JSON keys differ). "
            "Showing truncated reply below; use --include-ai-raw-response for a longer excerpt in JSON."
        )

    if include_raw_response:
        review["raw_response_excerpt"] = content[: max(0, int(raw_max_chars))]
    return review
