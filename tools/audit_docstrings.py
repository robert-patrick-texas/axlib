"""Audit axlib source modules for the project's educational docstring contract.

Network engineers use this repository as learning material, so automated checks
ensure every module explains its context and every function includes explicit
``Args``, ``Returns``, and ``Raises`` sections.  The script parses Python source
with :mod:`ast`; it never imports modules or accesses credentials.

Dependencies:
    Python standard library only.

Example:
    ``python tools/audit_docstrings.py src``
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Sequence
from pathlib import Path

REQUIRED_SECTIONS = ("Args:", "Returns:", "Raises:")


def python_files(root: Path) -> list[Path]:
    """Return sorted Python files below a source root.

    Args:
        root (pathlib.Path): Source tree to inspect.

    Returns:
        list[pathlib.Path]: Sorted ``.py`` paths.

    Raises:
        ValueError: If ``root`` is not an existing directory.
    """
    if not root.is_dir():
        raise ValueError(f"Source root is not a directory: {root}")
    return sorted(root.rglob("*.py"))


def audit_file(path: Path) -> list[str]:
    """Check one Python file for module and function documentation.

    Args:
        path (pathlib.Path): Python source file.

    Returns:
        list[str]: Human-readable violations; an empty list means success.

    Raises:
        OSError: If the file cannot be read.
        SyntaxError: If the file is not valid Python.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    problems: list[str] = []
    if ast.get_docstring(tree) is None:
        problems.append(f"{path}: missing module docstring")

    # Python silently replaces an earlier function when the same name appears
    # twice in one class or module. Catching that pattern is especially useful in
    # teaching code because otherwise a reader may study dead, misleading logic.
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
                problems.append(
                    f"{path}: duplicate {scope_name}.{name} definitions at "
                    f"lines {line_numbers}"
                )

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        docstring = ast.get_docstring(node)
        if docstring is None:
            problems.append(f"{path}:{node.lineno}: {node.name} missing docstring")
            continue
        for section in REQUIRED_SECTIONS:
            if section not in docstring:
                problems.append(
                    f"{path}:{node.lineno}: {node.name} missing {section} section"
                )
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    """Audit a source tree and print all violations.

    Args:
        argv (Sequence[str] | None): Optional arguments containing one source
            root; defaults to ``src``.

    Returns:
        int: ``0`` when all files pass or ``1`` when violations are found.

    Raises:
        ValueError: If the selected root is not a directory.
        OSError: If a source file cannot be read.
        SyntaxError: If a source file is invalid Python.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    root = Path(arguments[0] if arguments else "src")
    problems = [problem for path in python_files(root) for problem in audit_file(path)]
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(f"Docstring audit passed for {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
