"""NavTree seeder stub (R3 replaces portal_clinical_nav-only SQL)."""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "goliath-omics-seed-nav: R2 shell — NavTree MVP lands in R3 "
        "(see docs/refresh-plan.md).",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
