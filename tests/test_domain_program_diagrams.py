"""Verify DomainProgram Mermaid diagram catalog generation and drift invariants."""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import generate_domain_program_diagrams as gen  # noqa: E402

_MERMAID_FENCE_RE = re.compile(r"^```mermaid\s*\n(?P<body>.*?)^```\s*$", re.MULTILINE | re.DOTALL)
_SUBGRAPH_OPEN_RE = re.compile(r"^\s*subgraph\s+\S+\s+\[")
_SUBGRAPH_CLOSE_RE = re.compile(r"^\s*end\s*$")


def _assert_valid_mermaid(diagram: str, context: str) -> None:
    lines = [line for line in diagram.splitlines() if line.strip()]
    assert lines and lines[0] == "flowchart TD", f"{context}: missing flowchart TD header"
    depth = 0
    for line in lines[1:]:
        if _SUBGRAPH_OPEN_RE.match(line):
            depth += 1
        elif _SUBGRAPH_CLOSE_RE.match(line):
            depth -= 1
            assert depth >= 0, f"{context}: unmatched 'end'"
        if "classDef " not in line:
            assert ";" not in line, f"{context}: unexpected semicolon in {line!r}"
    assert depth == 0, f"{context}: unclosed subgraph(s), depth={depth}"


def test_every_fixture_is_categorized_and_rendered() -> None:
    fixture_files = sorted(p.name for p in gen.DEFAULT_FIXTURES_DIR.glob("*.program.json"))
    assert len(fixture_files) > 0

    md_files, mmd_files = gen.build_expected_docs()
    assert "other-programs.md" not in md_files, (
        "Every fixture in workflow_engine/domain/fixtures/ should be assigned to a category "
        "in workflow_engine/domain/diagram_categories.json."
    )
    assert len(mmd_files) == len(fixture_files)
    for name, diagram in sorted(mmd_files.items()):
        _assert_valid_mermaid(diagram, name)

    # Verify every fixture appears as a fenced mermaid block in docs/workflows/*.md
    total_fences = 0
    for md_name, content in sorted(md_files.items()):
        if md_name == "index.md":
            for fname in fixture_files:
                assert f"`{fname}`" in content, f"{fname} missing from docs/workflows/index.md"
            continue
        fences = list(_MERMAID_FENCE_RE.finditer(content))
        assert fences, f"{md_name}: expected at least one mermaid block"
        for idx, match in enumerate(fences, start=1):
            _assert_valid_mermaid(match.group("body"), f"{md_name}#{idx}")
        total_fences += len(fences)
    assert total_fences == len(fixture_files)


def test_committed_workflow_docs_match_fixtures() -> None:
    assert gen.main(["--check"]) == 0


def test_platform_and_fallback_renderers_match() -> None:
    md_plat, mmd_plat = gen.build_expected_docs(force_fallback=False)
    md_fb, mmd_fb = gen.build_expected_docs(force_fallback=True)
    assert md_plat == md_fb
    assert mmd_plat == mmd_fb
