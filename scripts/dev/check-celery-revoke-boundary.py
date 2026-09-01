#!/usr/bin/env python3
"""Fail when Celery revoke bypasses the shared DB Session boundary."""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path


HELPER_NAMES = {
    "revoke_celery_task_after_db_commit",
    "revoke_celery_tasks_after_db_commit",
}
REQUIRED_HELPER_KEYWORDS = {"operation", "business_task_id"}


def _runtime_guard_path(backend_root: Path) -> Path:
    return (backend_root / "services" / "runtime_guard.py").resolve()


def _relative_path(path: Path, backend_root: Path) -> str:
    try:
        return path.relative_to(backend_root).as_posix()
    except ValueError:
        return path.as_posix()


def _imported_helper_aliases(tree: ast.AST) -> dict[str, str]:
    aliases = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if not str(node.module or "").endswith("runtime_guard"):
            continue
        for imported in node.names:
            if imported.name in HELPER_NAMES:
                aliases[imported.asname or imported.name] = imported.name
    return aliases


def _called_helper_name(func: ast.expr, aliases: dict[str, str]) -> str | None:
    if isinstance(func, ast.Name):
        return aliases.get(func.id) or (
            func.id if func.id in HELPER_NAMES else None
        )
    if isinstance(func, ast.Attribute) and func.attr in HELPER_NAMES:
        return func.attr
    return None


def _is_direct_control_revoke(node: ast.Attribute) -> bool:
    return (
        node.attr == "revoke"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "control"
    )


def inspect_file(path: Path, backend_root: Path) -> list[str]:
    relative = _relative_path(path, backend_root)
    try:
        tree = ast.parse(path.read_bytes(), filename=str(path))
    except (OSError, SyntaxError) as exc:
        return [f"{relative}:1: unable to inspect Python source: {exc}"]

    if path.resolve() == _runtime_guard_path(backend_root):
        return []

    violations = []
    aliases = _imported_helper_aliases(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and _is_direct_control_revoke(node):
            violations.append(
                f"{relative}:{node.lineno}: direct control.revoke bypasses "
                "the shared DB Session boundary"
            )
            continue
        if not isinstance(node, ast.Call):
            continue
        helper_name = _called_helper_name(node.func, aliases)
        if not helper_name:
            continue
        supplied = {keyword.arg for keyword in node.keywords if keyword.arg}
        missing = sorted(REQUIRED_HELPER_KEYWORDS - supplied)
        if missing:
            violations.append(
                f"{relative}:{node.lineno}: {helper_name} missing keyword(s): "
                f"{', '.join(missing)}"
            )
    return violations


def check_backend(backend_root: Path) -> tuple[list[str], int]:
    backend_root = backend_root.resolve()
    if not backend_root.is_dir():
        return [f"{backend_root}: backend root does not exist"], 0

    paths = sorted(backend_root.rglob("*.py"))
    violations = []
    for path in paths:
        violations.extend(inspect_file(path, backend_root))
    return violations, len(paths)


def main(argv: list[str] | None = None) -> int:
    project_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend-root",
        type=Path,
        default=project_root / "src" / "backend",
    )
    args = parser.parse_args(argv)

    violations, scanned = check_backend(args.backend_root)
    if violations:
        print("Celery revoke boundary contract violations:")
        for violation in violations:
            print(f"- {violation}")
        return 1

    print(
        "OK: Celery revoke boundary contract "
        f"({scanned} Python files inspected)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
