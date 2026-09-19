#!/usr/bin/env python3
"""Reject import cycles between packages.

tach's own ``forbid_circular_dependencies`` checks the dependency graph
*declared* in ``tach.toml`` via ``depends_on``. The generic config declares
none -- that is the whole point of it -- so the setting never sees a cycle that
the real imports form. This reads the real graph from ``tach map`` instead,
collapses it to the package tier, and fails on any cycle it finds.

Like ``tach check``, this needs no per-package configuration: the packages are
whatever the directory listing says they are.

Usage:  python scripts/check_cycles.py     # from the directory holding tach.toml
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path


def _source_roots(config_path: Path) -> list[str]:
    """The source roots tach is configured with, longest first.

    Longest first matters: a file under ``src`` must not be matched by a source
    root of ``.`` before the more specific one is tried.
    """
    with config_path.open("rb") as handle:
        config = tomllib.load(handle)
    roots = config.get("source_roots", ["."])
    return sorted((root.rstrip("/") for root in roots), key=len, reverse=True)


def _package_of(file_path: str, source_roots: list[str]) -> str | None:
    """The package a file belongs to, or None if it belongs to no package.

    A file is in a package when, below its source root, it sits at least one
    directory deep inside the root package -- ``myproject/billing/quote.py``
    yields ``myproject.billing``. Loose modules at the root package level
    (``myproject/app.py``) and the top-level tests belong to no package: they
    are the unconstrained tier, and a cycle is not a thing they can form.
    """
    path = Path(file_path)
    for root in source_roots:
        if root in (".", ""):
            segments = path.parts
        elif path.is_relative_to(root):
            segments = path.relative_to(root).parts
        else:
            continue
        return ".".join(segments[:2]) if len(segments) >= 3 else None
    return None


def _package_graph(
    dependency_map: dict[str, list[str]], source_roots: list[str]
) -> dict[str, set[str]]:
    """Collapse the file-level map tach produced into edges between packages."""
    graph: dict[str, set[str]] = {}
    for importer, imported in dependency_map.items():
        source = _package_of(importer, source_roots)
        if source is None:
            continue
        targets = graph.setdefault(source, set())
        for target_file in imported:
            target = _package_of(target_file, source_roots)
            if target is not None and target != source:
                targets.add(target)
    return graph


def _cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Every group of packages that can reach each other -- Tarjan's SCCs.

    Any strongly connected component with more than one package is a cycle.
    """
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    found: list[list[str]] = []
    counter = 0

    def visit(node: str) -> None:
        nonlocal counter
        index[node] = low[node] = counter
        counter += 1
        stack.append(node)
        on_stack.add(node)
        for neighbour in sorted(graph.get(node, ())):
            if neighbour not in index:
                visit(neighbour)
                low[node] = min(low[node], low[neighbour])
            elif neighbour in on_stack:
                low[node] = min(low[node], index[neighbour])
        if low[node] == index[node]:
            component = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1:
                found.append(sorted(component))

    for node in sorted(graph):
        if node not in index:
            visit(node)
    return found


def main() -> int:
    config_path = Path("tach.toml")
    if not config_path.exists():
        print("No tach.toml here. Run this from the directory that holds it.", file=sys.stderr)
        return 2

    completed = subprocess.run(
        [sys.executable, "-m", "tach", "map", "--output", "-"],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        print(completed.stdout + completed.stderr, file=sys.stderr)
        return completed.returncode

    graph = _package_graph(json.loads(completed.stdout), _source_roots(config_path))
    cycles = _cycles(graph)
    if not cycles:
        print("No import cycles between packages.")
        return 0

    for cycle in cycles:
        print(f"Circular dependency between packages: {' <-> '.join(cycle)}")
    print("\nResolve the cycle, or move the shared code into a package both may depend on.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
