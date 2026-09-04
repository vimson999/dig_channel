#!/usr/bin/env python3
"""Reject background-task app factories that import the full Web API graph."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TASK_ROOT = ROOT / "src" / "backend" / "tasks"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    return parser.parse_args()


def python_files(paths: list[Path]) -> list[Path]:
    files: set[Path] = set()
    for path in paths or [DEFAULT_TASK_ROOT]:
        resolved = path.resolve()
        if resolved.is_dir():
            files.update(candidate for candidate in resolved.rglob("*.py") if candidate.is_file())
        elif resolved.is_file() and resolved.suffix == ".py":
            files.add(resolved)
    return sorted(files)


def is_minimal_create_app_call(node: ast.Call) -> bool:
    if not isinstance(node.func, ast.Name) or node.func.id != "create_app":
        return True
    for keyword in node.keywords:
        if keyword.arg != "register_apis":
            continue
        return isinstance(keyword.value, ast.Constant) and keyword.value.value is False
    return False


def violations(path: Path) -> list[tuple[int, int]]:
    tree = ast.parse(path.read_bytes(), filename=str(path))
    return [
        (node.lineno, node.col_offset + 1)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and not is_minimal_create_app_call(node)
    ]


def main() -> int:
    files = python_files(parse_args().paths)
    failures: list[str] = []
    for path in files:
        for line, column in violations(path):
            try:
                display_path = path.relative_to(ROOT)
            except ValueError:
                display_path = path
            failures.append(
                f"{display_path}:{line}:{column}: create_app must set register_apis=False"
            )
    if failures:
        print("\n".join(failures))
        return 1
    print(f"worker app factory boundary passed: files={len(files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
