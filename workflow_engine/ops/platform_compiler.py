"""Compile DomainPrograms with the packaged platform compiler.

``domain.compiler`` ships in the methyl-gateway wheel (GoliathApp PR #7).
This repository's ``workflow_engine`` tree is often on ``sys.path`` for
``ops`` and ``rest``, which would bind ``domain`` to the product fixture
package. The import below drops that entry for the duration of the compile
so the wheel's package (relative imports, ``domain.program``) is the one
that runs.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any


_REPO_WORKFLOW_ENGINE = Path(__file__).resolve().parents[1]


def _path_is_repo_domain(entry: str) -> bool:
    try:
        resolved = Path(entry).resolve()
    except OSError:
        return False
    return resolved == _REPO_WORKFLOW_ENGINE or resolved == _REPO_WORKFLOW_ENGINE / "domain"


def compile_domain_program_file(
    path: Path,
    *,
    enrich_context: bool = False,
    project_path: str | None = None,
) -> Any:
    """Compile ``path`` with the installed ``domain.compiler``.

    ``project_path`` rewrites the program's ``projectPath`` before lowering.
    """
    saved_path = list(sys.path)
    saved_modules = {
        key: sys.modules.pop(key)
        for key in list(sys.modules)
        if key == "domain" or key.startswith("domain.")
    }
    sys.path = [entry for entry in sys.path if not _path_is_repo_domain(entry)]
    try:
        compiler = importlib.import_module("domain.compiler")
        compiler_file = Path(getattr(compiler, "__file__", "") or "").resolve()
        if _path_is_repo_domain(str(compiler_file.parent)) or compiler_file == (
            _REPO_WORKFLOW_ENGINE / "domain" / "compiler.py"
        ):
            raise ImportError(
                "domain.compiler resolved to this repository, not the methyl-gateway wheel"
            )
        program_path = Path(path)
        if project_path:
            program_mod = importlib.import_module("domain.program")
            data = json.loads(program_path.read_text(encoding="utf-8"))
            data["projectPath"] = str(Path(project_path).expanduser().resolve())
            model = program_mod.DomainProgram.model_validate(data)
            return compiler.compile_domain_program(model, enrich_context=enrich_context)
        return compiler.compile_domain_program_file(
            program_path, enrich_context=enrich_context
        )
    except ImportError as exc:
        raise ImportError(
            "Compiling a DomainProgram requires domain.compiler from the "
            "methyl-gateway wheel (GoliathApp PR #7). Install that package, "
            "or run methyl-workflow-run --compile-only."
        ) from exc
    finally:
        sys.path = saved_path
        for key in list(sys.modules):
            if key == "domain" or key.startswith("domain."):
                sys.modules.pop(key, None)
        sys.modules.update(saved_modules)
