"""Every core package is importable, versioned and ships a ``py.typed`` marker."""

import importlib
from pathlib import Path

import pytest

CORE_PACKAGES = [
    "atelier_domain",
    "atelier_plugin_sdk",
    "atelier_plugin_host",
    "atelier_show_engine",
    "atelier_blender_bridge",
    "atelier_cli",
]


@pytest.mark.parametrize("name", CORE_PACKAGES)
def test_core_package_is_importable_and_typed(name: str) -> None:
    module = importlib.import_module(name)

    assert module.__version__
    assert module.__file__ is not None
    assert (Path(module.__file__).parent / "py.typed").is_file()
