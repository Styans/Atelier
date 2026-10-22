"""Architecture rules, checked statically on the source tree.

1. The core (``packages/*``, ``apps/*``) never imports a plugin package.
2. Core packages only import the core packages below them:
   domain <- plugin_sdk <- plugin_host <- show_engine <- apps.
3. Plugins only use the public core: domain, plugin_sdk and blender_bridge.
"""

from __future__ import annotations

import ast
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

CORE_ALLOWED: dict[str, frozenset[str]] = {
    "atelier_domain": frozenset(),
    "atelier_plugin_sdk": frozenset({"atelier_domain"}),
    "atelier_plugin_host": frozenset({"atelier_domain", "atelier_plugin_sdk"}),
    "atelier_show_engine": frozenset(
        {"atelier_domain", "atelier_plugin_sdk", "atelier_plugin_host"}
    ),
    "atelier_blender_bridge": frozenset({"atelier_domain"}),
    "atelier_jobs": frozenset(
        {"atelier_domain", "atelier_plugin_sdk", "atelier_plugin_host", "atelier_show_engine"}
    ),
    "atelier_api": frozenset(
        {
            "atelier_domain",
            "atelier_plugin_sdk",
            "atelier_plugin_host",
            "atelier_show_engine",
            "atelier_jobs",
        }
    ),
    "atelier_worker": frozenset(
        {
            "atelier_domain",
            "atelier_plugin_sdk",
            "atelier_plugin_host",
            "atelier_show_engine",
            "atelier_jobs",
        }
    ),
    "atelier_cli": frozenset(
        {
            "atelier_domain",
            "atelier_plugin_sdk",
            "atelier_plugin_host",
            "atelier_show_engine",
            "atelier_blender_bridge",
            "atelier_jobs",
            "atelier_worker",
            "atelier_api",
        }
    ),
}
PLUGIN_ALLOWED_CORE = frozenset({"atelier_domain", "atelier_plugin_sdk", "atelier_blender_bridge"})


def imported_roots(source: str, filename: str = "<string>") -> set[str]:
    """Top-level module names a source file imports, including literal dynamic imports."""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and node.args:
            func, arg = node.func, node.args[0]
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if (
                name in {"import_module", "__import__"}
                and isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
            ):
                roots.add(arg.value.split(".")[0])
    return roots


def core_packages() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for src in [*ROOT.glob("packages/*/src"), *ROOT.glob("apps/*/src")]:
        for pkg in src.iterdir():
            if (pkg / "__init__.py").is_file():
                found[pkg.name] = pkg
    return found


def plugin_packages() -> dict[str, Path]:
    """Module names owned by plugins: their ``src/*`` packages and manifest entry modules."""
    found: dict[str, Path] = {}
    for manifest in ROOT.glob("plugins/*/atelier-plugin.toml"):
        plugin_dir = manifest.parent
        for pkg in plugin_dir.glob("src/*"):
            if pkg.is_dir():
                found[pkg.name] = pkg
        entry = tomllib.loads(manifest.read_text(encoding="utf-8")).get("entry", "")
        if entry:
            found.setdefault(entry.split(":")[0].split(".")[0], plugin_dir)
    return found


def python_files(path: Path) -> Iterator[Path]:
    yield from (p for p in path.rglob("*.py") if "__pycache__" not in p.parts)


def test_every_core_package_has_layering_rules() -> None:
    assert set(core_packages()) == set(CORE_ALLOWED)


def test_core_never_imports_plugins() -> None:
    plugins = set(plugin_packages())
    violations = [
        f"{file.relative_to(ROOT)} imports {sorted(hits)}"
        for pkg_dir in core_packages().values()
        for file in python_files(pkg_dir)
        if (hits := imported_roots(file.read_text(encoding="utf-8"), str(file)) & plugins)
    ]
    assert not violations, "\n".join(violations)


def test_core_packages_respect_layering() -> None:
    core = set(CORE_ALLOWED)
    violations = []
    for name, pkg_dir in core_packages().items():
        for file in python_files(pkg_dir):
            roots = imported_roots(file.read_text(encoding="utf-8"), str(file))
            forbidden = (roots & core) - CORE_ALLOWED[name] - {name}
            if forbidden:
                violations.append(f"{file.relative_to(ROOT)} imports {sorted(forbidden)}")
    assert not violations, "\n".join(violations)


def test_plugins_use_only_public_core() -> None:
    """Plugin code (not its tests, which may drive it through the host) uses the public core."""
    private_core = set(CORE_ALLOWED) - PLUGIN_ALLOWED_CORE
    violations = [
        f"{file.relative_to(ROOT)} imports {sorted(hits)}"
        for manifest in ROOT.glob("plugins/*/atelier-plugin.toml")
        for file in python_files(manifest.parent)
        if "tests" not in file.relative_to(manifest.parent).parts
        and (hits := imported_roots(file.read_text(encoding="utf-8"), str(file)) & private_core)
    ]
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import atelier_pointille.plugin", {"atelier_pointille"}),
        ("from atelier_pointille import plugin", {"atelier_pointille"}),
        ("import importlib\nimportlib.import_module('atelier_pointille')", {"atelier_pointille"}),
        ("from . import sibling", set()),
    ],
)
def test_import_scanner_detects_imports(source: str, expected: set[str]) -> None:
    assert expected <= imported_roots(source)
    if not expected:
        assert imported_roots(source) == set()
