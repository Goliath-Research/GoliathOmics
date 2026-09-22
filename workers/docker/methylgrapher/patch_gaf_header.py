#!/usr/bin/env python3
"""Make methylGrapher's GAF reader tolerate vg's GAF header lines.

vg 1.70 `giraffe -o gaf` writes a SAM-style header (`@HD\tVN:Z:1.0`) before the
alignment records. methylGrapher 0.2.0/0.2.1 indexes column 11 of every stdout
line, so the 2-column header aborts Align with:

    File "alignments.py", line 142, in alignment
      if l[i] == "*":
    IndexError: list index out of range

Upstream (twlab/methylGrapher) has not released a fix, so patch the installed
module at image build time. Skipping short lines is safe: GAF alignment records
always carry at least 12 columns.
"""

from __future__ import annotations

import sys
from pathlib import Path

ANCHOR = '''                    # Skip unaligned
                    l = line.strip().split("\\t")
'''

INSERT = '''                    # vg >= 1.65 prefixes GAF output with header lines (@HD ...);
                    # alignment records always have >= 12 columns.
                    if len(l) < 12:
                        continue
'''


def main() -> int:
    candidates = list(Path("/usr/local/lib").glob("python3*/dist-packages/alignments.py"))
    candidates += list(Path("/usr/lib").glob("python3*/dist-packages/alignments.py"))
    if not candidates:
        print("patch_gaf_header: alignments.py not found", file=sys.stderr)
        return 1

    patched = 0
    for path in candidates:
        text = path.read_text(encoding="utf-8")
        if "alignment records always have >= 12 columns" in text:
            print(f"patch_gaf_header: already patched {path}")
            patched += 1
            continue
        if ANCHOR not in text:
            print(f"patch_gaf_header: anchor not found in {path}", file=sys.stderr)
            continue
        path.write_text(text.replace(ANCHOR, ANCHOR + INSERT, 1), encoding="utf-8")
        print(f"patch_gaf_header: patched {path}")
        patched += 1

    if not patched:
        print("patch_gaf_header: no file patched", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
