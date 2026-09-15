# Copyright 2026 Robert Patrick
# SPDX-License-Identifier: Apache-2.0

"""Regression test for the educational docstring contract."""

from __future__ import annotations

import ast
from pathlib import Path


def test_source_functions_have_required_docstring_sections() -> None:
    problems: list[str] = []
    for path in Path("src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if ast.get_docstring(tree) is None:
            problems.append(f"{path}: module docstring")
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            docstring = ast.get_docstring(node) or ""
            problems.extend(
                f"{path}:{node.lineno}:{node.name}:{section}"
                for section in ("Args:", "Returns:", "Raises:")
                if section not in docstring
            )
    assert not problems, "\n".join(problems)


def test_source_scopes_do_not_redefine_functions() -> None:
    """Reject duplicate functions that Python would silently overwrite."""
    problems: list[str] = []
    for path in Path("src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        scopes: list[ast.Module | ast.ClassDef] = [
            tree,
            *[node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)],
        ]
        for scope in scopes:
            definitions: dict[str, list[int]] = {}
            for child in scope.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    definitions.setdefault(child.name, []).append(child.lineno)
            for name, line_numbers in definitions.items():
                if len(line_numbers) > 1:
                    scope_name = getattr(scope, "name", "<module>")
                    problems.append(f"{path}:{scope_name}.{name}:{line_numbers}")
    assert not problems, "\n".join(problems)
