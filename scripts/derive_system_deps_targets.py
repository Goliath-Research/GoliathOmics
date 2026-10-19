#!/usr/bin/env python3
"""Derive offline system-deps OS/arch targets from deploy/targets/*.json.

Prints space-separated targets like ``ubuntu-22.04-amd64`` (stdout).
Only the latest target configs matter (no backward-compat for retired images).

Mapping (worker.os overrides when set):
  * goliath-worker / clara-parabricks / *ubuntu2204* / jammy → ubuntu-22.04
  * *ubuntu2404* / noble / ubuntu:24.04 → ubuntu-24.04
  * SSH workers without image/os contribute nothing
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

ARCH_NORM = {"x86_64": "amd64", "aarch64": "arm64", "amd64": "amd64", "arm64": "arm64"}


def os_from_image(image: str) -> str | None:
    img = (image or "").lower()
    if not img:
        return None
    if any(
        x in img
        for x in (
            "goliath-worker",
            "clara-parabricks",
            "ubuntu2204",
            "ubuntu-22.04",
            "ubuntu:22.04",
            "jammy",
        )
    ):
        return "ubuntu-22.04"
    if any(
        x in img
        for x in (
            "ubuntu2404",
            "ubuntu-24.04",
            "ubuntu:24.04",
            "noble",
            "runpod/pytorch",  # current fleet tags are *-ubuntu2404
        )
    ):
        return "ubuntu-24.04"
    return None


def collect(targets_dir: str) -> list[str]:
    seen: list[str] = []
    for path in sorted(glob.glob(os.path.join(targets_dir, "*.json"))):
        if path.endswith((".example.json", ".schema.json")):
            continue
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception as exc:
            print(f"[derive-system-deps] WARNING: skip {path}: {exc}", file=sys.stderr)
            continue
        for w in data.get("workers") or []:
            if not isinstance(w, dict):
                continue
            arch = ARCH_NORM.get(str(w.get("arch") or "amd64"), str(w.get("arch") or "amd64"))
            os_ver = (w.get("os") or "").strip() or None
            if os_ver and not os_ver.startswith("ubuntu-"):
                # allow "22.04" shorthand
                if os_ver.replace(".", "").isdigit():
                    os_ver = f"ubuntu-{os_ver}"
            if not os_ver:
                os_ver = os_from_image(str(w.get("image") or ""))
            if not os_ver:
                continue
            target = f"{os_ver}-{arch}"
            if target not in seen:
                seen.append(target)
    return seen


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--targets-dir",
        default=os.path.join(os.path.dirname(__file__), "..", "deploy", "targets"),
    )
    ap.add_argument(
        "--default",
        default="",
        help="Fallback when no worker images map (empty → exit 1)",
    )
    args = ap.parse_args()
    targets = collect(os.path.abspath(args.targets_dir))
    if not targets:
        if args.default:
            print(args.default)
            return 0
        print(
            "[derive-system-deps] ERROR: no worker OS/arch derived from "
            f"{args.targets_dir}; set worker.os or a known worker image",
            file=sys.stderr,
        )
        return 1
    print(" ".join(targets))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
